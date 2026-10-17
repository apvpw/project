"""Подсказки из PyPI. Пакеты не скачиваются и не устанавливаются."""

import asyncio
import json
import re
import time
from urllib.parse import quote

import httpx

from backend.dependencies import normalize

MAX_LOOKUPS = 30
MAX_RESPONSE = 2 * 1024 * 1024
CACHE_TTL = 3600
cache: dict[str, tuple[float, dict]] = {}


async def query_package(name: str, client: httpx.AsyncClient) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
        return {"status": "not_found"}
    name = normalize(name)
    cached = cache.get(name)
    if cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        async with client.stream(
            "GET", f"https://pypi.org/pypi/{quote(name, safe='')}/json"
        ) as response:
            if response.status_code == 404:
                result = {"status": "not_found"}
            else:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE:
                        return {"status": "unavailable"}
                info = json.loads(content)["info"]
                package = info["name"]
                if not isinstance(package, str) or normalize(package) != name:
                    return {"status": "unavailable"}
                summary = info.get("summary") or ""
                if not isinstance(summary, str):
                    return {"status": "unavailable"}
                result = {
                    "status": "found",
                    "package": package,
                    "summary": summary[:300],
                    "url": f"https://pypi.org/project/{quote(package, safe='')}/",
                }
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"status": "unavailable"}
    if len(cache) >= 2000:
        cache.clear()
    cache[name] = (time.monotonic(), result)
    return result


async def search_unknowns(dependencies) -> dict:
    unknown = [
        dependency.module
        for dependency in dependencies
        if dependency.kind == "unknown"
        and not dependency.package
        and dependency.lookup_allowed
    ]
    if not unknown:
        return {}
    selected = unknown[:MAX_LOOKUPS]
    semaphore = asyncio.Semaphore(6)
    results = {name: {"status": "skipped"} for name in unknown}
    async with httpx.AsyncClient(
        timeout=3, follow_redirects=False, trust_env=False
    ) as client:

        async def find(name):
            async with semaphore:
                results[name] = await query_package(name, client)

        try:
            async with asyncio.timeout(5):
                await asyncio.gather(*(find(name) for name in selected))
        except TimeoutError:
            for name in selected:
                if results[name]["status"] == "skipped":
                    results[name] = {"status": "unavailable"}
    return results
