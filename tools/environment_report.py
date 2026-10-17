"""Добровольный снимок среды. Запускается только самим владельцем компьютера."""

import json
import platform
from importlib.metadata import distributions, packages_distributions
from pathlib import Path


if __name__ == "__main__":
    packages = {}
    for distribution in distributions():
        name = distribution.metadata.get("Name")
        if name:
            packages[name] = distribution.version
    report = {
        "python_version": platform.python_version(),
        "packages": dict(sorted(packages.items())),
        "imports": dict(sorted(packages_distributions().items())),
    }
    target = Path("environment-report.json")
    if target.exists():
        raise SystemExit(
            "environment-report.json уже существует. Переименуйте его перед созданием нового отчёта."
        )
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Создан {target}. Проверьте JSON перед загрузкой: он содержит версию Python и названия установленных пакетов."
    )
    print("Переменные окружения, секреты, пути и содержимое исходников не собирались.")
