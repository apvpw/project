"""HTTP-слой. Загрузки живут в памяти до 30 минут, без базы и записи исходников на диск."""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from backend.analyzer import analyze
from backend.archive import MAX_UPLOAD, UploadError, read_uploads
from backend.generator import archive_contents, build_zip, generate
from backend.models import Analysis, PackageRequest, Warning
from backend.registry import search_unknowns

ROOT = Path(__file__).resolve().parent.parent
SESSION_TTL = 30 * 60
MAX_SESSIONS = 8
sessions: dict[str, tuple[float, dict[str, bytes], Analysis]] = {}


def cleanup():
    now = time.monotonic()
    for key in list(sessions):
        if now - sessions[key][0] > SESSION_TTL:
            del sessions[key]


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def sweep():
        while True:
            await asyncio.sleep(60)
            cleanup()

    task = asyncio.create_task(sweep())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    sessions.clear()


app = FastAPI(
    title="Перенос",
    description="Статическая подготовка Python-проекта к воспроизведению",
    lifespan=lifespan,
)


class BodyLimit:
    """Ограничиваем и Content-Length, и реально полученное тело multipart."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = MAX_UPLOAD + 2 * 1024 * 1024
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > limit:
            return await Response("Предел запроса — 22 МБ.", status_code=413)(
                scope, receive, send
            )
        size = 0

        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit:
                raise HTTPException(413, "Загрузка превышает допустимый размер.")
            return message

        await self.app(scope, limited_receive, send)


app.add_middleware(BodyLimit)


@app.middleware("http")
async def response_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
async def health():
    cleanup()
    return {"status": "ok", "mode": "static", "ttl_minutes": 30}


async def read_project(files: list[UploadFile]):
    uploads = []
    total = 0
    try:
        if not 1 <= len(files) <= 100:
            raise UploadError("Выберите от 1 до 100 файлов или один ZIP/RAR.")
        for file in files:
            data = await file.read(MAX_UPLOAD + 1)
            total += len(data)
            if total > MAX_UPLOAD:
                raise UploadError("Общий размер загрузки превышает 20 МБ.")
            uploads.append((file.filename or "source.py", data))
        source_files, skipped = await run_in_threadpool(read_uploads, uploads)
        paths = [Path(filename).parts for filename, _ in uploads]
        if all(len(path) > 1 and path[0] == paths[0][0] for path in paths):
            name = paths[0][0]
        else:
            name = Path(uploads[0][0]).stem if len(uploads) == 1 else "python-project"
        return source_files, skipped, name
    finally:
        for file in files:
            await file.close()


@app.post("/api/inspect")
async def inspect_upload(files: list[UploadFile] = File(...)):
    try:
        source_files, skipped, name = await read_project(files)
        return {"name": name, "files": sorted(source_files), "skipped": len(skipped)}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/analyze", response_model=Analysis)
async def analyze_upload(
    files: list[UploadFile] = File(...), environment: UploadFile | None = File(None)
):
    cleanup()
    if len(sessions) >= MAX_SESSIONS:
        raise HTTPException(
            429,
            "Открыто слишком много проектов. Завершите предыдущий анализ или повторите позже.",
        )
    try:
        source_files, skipped, name = await read_project(files)
        environment_data = (
            await environment.read(1024 * 1024 + 1) if environment else None
        )
        if environment_data and len(environment_data) > 1024 * 1024:
            raise UploadError("Отчёт среды должен быть меньше 1 МБ.")
        analysis_id = uuid.uuid4().hex
        result = await run_in_threadpool(
            analyze, source_files, analysis_id, name, environment_data
        )
        if skipped:
            result.warnings.append(
                Warning(
                    code="ignored",
                    level="info",
                    message=f"Служебные файлы и окружения пропущены: {len(skipped)}. Например, .git, .venv и __pycache__.",
                )
            )
        # Слот проверяем повторно после await: параллельные загрузки могли занять память.
        if len(sessions) >= MAX_SESSIONS:
            raise HTTPException(
                429, "Открыто слишком много проектов. Попробуйте позже."
            )
        sessions[analysis_id] = (time.monotonic(), source_files, result)
        return result
    except (UploadError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    finally:
        if environment:
            await environment.close()


def get_session(analysis_id: str):
    cleanup()
    if analysis_id not in sessions:
        raise HTTPException(
            404, "Проект уже удалён из временной памяти. Загрузите файлы повторно."
        )
    _, files, analysis = sessions[analysis_id]
    return files, analysis


@app.get("/api/projects/{analysis_id}/pypi")
async def pypi_suggestions(analysis_id: str):
    _, analysis = get_session(analysis_id)
    private_files = {
        warning.file for warning in analysis.warnings if warning.code == "secret"
    }
    return await search_unknowns(analysis.dependencies, private_files)


@app.get("/api/environment-script")
async def environment_script():
    return FileResponse(
        ROOT / "tools/environment_report.py",
        filename="environment_report.py",
        media_type="text/plain; charset=utf-8",
    )


@app.post("/api/preview")
async def preview(request: PackageRequest):
    files, analysis = get_session(request.analysis_id)
    try:
        generated = await run_in_threadpool(generate, files, analysis, request)
        contents = await run_in_threadpool(archive_contents, files, generated, request)
        return {"files": generated, **contents}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/package")
async def package(request: PackageRequest):
    files, analysis = get_session(request.analysis_id)
    try:
        generated = await run_in_threadpool(generate, files, analysis, request)
        archive = await run_in_threadpool(build_zip, files, generated, request)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return Response(
        archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="python-reproducibility.zip"'
        },
    )


@app.delete("/api/projects/{analysis_id}")
async def delete_project(analysis_id: str):
    sessions.pop(analysis_id, None)
    return {"deleted": True}


DIST = ROOT / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(DIST / "index.html")

    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(DIST / "favicon.svg")
else:

    @app.get("/")
    async def missing_frontend():
        return {
            "message": "Соберите интерфейс: cd frontend, npm install, npm run build. Или откройте Vite на порту 5173."
        }
