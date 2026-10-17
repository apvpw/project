"""Сопоставление импортов пакетам и чтение заявленных зависимостей."""

import json
import re
import tomllib

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from backend.models import Evidence, Warning

# Пакеты с одинаковыми именами импорта и дистрибутива.
PACKAGES = {
    name: name
    for name in (
        "numpy pandas scipy matplotlib seaborn requests httpx fastapi uvicorn flask django "
        "streamlit pydantic sqlalchemy pytest openpyxl torch torchvision torchaudio tensorflow "
        "keras transformers tokenizers datasets accelerate safetensors rich click typer tqdm "
        "aiohttp aiofiles anyio trio jinja2 packaging sympy pygame plotly polars pyarrow "
        "xarray dask distributed numba numexpr statsmodels networkx bokeh altair holoviews "
        "panel dash h5py tables zarr fsspec s3fs gcsfs duckdb rasterio geopandas shapely "
        "pyproj fiona cartopy astropy obspy uncertainties pint lmfit joblib lightgbm xgboost "
        "catboost optuna optax flax jax jaxlib nltk spacy gensim sentencepiece tiktoken "
        "langchain langgraph openai anthropic ollama litellm chromadb faiss gradio chainlit "
        "aiogram telethon discord alembic asyncpg aiomysql aiosqlite psycopg redis pymongo "
        "motor peewee sqlalchemy_utils tortoise celery kombu rq dramatiq apscheduler "
        "starlette quart sanic falcon bottle gunicorn hypercorn waitress websockets websocket "
        "socketio engineio multipart orjson ujson msgpack toml tomlkit attrs attr "
        "cattrs marshmallow dacite dataclasses_json jsonschema pydantic_settings pydantic_core "
        "loguru structlog colorama termcolor tabulate prettytable backoff tenacity cachetools "
        "diskcache wrapt deprecated boltons more_itertools toolz inflection slugify shortuuid "
        "uuid6 ulid pendulum arrow pytz tzdata isodate croniter holidays humanize babel "
        "cryptography bcrypt nacl jose jwt passlib paramiko fabric invoke psutil watchdog "
        "platformdirs appdirs filelock portalocker nats confluent_kafka kafka pika stomp "
        "boto3 botocore s3transfer minio sagemaker mlflow wandb tensorboard tensorboardX "
        "hydra omegaconf dynaconf decouple environs pythonjsonlogger sentry_sdk prometheus_client "
        "selenium playwright scrapy twisted lxml html5lib cssselect pdfplumber "
        "pypdf pdfminer fitz reportlab docx pptx xlrd xlwt xlsxwriter pyxlsb odf imageio "
        "skimage av soundfile librosa pydub mutagen moviepy ffmpeg yt_dlp requests_cache "
        "requests_toolbelt urllib3 certifi charset_normalizer idna chardet faker hypothesis "
        "coverage freezegun responses respx factory model_bakery testcontainers locust "
        "tox nox ruff black isort mypy pre_commit sphinx mkdocs mkdocstrings build wheel "
        "setuptools hatchling poetry pip pyperclip pyautogui qrcode barcode "
        "serial usb bleak pyudev pyqtgraph PyQt5 PyQt6 PySide2 PySide6 wx kivy dearpygui "
        "customtkinter textual prompt_toolkit pygments jedi parso ipykernel jupyterlab "
        "notebook nbformat nbconvert nbclient papermill ipywidgets ipyparallel traitlets tornado "
        "symengine qiskit pennylane cirq qutip brian2 simpy mesa deap SALib control trimesh "
        "open3d laspy netCDF4 cftime pyreadstat pyogrio rapidfuzz fuzzywuzzy Levenshtein"
    ).split()
}

# Отличия от имени импорта и составные namespace-пакеты.
PACKAGES.update(
    {
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
        "pytest_asyncio": "pytest-asyncio",
        "pytest_cov": "pytest-cov",
        "pytest_mock": "pytest-mock",
        "pytest_django": "pytest-django",
        "pytest_httpx": "pytest-httpx",
        "pytest_timeout": "pytest-timeout",
        "pytest_benchmark": "pytest-benchmark",
        "xdist": "pytest-xdist",
        "telebot": "pyTelegramBotAPI",
        "telegram": "python-telegram-bot",
        "discord": "discord.py",
        "websocket": "websocket-client",
        "socketio": "python-socketio",
        "engineio": "python-engineio",
        "multipart": "python-multipart",
        "skimage": "scikit-image",
        "skopt": "scikit-optimize",
        "imblearn": "imbalanced-learn",
        "umap": "umap-learn",
        "faiss": "faiss-cpu",
        "sentence_transformers": "sentence-transformers",
        "langchain_core": "langchain-core",
        "langchain_community": "langchain-community",
        "langchain_openai": "langchain-openai",
        "langchain_text_splitters": "langchain-text-splitters",
        "huggingface_hub": "huggingface-hub",
        "cvxpy": "cvxpy",
        "cvxopt": "cvxopt",
        "tslearn": "tslearn",
        "prophet": "prophet",
        "Bio": "biopython",
        "rdkit": "rdkit",
        "MDAnalysis": "MDAnalysis",
        "SimpleITK": "SimpleITK",
        "nibabel": "nibabel",
        "scvelo": "scvelo",
        "scanpy": "scanpy",
        "anndata": "anndata",
        "sqlalchemy_utils": "SQLAlchemy-Utils",
        "tortoise": "tortoise-orm",
        "dataclasses_json": "dataclasses-json",
        "pydantic_settings": "pydantic-settings",
        "pydantic_core": "pydantic-core",
        "more_itertools": "more-itertools",
        "slugify": "python-slugify",
        "nacl": "PyNaCl",
        "jose": "python-jose",
        "jwt": "PyJWT",
        "confluent_kafka": "confluent-kafka",
        "kafka": "kafka-python",
        "stomp": "stomp.py",
        "nats": "nats-py",
        "hydra": "hydra-core",
        "decouple": "python-decouple",
        "sentry_sdk": "sentry-sdk",
        "prometheus_client": "prometheus-client",
        "opentelemetry.trace": "opentelemetry-api",
        "opentelemetry.metrics": "opentelemetry-api",
        "opentelemetry.sdk": "opentelemetry-sdk",
        "pythonjsonlogger": "python-json-logger",
        "docx": "python-docx",
        "pptx": "python-pptx",
        "pdfminer": "pdfminer.six",
        "fitz": "PyMuPDF",
        "odf": "odfpy",
        "ffmpeg": "ffmpeg-python",
        "yt_dlp": "yt-dlp",
        "requests_cache": "requests-cache",
        "requests_toolbelt": "requests-toolbelt",
        "factory": "factory-boy",
        "model_bakery": "model-bakery",
        "pre_commit": "pre-commit",
        "serial": "pyserial",
        "usb": "pyusb",
        "wx": "wxPython",
        "attr": "attrs",
        "IPython": "ipython",
        "google.protobuf": "protobuf",
        "google.auth": "google-auth",
        "google.cloud.storage": "google-cloud-storage",
        "google.cloud.bigquery": "google-cloud-bigquery",
        "google.cloud.firestore": "google-cloud-firestore",
        "google.cloud.pubsub": "google-cloud-pubsub",
        "googleapiclient": "google-api-python-client",
        "azure.identity": "azure-identity",
        "azure.storage.blob": "azure-storage-blob",
        "azure.cosmos": "azure-cosmos",
        "ruamel.yaml": "ruamel.yaml",
        "poetry.core": "poetry-core",
        "Crypto": "pycryptodome",
        "Cryptodome": "pycryptodomex",
        "OpenSSL": "pyOpenSSL",
        "dns": "dnspython",
        "rest_framework": "djangorestframework",
        "django_filters": "django-filter",
        "corsheaders": "django-cors-headers",
        "allauth": "django-allauth",
        "flask_cors": "Flask-Cors",
        "flask_sqlalchemy": "Flask-SQLAlchemy",
        "sse_starlette": "sse-starlette",
        "win32api": "pywin32",
        "win32con": "pywin32",
        "win32com": "pywin32",
        "win32gui": "pywin32",
        "pywintypes": "pywin32",
    }
)

PACKAGE_ALTERNATIVES = {
    "cv2": (
        "opencv-python",
        "opencv-python-headless",
        "opencv-contrib-python",
        "opencv-contrib-python-headless",
    ),
    "psycopg2": ("psycopg2", "psycopg2-binary"),
    "faiss": ("faiss-cpu", "faiss-gpu"),
    "tensorflow": (
        "tensorflow",
        "tensorflow-cpu",
        "tensorflow-intel",
        "tensorflow-macos",
    ),
}


def known_package(module: str) -> tuple[str, tuple[str, ...]]:
    parts = module.split(".")
    for length in range(len(parts), 0, -1):
        prefix = ".".join(parts[:length])
        if prefix in PACKAGE_ALTERNATIVES:
            return prefix, PACKAGE_ALTERNATIVES[prefix]
        if prefix in PACKAGES:
            return prefix, (PACKAGES[prefix],)
    return parts[0], ()


STACKS = {
    "aiogram": "aiogram",
    "telebot": "Telegram Bot",
    "telegram": "Telegram Bot",
    "telethon": "Telethon",
    "sqlalchemy": "SQLAlchemy",
    "alembic": "Alembic",
    "redis": "Redis",
    "celery": "Celery",
    "pydantic": "Pydantic",
    "transformers": "Transformers",
    "jax": "JAX",
    "lightgbm": "LightGBM",
    "xgboost": "XGBoost",
    "catboost": "CatBoost",
    "geopandas": "GeoPandas",
    "sympy": "SymPy",
    "plotly": "Plotly",
    "polars": "Polars",
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


def read_locked_versions(files, warnings):
    locked = {}
    for path, data in sorted(files.items()):
        basename = path.rsplit("/", 1)[-1].lower()
        if basename not in {"uv.lock", "poetry.lock", "pdm.lock", "pipfile.lock"}:
            continue
        try:
            text = data.decode("utf-8-sig")
            if basename == "pipfile.lock":
                document = json.loads(text)
                sources = {
                    entry["name"]: entry["url"].rstrip("/")
                    for entry in document.get("_meta", {}).get("sources", [])
                }
                packages = []
                for group in ("default", "develop"):
                    for name, item in document.get(group, {}).items():
                        index = item.get("index")
                        separate_index = (
                            sources.get(index) != "https://pypi.org/simple"
                            if index
                            else any(
                                url != "https://pypi.org/simple"
                                for url in sources.values()
                            )
                        )
                        packages.append(
                            {
                                **item,
                                "name": name,
                                "source": {"index": index} if separate_index else {},
                            }
                        )
            else:
                packages = tomllib.loads(text).get("package", [])
            if not isinstance(packages, list):
                raise ValueError("Ожидался список пакетов")
            for item in packages:
                name = item["name"]
                if not isinstance(name, str) or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name
                ):
                    raise ValueError("Недопустимое имя пакета")
                source = item.get("source", {})
                if basename == "uv.lock" and "virtual" in source:
                    continue
                if (
                    any(
                        key in item
                        for key in ("git", "path", "file", "url", "editable")
                    )
                    or (
                        basename == "uv.lock"
                        and source.get("registry", "").rstrip("/")
                        != "https://pypi.org/simple"
                    )
                    or (basename != "uv.lock" and source)
                ):
                    warnings.append(
                        Warning(
                            code="lock_source",
                            file=path,
                            message=f"Для {name} в lock-файле указан отдельный источник. Его нельзя заменить обычной установкой из PyPI; проверьте исходный файл.",
                        )
                    )
                    continue
                version = item["version"]
                if basename == "pipfile.lock":
                    if not isinstance(version, str) or not version.startswith("=="):
                        raise ValueError("Нужна точная версия")
                    version = version[2:]
                if not isinstance(version, str) or len(version) > 80:
                    raise ValueError("Недопустимая версия")
                Version(version)
                locked.setdefault(normalize(name), []).append((version, path))
        except (
            UnicodeDecodeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RecursionError,
        ):
            warnings.append(
                Warning(
                    code="lock_parse",
                    file=path,
                    message="Lock-файл прочитан не полностью. Поддерживаются записи пакетов с точными версиями; проверьте оригинал.",
                )
            )
    return locked


def apply_recorded_versions(dependencies, locked, environment, warnings):
    for dependency in dependencies:
        if dependency.kind != "external" or not dependency.package:
            continue
        requirement = Requirement(dependency.package + dependency.version)
        name = normalize(requirement.name)
        entries = locked.get(name, [])
        versions = {version for version, _ in entries}
        if len(versions) > 1:
            warnings.append(
                Warning(
                    code="lock_ambiguous",
                    file=entries[0][1],
                    message=f"Для {requirement.name} в lock-файлах несколько версий. Они могут относиться к разным средам; версия автоматически не выбрана.",
                )
            )
        elif versions:
            version = next(iter(versions))
            if not requirement.specifier.contains(version, prereleases=True):
                warnings.append(
                    Warning(
                        code="lock_conflict",
                        file=entries[0][1],
                        message=f"Версия {requirement.name}=={version} из lock-файла не соответствует {requirement.specifier}. Ограничение проекта сохранено.",
                    )
                )
            else:
                marker = f"; {requirement.marker}" if requirement.marker else ""
                dependency.version = "==" + version + marker
                dependency.source += f". Точная версия из {entries[0][1]}"
                dependency.evidence.append(
                    Evidence(file=entries[0][1], detail="Зафиксированная версия пакета")
                )

        author_version = environment.get(name)
        if not author_version:
            continue
        current = Requirement(dependency.package + dependency.version)
        if not current.specifier.contains(author_version, prereleases=True):
            warnings.append(
                Warning(
                    code="environment_version_conflict",
                    message=f"У автора установлен {requirement.name}=={author_version}, что не соответствует {current.specifier}. Версия проекта сохранена.",
                )
            )
        elif not entries:
            marker = f"; {current.marker}" if current.marker else ""
            dependency.version = "==" + author_version + marker
            dependency.source += ". Точная версия из отчёта среды автора"


def read_manifests(
    files: dict[str, bytes],
) -> tuple[list[dict], str, str, list[str], list[Warning]]:
    declared: list[dict] = []
    python = ""
    python_source = ""
    conda: list[str] = []
    warnings: list[Warning] = []
    visited: set[tuple[str, bool]] = set()

    def add(text: str, source: str, group: str = "", optional: bool = False):
        try:
            package, version = requirement_parts(text)
            declared.append(
                {
                    "package": package,
                    "version": version,
                    "source": source,
                    "group": group,
                    "optional": optional,
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

    def read_requirements(name: str, depth: int = 0, optional: bool = False):
        from pathlib import PurePosixPath

        key = (name, optional)
        if key in visited or depth > 10:
            return
        visited.add(key)
        for raw in files[name].decode("utf-8-sig").splitlines():
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r ", "--requirement ")):
                target = PurePosixPath(name).parent / line.split(maxsplit=1)[1].strip()
                if ".." not in target.parts and target.as_posix() in files:
                    read_requirements(target.as_posix(), depth + 1, optional)
                else:
                    warnings.append(
                        Warning(
                            code="requirements_include",
                            file=name,
                            message="Подключённый файл зависимостей отсутствует или требует ручной проверки пути.",
                        )
                    )
            else:
                add(line, name, optional=optional)

    for name, data in sorted(files.items()):
        basename = name.rsplit("/", 1)[-1].lower()
        try:
            if basename in {"requirements.txt", "requirements.in"}:
                read_requirements(name)
            elif re.fullmatch(r"requirements[-_.][\w.-]+\.(?:txt|in)", basename) or (
                name.startswith("requirements/") and basename.endswith((".txt", ".in"))
            ):
                read_requirements(name, optional=True)
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
                optional_groups = project.get("optional-dependencies", {})
                dependency_groups = document.get("dependency-groups", {})
                for group, requirements in optional_groups.items():
                    if not isinstance(requirements, list):
                        raise ValueError("Дополнительная группа должна быть списком")
                    for item in requirements:
                        add(str(item), name, f"extra:{group}", optional=True)
                for group, requirements in dependency_groups.items():
                    if not isinstance(requirements, list):
                        raise ValueError("Группа зависимостей должна быть списком")
                    for item in requirements:
                        if isinstance(item, str):
                            add(item, name, f"group:{group}", optional=True)
                        elif isinstance(item, dict) and set(item) == {"include-group"}:
                            # Читаем все группы; вложенная группа уже учтена отдельно.
                            target = normalize(item["include-group"])
                            if target not in {
                                normalize(key) for key in dependency_groups
                            }:
                                raise ValueError("Подключённая группа не найдена")
                        else:
                            raise ValueError("Неизвестная запись группы зависимостей")
                if optional_groups or dependency_groups:
                    warnings.append(
                        Warning(
                            code="optional_dependencies",
                            file=name,
                            level="info",
                            message="Дополнительные группы прочитаны. Из них включены только пакеты, для которых найдены импорты; остальные инструменты разработки не добавляются.",
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
