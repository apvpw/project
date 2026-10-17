"""Сопоставление импортов пакетам и чтение заявленных зависимостей."""

import re
import tomllib

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from backend.models import Warning

# Здесь только известные соответствия, а не догадки по похожему названию.
PACKAGES = {
    "numpy": "numpy",
    "pandas": "pandas",
    "scipy": "scipy",
    "matplotlib": "matplotlib",
    "seaborn": "seaborn",
    "requests": "requests",
    "httpx": "httpx",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "flask": "Flask",
    "django": "Django",
    "streamlit": "streamlit",
    "pydantic": "pydantic",
    "sqlalchemy": "SQLAlchemy",
    "pytest": "pytest",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "openpyxl": "openpyxl",
    "torch": "torch",
    "tensorflow": "tensorflow",
    "transformers": "transformers",
    "rich": "rich",
    "click": "click",
    "tqdm": "tqdm",
    "aiohttp": "aiohttp",
    "jinja2": "Jinja2",
    "packaging": "packaging",
    "sympy": "sympy",
    "pygame": "pygame",
    "plotly": "plotly",
    "polars": "polars",
}

STACKS = {
    "numpy": "NumPy",
    "pandas": "pandas",
    "scipy": "SciPy",
    "matplotlib": "Matplotlib",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "streamlit": "Streamlit",
    "torch": "PyTorch",
    "tensorflow": "TensorFlow",
    "sklearn": "scikit-learn",
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_parts(text: str) -> tuple[str, str]:
    """Разрешаем спецификаторы PyPI, но не URL, пути и pip-опции."""
    text = text.strip()
    if not text or len(text) > 500 or "\n" in text or "\r" in text:
        raise ValueError("Пустое или слишком длинное описание зависимости.")
    try:
        requirement = Requirement(text)
    except InvalidRequirement as error:
        raise ValueError("Нужны имя пакета и допустимое ограничение версии.") from error
    if requirement.url:
        raise ValueError("Зависимость по URL требует ручной проверки.")
    extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
    marker = f"; {requirement.marker}" if requirement.marker else ""
    return requirement.name + extras, str(requirement.specifier) + marker


def read_manifests(
    files: dict[str, bytes],
) -> tuple[list[dict], str, str, list[str], list[Warning]]:
    declared: list[dict] = []
    python = ""
    python_source = ""
    conda: list[str] = []
    warnings: list[Warning] = []
    visited: set[str] = set()

    def add(text: str, source: str, group: str = ""):
        try:
            package, version = requirement_parts(text)
            declared.append(
                {
                    "package": package,
                    "version": version,
                    "source": source,
                    "group": group,
                }
            )
        except ValueError:
            warnings.append(
                Warning(
                    code="manifest_dependency",
                    file=source,
                    message="В файле зависимостей есть URL, путь, pip-опция или неподдерживаемая запись. Проверьте оригинал вручную.",
                )
            )

    def read_requirements(name: str, depth: int = 0):
        from pathlib import PurePosixPath

        if name in visited or depth > 10:
            return
        visited.add(name)
        for raw in files[name].decode("utf-8-sig").splitlines():
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r ", "--requirement ")):
                target = PurePosixPath(name).parent / line.split(maxsplit=1)[1].strip()
                if ".." not in target.parts and target.as_posix() in files:
                    read_requirements(target.as_posix(), depth + 1)
                else:
                    warnings.append(
                        Warning(
                            code="requirements_include",
                            file=name,
                            message="Подключённый файл зависимостей отсутствует или требует ручной проверки пути.",
                        )
                    )
            else:
                add(line, name)

    for name, data in sorted(files.items()):
        basename = name.rsplit("/", 1)[-1].lower()
        try:
            if basename == "requirements.txt":
                read_requirements(name)
            elif basename == "pyproject.toml":
                document = tomllib.loads(data.decode("utf-8-sig"))
                project = document.get("project", {})
                if isinstance(project.get("requires-python"), str):
                    candidate = project["requires-python"]
                    SpecifierSet(candidate)
                    if python and python != candidate:
                        warnings.append(
                            Warning(
                                code="python_conflict",
                                file=name,
                                message="В проекте несколько разных требований к Python. Проверьте их совместимость.",
                            )
                        )
                    else:
                        python, python_source = candidate, name
                for item in project.get("dependencies", []):
                    add(str(item), name)
                if project.get("optional-dependencies"):
                    warnings.append(
                        Warning(
                            code="optional_dependencies",
                            file=name,
                            level="info",
                            message="В pyproject.toml есть дополнительные группы зависимостей. В черновик включена только основная группа.",
                        )
                    )
                if project.get("dynamic"):
                    warnings.append(
                        Warning(
                            code="dynamic_metadata",
                            file=name,
                            message="Часть метаданных pyproject.toml вычисляется динамически. Сборка проекта не выполнялась.",
                        )
                    )
                if document.get("tool", {}).get("poetry"):
                    warnings.append(
                        Warning(
                            code="poetry",
                            file=name,
                            message="Найден раздел Poetry. Для его зависимостей используйте исходный pyproject.toml; автоматически читается стандартный раздел [project].",
                        )
                    )
            elif basename in {"environment.yml", "environment.yaml"}:
                text = data.decode("utf-8-sig")
                if text.count("*") > 50 or text.count("&") > 50:
                    raise ValueError("Слишком сложный YAML")
                document = yaml.safe_load(text) or {}
                for item in document.get("dependencies", []):
                    if isinstance(item, str):
                        conda.append(item)
                        match = re.fullmatch(r"python=(3\.\d+)(?:\.\d+)?", item)
                        if match:
                            minor = int(match[1].split(".")[1])
                            candidate = f">={match[1]},<3.{minor + 1}"
                            if not python:
                                python, python_source = candidate, name
                            elif not SpecifierSet(python).contains(match[1] + ".0"):
                                warnings.append(
                                    Warning(
                                        code="python_conflict",
                                        file=name,
                                        message="Версия Python в Conda отличается от требования pyproject.toml.",
                                    )
                                )
                    elif isinstance(item, dict) and isinstance(item.get("pip"), list):
                        for pip_item in item["pip"]:
                            add(str(pip_item), name, "pip")
                warnings.append(
                    Warning(
                        code="conda",
                        file=name,
                        level="info",
                        message="Conda-зависимости сохранены отдельно: их нельзя автоматически считать пакетами pip.",
                    )
                )
        except (
            UnicodeDecodeError,
            tomllib.TOMLDecodeError,
            yaml.YAMLError,
            ValueError,
            TypeError,
            AttributeError,
            RecursionError,
            InvalidSpecifier,
        ):
            warnings.append(
                Warning(
                    code="manifest_parse",
                    file=name,
                    message="Файл окружения прочитан не полностью: структура или синтаксис не поддерживается. Проверьте оригинал.",
                )
            )
    return declared, python, python_source, conda, warnings
