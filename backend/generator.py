"""Создание текстовых файлов пакета. Никакие команды здесь не выполняются."""

import io
import json
import re
import shlex
import sys
import zipfile

from backend.analyzer import secret_files
from backend.dependencies import normalize, requirement_parts
from backend.models import Analysis, Dependency, PackageOptions


def prepare_options(analysis: Analysis, options: PackageOptions) -> PackageOptions:
    if any(char in options.command for char in "\n\r;|&$<>`\x00"):
        raise ValueError(
            "Укажите одну команду без перенаправлений, подстановок и shell-операторов."
        )
    try:
        shlex.split(options.command)
    except ValueError as error:
        raise ValueError("В команде запуска не закрыты кавычки.") from error
    original = {d.module: d for d in analysis.dependencies}
    if len(options.dependencies) != len(original) or len(
        {d.module for d in options.dependencies}
    ) != len(original):
        raise ValueError("Список зависимостей изменился. Повторите анализ проекта.")
    clean = []
    for dependency in options.dependencies:
        if dependency.module not in original:
            raise ValueError("Получена зависимость, которой не было в анализе.")
        old = original[dependency.module]
        if old.kind in {"external", "unknown"}:
            package, version = dependency.package.strip(), dependency.version.strip()
            if package:
                if not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?", package
                ):
                    raise ValueError(
                        f"Проверьте имя пакета для {old.module}: нужны имя PyPI и, при необходимости, extras в []."
                    )
                if version and re.match(r"^\d", version):
                    version = "==" + version
                package, version = requirement_parts(package + version)
            elif version:
                raise ValueError("Сначала укажите название пакета, затем его версию.")
            source = old.source
            if package != old.package or version != old.version:
                source = f"Уточнено пользователем. Исходное основание: {old.source}"
            clean.append(
                old.model_copy(
                    update={"package": package, "version": version, "source": source}
                )
            )
        else:
            clean.append(old)
    return options.model_copy(update={"dependencies": clean})


def package_sources(files, generated_names, excluded):
    reserved = {name.casefold() for name in generated_names}
    roots = {name.split("/", 1)[0].casefold() for name in files}
    backup = "originals"
    index = 2
    while backup.casefold() in roots or backup.casefold() in reserved:
        backup = f"originals-{index}"
        index += 1

    result = {}
    for name in files:
        if name in excluded:
            continue
        root = name.split("/", 1)[0].casefold()
        result[name] = f"{backup}/{name}" if root in reserved else name
    return result


def file_tree(paths):
    tree = {}
    for path in paths:
        parts = path.split("/")
        branch = tree
        for part in parts[:-1]:
            branch = branch.setdefault(part, {})
        branch[parts[-1]] = None

    def render(branch, prefix=""):
        rows = []
        items = sorted(
            branch.items(), key=lambda item: (item[1] is None, item[0].casefold())
        )
        for index, (name, children) in enumerate(items):
            last = index == len(items) - 1
            connector = "└── " if last else "├── "
            rows.append(
                prefix + connector + name + ("/" if children is not None else "")
            )
            if children is not None:
                rows.extend(render(children, prefix + ("    " if last else "│   ")))
        return rows

    return "\n".join(["."] + render(tree))


def requirements_text(dependencies: list[Dependency]) -> str:
    lines = []
    seen: set[str] = set()
    for dependency in dependencies:
        if dependency.package:
            line = dependency.package + dependency.version
            key = normalize(dependency.package) + dependency.version
            if key not in seen:
                lines.append(line)
                seen.add(key)
    return "\n".join(lines) + ("\n" if lines else "")


def readme_text(analysis, options, sources, generated_names, unresolved) -> str:
    command = options.command or "# Укажите команду запуска после проверки исходников"
    python_note = "Версия Python в исходных файлах не указана."
    if analysis.python_requirement:
        python_note = (
            f"Python: **{analysis.python_requirement}**. {analysis.python_source}"
        )
    text = f"""# {analysis.name}

## Запуск

Распакуйте архив и откройте терминал в этой папке, рядом с README.md.
{python_note}

Создайте виртуальное окружение:

```sh
python -m venv .venv
```

Активируйте его в macOS / Linux:

```sh
source .venv/bin/activate
```

В Windows PowerShell:

```powershell
.venv\\Scripts\\Activate.ps1
```

Установите зависимости и запустите проект:

```sh
python -m pip install -r requirements.txt
{command}
```

Все команды выполняются из папки с этим README. Проверьте аргументы запуска,
переменные окружения и наличие необходимых входных данных.

## Структура проекта

```text
{file_tree(list(sources.values()) + generated_names)}
```

- `requirements.txt` — зависимости для установки через pip.
- `REPORT.md` — источники определения зависимостей и вопросы для проверки.
"""
    renamed = [(old, new) for old, new in sources.items() if old != new]
    if renamed:
        text += "\n## Исходные инструкции и настройки\n\n"
        text += "Для запуска подготовлены новые файлы в корне. Их исходные версии сохранены:\n\n"
        text += "\n".join(f"- `{old}` → `{new}`" for old, new in renamed) + "\n"

    conda_file = next(
        (
            name
            for name in sources
            if name.rsplit("/", 1)[-1].lower()
            in {"environment.yml", "environment.yaml"}
        ),
        "",
    )
    if conda_file:
        text += f"""
## Окружение Conda

Если проект требует Conda, вместо установки через venv создайте окружение по исходному YAML:

```sh
conda env create -f {shlex.quote(sources[conda_file])}
```

Активируйте окружение по имени из YAML. Conda-зависимости не преобразованы в pip автоматически.
"""
    if options.docker:
        text += f"""
## Docker

В корне лежит шаблон Dockerfile с Python {options.python_version}:

```sh
docker build -t reproduced-project .
docker run --rm reproduced-project
```

Проверьте совместимость выбранного Python и настройте системные библиотеки,
порты и оборудование, если они нужны проекту.
"""
    text += """
## Что нужно проверить

Код проанализирован без выполнения. Если версии библиотек не были указаны,
они не закреплены в requirements.txt. Полное восстановление исходной среды
по одному коду не гарантируется. Подробности — в REPORT.md.
"""
    if unresolved:
        text += f"\nНе определены пакеты для {len(unresolved)} импортов. Дополните requirements.txt перед установкой.\n"
    return text


def report_text(analysis, options, dependencies, suspected) -> str:
    report_lines = [
        "# Отчёт о неопределённостях",
        "",
        "## Границы анализа",
        "",
        f"Синтаксический анализ выполнен Python {sys.version.split()[0]}. Загрузка исходников не приводит к их импорту или запуску.",
        "Стандартные модули определяются по каталогу этой версии Python. Для другой версии состав может отличаться.",
        "Внешние зависимости определены по импортам, файлам зависимостей и каталогу соответствий. Точные версии берутся из lock-файлов или предоставленного отчёта среды, если нет конфликтов. Уточнения пользователя учитываются при сборке пакета.",
        "Транзитивные зависимости не разрешались: сохранённые lock-файлы не заменяются полным экспортом окружения. Установка и совместимость требуют отдельной проверки.",
        "Отсутствие предупреждений не доказывает полноту зависимостей и безопасность кода.",
        "",
        "## Зависимости",
        "",
    ]
    for dependency in options.dependencies:
        label = dependency.package or dependency.module
        version = dependency.version or (
            "версия неизвестна"
            if dependency.kind in {"external", "unknown"}
            else "установка через pip не требуется"
        )
        report_lines.append(f"- **{label}** — {version}. {dependency.source}.")
        for evidence in dependency.evidence[:10]:
            report_lines.append(
                f"  - `{evidence.file}:{evidence.line}` — {evidence.detail}"
            )
    report_lines.extend(["", "## Предупреждения", ""])
    for warning in analysis.warnings:
        location = f" (`{warning.file}:{warning.line}`)" if warning.file else ""
        report_lines.append(f"- [{warning.code}] {warning.message}{location}")
    if analysis.environment_summary:
        report_lines.extend(
            ["", "## Предоставленная среда", "", analysis.environment_summary]
        )
        for dependency in dependencies:
            if dependency.environment_version:
                report_lines.append(
                    f"- {dependency.package or dependency.module}: {dependency.environment_version} в предоставленном отчёте. В requirements: {dependency.version or 'не закреплена'}; основание выбора указано выше."
                )
    if analysis.conda_dependencies:
        report_lines.extend(["", "## Исходные Conda-зависимости", ""])
        report_lines.extend(f"- {item}" for item in analysis.conda_dependencies)
    report_lines.extend(["", "## Возможные секреты", ""])
    if suspected:
        action = (
            "Исключён из пакета"
            if options.exclude_suspected_secrets
            else "Оставлен пользователем в пакете"
        )
        report_lines.extend(f"- `{name}` — {action}." for name in sorted(suspected))
        report_lines.append(
            "Если исключённый файл нужен коду, восстановите настройки вручную безопасным способом."
        )
    else:
        report_lines.append(
            "Характерные признаки не найдены. Проверка эвристическая и не гарантирует отсутствия секретов."
        )
    report_lines.extend(
        [
            "",
            "## Проверка результата",
            "",
            "Анализ исходников завершён. Установка зависимостей и запуск проекта сервисом не выполнялись.",
            "После скачивания проверьте пакет в отдельном виртуальном окружении по README и заполните поля ниже.",
            "",
            "- Дата:",
            "- ОС и Python:",
            "- Команда установки:",
            "- Команда запуска:",
            "- Ожидаемый результат:",
            "- Фактический результат:",
            "- Итог проверки запуска: ожидает ручной проверки.",
            "",
        ]
    )
    return "\n".join(report_lines)


def dockerfile_text(options: PackageOptions) -> str:
    cmd = json.dumps(
        shlex.split(options.command) if options.command else ["python"],
        ensure_ascii=False,
    )
    return f"""# Шаблон: проверьте версию Python, системные зависимости и команду.
# Сборка из корня проекта: docker build -t reproduced-project .
FROM python:{options.python_version}-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt
RUN useradd --create-home runner
COPY --chown=runner:runner . /app
USER runner
CMD {cmd}
"""


def generate(
    files: dict[str, bytes], analysis: Analysis, options: PackageOptions
) -> dict[str, str]:
    options = prepare_options(analysis, options)
    suspected = secret_files(files)
    excluded = suspected if options.exclude_suspected_secrets else set()
    dependencies = [
        d for d in options.dependencies if d.kind in {"external", "unknown"}
    ]
    unresolved = [d for d in dependencies if not d.package]
    names = ["README.md", "requirements.txt", "REPORT.md"]
    if options.docker:
        names.append("Dockerfile")
    sources = package_sources(files, names, excluded)

    generated = {
        "README.md": readme_text(analysis, options, sources, names, unresolved),
        "requirements.txt": requirements_text(dependencies),
        "REPORT.md": report_text(analysis, options, dependencies, suspected),
    }
    if options.docker:
        generated["Dockerfile"] = dockerfile_text(options)
    return generated


def archive_contents(files, generated, options):
    excluded = secret_files(files) if options.exclude_suspected_secrets else set()
    sources = package_sources(files, generated, excluded)
    return {
        "archive_files": sorted(list(sources.values()) + list(generated)),
        "preserved_files": {old: new for old, new in sources.items() if old != new},
    }


def build_zip(
    files: dict[str, bytes], generated: dict[str, str], options: PackageOptions
) -> bytes:
    excluded = secret_files(files) if options.exclude_suspected_secrets else set()
    sources = package_sources(files, generated, excluded)
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, target in sources.items():
            archive.writestr(target, files[name])
        for name, content in generated.items():
            archive.writestr(name, content.encode("utf-8"))
    return result.getvalue()
