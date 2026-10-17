"""Статический анализ: только чтение файлов и обход AST, без import/exec."""

import ast
import json
import re
import shlex
import sys
from pathlib import PurePosixPath

from backend.archive import decode_python
from backend.dependencies import STACKS, known_package, normalize, read_manifests
from backend.models import Analysis, Dependency, EntryPoint, Evidence, Warning

DATA_SUFFIXES = {".csv", ".tsv", ".json", ".txt", ".xlsx", ".parquet", ".npy", ".npz"}
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:ghp|github_pat|sk_live|xoxb)[_-][A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"""(?im)^\s*(?:[\w.-]*(?:api[_-]?key|secret|password|access[_-]?token)[\w.-]*)\s*[:=]\s*["']?([A-Za-z0-9_+/=-]{12,})"""
    ),
]


def secret_files(files: dict[str, bytes]) -> set[str]:
    result = set()
    for name, data in files.items():
        basename = PurePosixPath(name).name.lower()
        if (
            basename == ".env"
            or (
                basename.startswith(".env.")
                and not basename.endswith(("example", "sample", "template"))
            )
            or basename in {"id_rsa", "id_ed25519"}
        ):
            result.add(name)
        text = data.decode("utf-8", errors="replace")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            result.add(name)
    return result


def read_environment(
    data: bytes | None,
) -> tuple[dict[str, str], dict[str, list[str]], str]:
    if not data:
        return {}, {}, ""
    try:
        report = json.loads(data)
        packages = report["packages"]
        python = report["python_version"]
        if (
            not isinstance(packages, dict)
            or not isinstance(python, str)
            or len(packages) > 2000
        ):
            raise ValueError
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)", python):
            raise ValueError
        clean = {}
        for name, version in packages.items():
            if (
                not isinstance(name, str)
                or not isinstance(version, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name)
                or not re.fullmatch(r"[A-Za-z0-9.!+_-]{1,80}", version)
            ):
                raise ValueError
            clean[normalize(name)] = version
        imports = report.get("imports", {})
        if not isinstance(imports, dict) or len(imports) > 5000:
            raise ValueError
        for module, distributions in imports.items():
            if (
                not isinstance(module, str)
                or not re.fullmatch(r"[A-Za-z_]\w*", module, flags=re.ASCII)
                or not isinstance(distributions, list)
                or not 1 <= len(distributions) <= 100
            ):
                raise ValueError
            for package in distributions:
                if not isinstance(package, str) or normalize(package) not in clean:
                    raise ValueError
        return (
            clean,
            imports,
            f"Python {python}; пакетов в предоставленном отчёте: {len(clean)}. Совместимость с проектом не проверена.",
        )
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
        raise ValueError(
            "Не удалось прочитать отчёт с версиями. Скачайте скрипт в разделе «Версии с компьютера автора» и загрузите созданный environment-report.json."
        ) from error


def local_module(
    module: str, filename: str, files: dict[str, bytes], level: int = 0
) -> bool:
    path = module.replace(".", "/")
    parent = PurePosixPath(filename).parent
    if level:
        for _ in range(level - 1):
            parent = parent.parent
        roots = [parent]
    else:
        roots = [PurePosixPath("."), PurePosixPath("src")]
        packaged = any(
            (ancestor / "__init__.py").as_posix() in files
            for ancestor in (parent, *parent.parents)
            if ancestor != PurePosixPath(".")
        )
        if not packaged and module.split(".")[0] not in sys.stdlib_module_names:
            roots.append(parent)
    for root in roots:
        candidate = (root / path).as_posix()
        if candidate + ".py" in files or candidate + "/__init__.py" in files:
            return True
        # Namespace-пакеты могут не иметь __init__.py.
        if any(
            name.startswith(candidate + "/") and name.endswith(".py") for name in files
        ):
            return True
    return False


def function_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def inspect_call(node, filename, files, record, warnings):
    function = node.func
    call_name = function_name(function)
    if call_name in {"import_module", "__import__", "exec", "eval"}:
        warnings.append(
            Warning(
                code="dynamic_import",
                file=filename,
                line=node.lineno,
                message="Найден динамический импорт или выполнение строки. Дополнительные зависимости статически могут быть не видны.",
            )
        )
        if (
            call_name in {"import_module", "__import__"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", node.args[0].value):
                record(
                    node.args[0].value,
                    filename,
                    node.lineno,
                    detail="динамический импорт константы",
                )
    if call_name in {
        "open",
        "read_csv",
        "read_excel",
        "read_json",
        "read_parquet",
        "load",
        "loadtxt",
        "read_text",
        "read_bytes",
    }:
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            target = node.args[0].value
            if "://" in target:
                warnings.append(
                    Warning(
                        code="network_data",
                        file=filename,
                        line=node.lineno,
                        message="Код обращается к сетевому ресурсу. Доступность и содержимое ресурса не проверены.",
                    )
                )
            elif PurePosixPath(target).suffix.lower() in DATA_SUFFIXES:
                candidates = {
                    target,
                    (PurePosixPath(filename).parent / target).as_posix(),
                }
                if not candidates.intersection(files):
                    warnings.append(
                        Warning(
                            code="missing_data",
                            file=filename,
                            line=node.lineno,
                            message="В коде есть путь к данным, которых нет в загрузке. Проверьте входные файлы и рабочую папку.",
                        )
                    )
    if call_name in {"getenv", "environ"} or (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Attribute)
        and function.value.attr == "environ"
    ):
        warnings.append(
            Warning(
                code="environment_variable",
                file=filename,
                line=node.lineno,
                message="Код читает переменную окружения. Её значение нужно настроить отдельно.",
            )
        )


def analyze_source(filename, files, record, warnings):
    entries = []
    inferred_minor = 0
    feature_evidence = ""
    data = files[filename]
    if len(data) > 1024 * 1024:
        warnings.append(
            Warning(
                code="large_source",
                file=filename,
                message="Файл больше 1 МБ: синтаксический разбор пропущен. Его зависимости требуют ручной проверки.",
            )
        )
        return [], 0, ""
    try:
        tree = ast.parse(decode_python(data), filename=filename)
    except (
        SyntaxError,
        UnicodeError,
        LookupError,
        ValueError,
        RecursionError,
    ) as error:
        warnings.append(
            Warning(
                code="syntax",
                file=filename,
                line=getattr(error, "lineno", 0) or 0,
                message="Не удалось разобрать Python-файл: проверьте синтаксис, кодировку и версию Python. Файл сохранён в пакете.",
            )
        )
        return [], 0, ""
    guarded = False
    web_apps: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                record(alias.name, filename, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                needs_parent = False
                for alias in node.names:
                    child = f"{node.module}.{alias.name}"
                    prefix, candidates = known_package(child)
                    if (
                        not node.level
                        and candidates
                        and prefix.startswith(node.module + ".")
                    ):
                        record(child, filename, node.lineno, 0, "from")
                    else:
                        needs_parent = True
                if needs_parent:
                    record(node.module, filename, node.lineno, node.level, "from")
            elif node.level:
                for alias in node.names:
                    record(alias.name, filename, node.lineno, node.level, "from")
        elif isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and len(test.comparators) == 1
                and isinstance(test.ops[0], ast.Eq)
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "__main__"
            ):
                guarded = True
            if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            ):
                warnings.append(
                    Warning(
                        code="type_checking",
                        file=filename,
                        line=node.lineno,
                        level="info",
                        message="Есть импорты только для проверки типов. Они показаны в общем списке, но могут не требоваться при запуске.",
                    )
                )
        elif isinstance(node, ast.Try):
            if any(
                isinstance(n, (ast.Import, ast.ImportFrom))
                for part in node.body
                for n in ast.walk(part)
            ):
                warnings.append(
                    Warning(
                        code="optional_import",
                        file=filename,
                        line=node.lineno,
                        message="Импорт внутри try может быть необязательным или иметь альтернативу. Проверьте зависимости этого блока.",
                    )
                )
        elif isinstance(node, ast.Call):
            inspect_call(node, filename, files, record, warnings)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Call):
                constructor = function_name(value.func)
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name) and constructor in {
                        "FastAPI",
                        "Flask",
                    }:
                        web_apps.append((target.id, constructor))
        # Это нижняя граница по нескольким известным конструкциям, а не точная версия.
        feature = ""
        minor = 0
        if isinstance(node, ast.Match):
            minor, feature = 10, "match/case"
        elif isinstance(node, ast.TryStar):
            minor, feature = 11, "except*"
        elif isinstance(node, ast.TypeAlias) or getattr(node, "type_params", []):
            minor, feature = 12, "синтаксис параметров типов"
        if minor > inferred_minor:
            inferred_minor, feature_evidence = (
                minor,
                f"{filename}:{node.lineno}, {feature}",
            )

    module = filename[:-3].replace("/", ".")
    if module.startswith("src."):
        module = module[4:]
    for variable, framework in web_apps:
        if framework == "FastAPI":
            entries.append(
                EntryPoint(
                    command=f"python -m uvicorn {module}:{variable}",
                    file=filename,
                    reason="Найдено создание FastAPI; проверьте путь модуля и наличие uvicorn",
                )
            )
        else:
            entries.append(
                EntryPoint(
                    command=f"python -m flask --app {module}:{variable} run",
                    file=filename,
                    reason="Найдено создание Flask; команда требует подтверждения",
                )
            )
    if guarded:
        # Пакетный запуск сохраняет относительные импорты.
        parent = PurePosixPath(filename).parent
        packaged = (parent / "__init__.py").as_posix() in files or filename.startswith(
            "src/"
        )
        command = (
            f"python -m {module}" if packaged else f"python {shlex.quote(filename)}"
        )
        entries.append(
            EntryPoint(
                command=command,
                file=filename,
                reason="Найден блок if __name__ == '__main__'",
            )
        )

    return entries, inferred_minor, feature_evidence


def analyze(
    files: dict[str, bytes],
    analysis_id: str,
    name: str,
    environment: bytes | None = None,
) -> Analysis:
    declared, python, python_source, conda, warnings = read_manifests(files)
    environment_packages, environment_imports, environment_summary = read_environment(
        environment
    )
    imported: dict[tuple[str, str], Dependency] = {}
    entries: list[EntryPoint] = []
    used_declarations: set[int] = set()
    inferred_minor = 0
    feature_evidence = ""
    stacks: set[str] = set()
    python_files = sorted(name for name in files if name.endswith(".py"))

    def record(
        module: str, file: str, line: int, level: int = 0, detail: str = "import"
    ):
        root = module.split(".")[0]
        label = "." * level + module
        if not root:
            return
        package = version = ""
        lookup_allowed = False
        prefix, candidates = known_package(module)
        local = local_module(module, file, files, level)
        if not level and root in sys.builtin_module_names:
            kind, source = "standard", "Встроенный модуль интерпретатора Python"
        elif local:
            kind, source = "local", "Модуль найден среди загруженных файлов"
            if root in sys.stdlib_module_names and not level:
                warnings.append(
                    Warning(
                        code="shadowing",
                        file=file,
                        line=line,
                        message=f"Локальный модуль {root} может перекрывать стандартную библиотеку. Проверьте путь импорта.",
                    )
                )
        elif not level and root in sys.stdlib_module_names:
            kind, source = (
                "standard",
                "Стандартная библиотека Python (по каталогу анализатора)",
            )
        elif level:
            kind, source = (
                "unknown",
                "Цель относительного импорта не найдена в загрузке",
            )
            warnings.append(
                Warning(
                    code="missing_local",
                    file=file,
                    line=line,
                    message=f"Относительный импорт {label} не удалось разрешить. Возможно, загружена только часть проекта.",
                )
            )
        else:
            environment_names = (
                environment_imports.get(root, []) if prefix == root else []
            )
            names = {normalize(name) for name in candidates + tuple(environment_names)}
            if prefix == root:
                names.add(normalize(root))
            matches = [
                (i, item)
                for i, item in enumerate(declared)
                if normalize(item["package"].split("[")[0]) in names
            ]
            matches.sort(key=lambda pair: pair[1]["optional"])
            distributions = {
                normalize(item["package"].split("[")[0]) for _, item in matches
            }
            if len(distributions) > 1:
                kind, source = (
                    "unknown",
                    "В файлах зависимостей несколько вариантов пакета: "
                    + ", ".join(sorted(distributions)),
                )
                warnings.append(
                    Warning(
                        code="package_variants",
                        file=file,
                        line=line,
                        message=f"Для {module} заявлено несколько вариантов установки. Уточните нужный пакет.",
                    )
                )
            elif matches:
                item = matches[0][1]
                package, version = item["package"], item["version"]
                kind, source = "external", f"Заявлено в {item['source']}"
                if item["group"]:
                    source += f" ({item['group']})"
                used_declarations.update(
                    index
                    for index, declared_item in matches
                    if declared_item["version"] == version
                    and declared_item["package"] == package
                )
                if any(
                    declared_item["version"] != version for _, declared_item in matches
                ):
                    warnings.append(
                        Warning(
                            code="version_conflict",
                            file=item["source"],
                            message=f"Для {package} найдены разные ограничения версии. Основная группа имеет приоритет; проверьте остальные записи.",
                        )
                    )
            elif len(environment_names) == 1:
                package, kind, source = (
                    environment_names[0],
                    "external",
                    "Соответствие из метаданных среды, предоставленной автором",
                )
            elif candidates:
                package, kind, source = (
                    candidates[0],
                    "external",
                    "Известное соответствие модуль → пакет; вариант сборки не проверен",
                )
            else:
                kind, source = "unknown", "Соответствие модуль → пакет неизвестно"
                lookup_allowed = not environment_names
                if environment_names:
                    source = (
                        "В среде автора этот импорт предоставляют несколько пакетов: "
                        + ", ".join(environment_names)
                    )
            stack = STACKS.get(prefix, STACKS.get(root))
            if stack:
                stacks.add(stack)
        if (
            not level
            and root in sys.stdlib_module_names
            and not local
            and local_module(root, file, files, 1)
        ):
            warnings.append(
                Warning(
                    code="shadowing",
                    file=file,
                    line=line,
                    message=f"В папке есть {root}.py или одноимённый пакет. При прямом запуске файла он может перекрыть стандартный {root}; проверьте способ запуска. Установка через pip не нужна.",
                )
            )
        key = (label if level else prefix, kind)
        if key not in imported:
            imported[key] = Dependency(
                module=key[0],
                kind=kind,
                package=package,
                version=version,
                source=source,
                environment_version=environment_packages.get(
                    normalize(package.split("[")[0]), ""
                ),
                lookup_allowed=lookup_allowed,
            )
        imported[key].evidence.append(
            Evidence(file=file, line=line, detail=f"{detail} {label}")
        )

    for filename in python_files:
        file_entries, file_minor, file_evidence = analyze_source(
            filename, files, record, warnings
        )
        entries.extend(file_entries)
        if file_minor > inferred_minor:
            inferred_minor = file_minor
            feature_evidence = file_evidence

    for i, item in enumerate(declared):
        if i not in used_declarations and not item["optional"]:
            imported[(f"declared:{i}", "external")] = Dependency(
                module=f"declared:{i}",
                kind="external",
                package=item["package"],
                version=item["version"],
                source=f"Заявлено в {item['source']}; прямой импорт не установлен",
                evidence=[
                    Evidence(file=item["source"], detail="Запись в файле зависимостей")
                ],
                environment_version=environment_packages.get(
                    normalize(item["package"].split("[")[0]), ""
                ),
            )

    if not entries:
        candidates = [
            f
            for f in python_files
            if PurePosixPath(f).name
            in {"main.py", "app.py", "manage.py", "__main__.py"}
        ]
        if not candidates and len(python_files) == 1:
            candidates = python_files
        for filename in candidates:
            entries.append(
                EntryPoint(
                    command=f"python {shlex.quote(filename)}",
                    file=filename,
                    reason="Кандидат по имени файла; подтвердите команду и аргументы",
                )
            )
    if not entries:
        warnings.append(
            Warning(
                code="entry_point",
                message="Файл запуска не определён. Укажите команду вручную.",
            )
        )
    if inferred_minor:
        warnings.append(
            Warning(
                code="python_syntax",
                level="info",
                message=f"По конструкции {feature_evidence} нужен Python не ниже 3.{inferred_minor}. Это неполная нижняя граница по синтаксису.",
            )
        )
        if not python:
            python, python_source = (
                f">=3.{inferred_minor}",
                "Оценка по синтаксису; совместимость не проверена",
            )
    if not python:
        python_source = (
            "Требование не задано. Точную версию по исходникам определить нельзя."
        )

    basenames = [PurePosixPath(f).name.lower() for f in files]
    checks = {
        "readme": any(n.startswith("readme") for n in basenames),
        "license": any(
            n.startswith(("license", "licence", "copying")) for n in basenames
        ),
        "data": any(
            PurePosixPath(f).suffix.lower() in DATA_SUFFIXES
            and PurePosixPath(f).name.lower()
            not in {"requirements.txt", "environment-report.json"}
            for f in files
        ),
        "manifest": any(
            n
            in {
                "requirements.txt",
                "pyproject.toml",
                "environment.yml",
                "environment.yaml",
            }
            for n in basenames
        ),
    }
    for key, text in {
        "readme": "README отсутствует: будет подготовлена инструкция запуска.",
        "license": "LICENSE отсутствует. Лицензию должен выбрать автор; автоматически она не добавляется.",
        "data": "Примерные данные не найдены по расширениям файлов. Проверьте, нужны ли они для запуска.",
    }.items():
        if not checks[key]:
            warnings.append(Warning(code=f"missing_{key}", message=text, level="info"))
    for filename in sorted(secret_files(files)):
        warnings.append(
            Warning(
                code="secret",
                file=filename,
                level="danger",
                message="Возможный секрет или приватный файл. По умолчанию исключён из ZIP. Значение не отображается; проверьте файл вручную.",
            )
        )
    warnings.append(
        Warning(
            code="limits",
            level="info",
            message="Анализ статический и неполный: плагины, системные библиотеки, сетевые данные и скрытые настройки могут потребовать ручной проверки.",
        )
    )
    if any(d.kind == "external" and not d.version for d in imported.values()):
        warnings.append(
            Warning(
                code="unknown_versions",
                message="Часть версий неизвестна. requirements будет черновиком, а не зафиксированным окружением.",
            )
        )
    if environment_summary:
        warnings.append(
            Warning(
                code="environment_report",
                level="info",
                message="Получен добровольно загруженный отчёт среды. Его версии показаны справочно и не подставлены автоматически.",
            )
        )
    # Один и тот же импорт в разных подпапках может разрешаться по-разному.
    # В таком случае не выбираем внешнюю библиотеку вместо локального файла.
    dependencies: dict[str, Dependency] = {}
    for dependency in imported.values():
        previous = dependencies.get(dependency.module)
        if previous and previous.kind != dependency.kind:
            dependencies[dependency.module] = Dependency(
                module=dependency.module,
                kind="unknown",
                source="Импорт разрешается по-разному в разных файлах; уточните структуру и пути запуска",
                evidence=previous.evidence + dependency.evidence,
            )
            warnings.append(
                Warning(
                    code="ambiguous_local",
                    message=f"Импорт {dependency.module} может быть локальным только для части файлов. Проверьте пути запуска перед выбором пакета.",
                )
            )
        else:
            dependencies[dependency.module] = dependency
    # Повторные совпадения одного признака не превращаем в десятки одинаковых сообщений.
    unique = {(w.code, w.file, w.line, w.message): w for w in warnings}
    return Analysis(
        id=analysis_id,
        name=name,
        files=sorted(files),
        dependencies=sorted(
            dependencies.values(),
            key=lambda d: (
                {"unknown": 0, "external": 1, "local": 2, "standard": 3}[d.kind],
                d.module,
            ),
        ),
        warnings=list(unique.values()),
        entry_points=entries,
        python_requirement=python,
        python_source=python_source,
        stack=sorted(stacks),
        checks=checks,
        conda_dependencies=conda,
        environment_summary=environment_summary,
    )
