"""Проверка только двух наших демонстрационных проектов в отдельных venv."""

import io
import platform
import subprocess
import sys
import tempfile
import venv
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from backend.analyzer import analyze
from backend.archive import read_uploads
from backend.generator import build_zip, generate
from backend.models import PackageOptions

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ("data_analysis", "local_modules")


def run(command, cwd):
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=240
    )
    if result.returncode:
        raise RuntimeError(
            f"Команда завершилась с кодом {result.returncode}: {command}\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout.strip()


def verify(name):
    source = ROOT / "examples" / name
    original = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.name != "result.csv"
    }
    demo_zip = io.BytesIO()
    with zipfile.ZipFile(demo_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in original.items():
            archive.writestr(f"{name}/{path}", content)
    files, _ = read_uploads([(f"{name}.zip", demo_zip.getvalue())])
    result = analyze(files, name, name)
    settings = PackageOptions(
        dependencies=result.dependencies,
        command="python main.py",
        python_version=f"3.{sys.version_info.minor}",
    )
    generated = generate(files, result, settings)
    package = build_zip(files, generated, settings)
    with tempfile.TemporaryDirectory(prefix=f"perenos-{name}-") as temporary:
        folder = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            # В архиве только известные файлы двух наших примеров.
            archive.extractall(folder)
        venv.EnvBuilder(with_pip=True).create(folder / ".venv")
        python = (
            folder
            / ".venv"
            / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        )
        run(
            [str(python), "-m", "pip", "install", "-r", "requirements.txt"],
            folder,
        )
        output = run([str(python), "main.py"], folder)
        frozen = run([str(python), "-m", "pip", "freeze"], folder)
        if name == "data_analysis":
            assert "Выручка: 1500.00" in output, output
            assert "Средняя выручка на строку: 375.00" in output, output
            assert (folder / "result.csv").exists()
        else:
            assert "Всего слов: 19" in output, output
            assert "helpers" not in generated["requirements.txt"]
        return f"""## {name}

Результат: **успешно**, отдельное чистое venv.
Пакет получен через тот же анализатор и генератор, что используются HTTP API.

Установка: `python -m pip install -r requirements.txt`.
Запуск: `python main.py` из корня распакованного пакета.

Вывод:

```text
{output}
```

Установленные зависимости (`pip freeze`):

```text
{frozen or "(внешних пакетов нет)"}
```
"""


if __name__ == "__main__":
    reports = []
    for name in EXAMPLES:
        print(f"Проверяем {name}…", flush=True)
        reports.append(verify(name))
        print(f"{name}: успешно", flush=True)
    report = f"""# Проверка демонстрационных пакетов

Дата (UTC): {datetime.now(timezone.utc).isoformat(timespec="seconds")}.
ОС: {platform.system()} {platform.release()}, {platform.machine()}.
Python: {platform.python_version()}.

Скрипт запускает только два подготовленных нами примера. Сервис не запускает загружаемый код.
Успех этих проверок не гарантирует работу других проектов или совместимость с другими ОС.

""" + "\n".join(reports)
    (ROOT / "tests" / "VERIFICATION.md").write_text(report, encoding="utf-8")
    print("Отчёт: tests/VERIFICATION.md. Временные окружения удалены.")
