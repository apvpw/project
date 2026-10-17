"""Проверяем решения анализатора и содержимое ZIP, а не внутренние вызовы."""

import io
import base64
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from backend.analyzer import analyze
from backend.archive import MAX_FILE, UploadError, read_uploads
from backend.generator import build_zip, generate
from backend.main import app
from backend.models import PackageOptions
from backend.registry import cache, query_package, search_unknowns


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def inspect(files, environment=None):
    return analyze(files, "test-project", "example", environment)


def options(result, **kwargs):
    return PackageOptions(
        dependencies=result.dependencies, command="python main.py", **kwargs
    )


class AnalysisTests(unittest.TestCase):
    def test_logging_in_package_is_not_a_dependency_to_install(self):
        files = {
            "app/__init__.py": b"",
            "app/logging.py": b"import logging\ndef configure(): pass",
            "app/main.py": b"import logging\nfrom app.logging import configure",
            "app/bot/delivery.py": b"import logging",
        }
        result = inspect(files)
        dependencies = {dep.module: dep for dep in result.dependencies}
        self.assertEqual(dependencies["logging"].kind, "standard")
        self.assertEqual(dependencies["app"].kind, "local")
        self.assertTrue(any(w.code == "shadowing" for w in result.warnings))
        requirements = generate(files, result, options(result))["requirements.txt"]
        self.assertNotIn("УТОЧНИТЬ", requirements)

    def test_root_shadow_and_explicit_relative_import_remain_local(self):
        result = inspect({"main.py": b"import logging", "logging.py": b"pass"})
        self.assertEqual(result.dependencies[0].kind, "local")
        result = inspect(
            {
                "app/main.py": b"from .logging import configure",
                "app/logging.py": b"pass",
            }
        )
        self.assertEqual(result.dependencies[0].kind, "local")

    def test_optional_dependencies_supply_versions_only_when_imported(self):
        result = inspect(
            {
                "main.py": b"import aiogram\nimport pytest_asyncio",
                "pyproject.toml": b'[project]\ndependencies=["aiogram>=3"]\n[project.optional-dependencies]\ntest=["pytest-asyncio==0.24.0", "ruff"]\n[dependency-groups]\ndocs=["sphinx"]\n',
            }
        )
        found = {dep.package: dep.version for dep in result.dependencies}
        self.assertEqual(found, {"aiogram": ">=3", "pytest-asyncio": "==0.24.0"})
        self.assertIn("aiogram", result.stack)

    def test_requirement_groups_do_not_hide_main_requirements(self):
        result = inspect(
            {
                "main.py": b"import pytest_asyncio",
                "requirements-dev.txt": b"-r requirements.txt\npytest-asyncio==0.24.0\nruff\n",
                "requirements.txt": b"requests>=2\n",
            }
        )
        found = {dep.package: dep.version for dep in result.dependencies}
        self.assertEqual(found, {"pytest-asyncio": "==0.24.0", "requests": ">=2"})

    def test_declared_opencv_variant_has_priority(self):
        result = inspect(
            {
                "main.py": b"import cv2",
                "requirements.txt": b"opencv-python-headless==4.10.0.84",
            }
        )
        self.assertEqual(len(result.dependencies), 1)
        self.assertEqual(result.dependencies[0].package, "opencv-python-headless")
        self.assertEqual(result.dependencies[0].version, "==4.10.0.84")

    def test_namespace_packages_are_not_merged_by_root(self):
        result = inspect(
            {
                "main.py": b"from google.cloud import storage, bigquery\nfrom google.protobuf import message\nimport azure.storage.blob"
            }
        )
        self.assertEqual(
            {dep.package for dep in result.dependencies},
            {
                "google-cloud-storage",
                "google-cloud-bigquery",
                "protobuf",
                "azure-storage-blob",
            },
        )
        self.assertEqual(len(result.dependencies), 4)

    def test_author_metadata_resolves_a_package_outside_the_catalog(self):
        report = json.dumps(
            {
                "python_version": "3.12.1",
                "packages": {"research-widget-sdk": "1.2.3"},
                "imports": {"research_widget": ["research-widget-sdk"]},
            }
        ).encode()
        result = inspect({"main.py": b"import research_widget"}, report)
        dep = result.dependencies[0]
        self.assertEqual(
            (dep.kind, dep.package, dep.environment_version, dep.version),
            ("external", "research-widget-sdk", "1.2.3", ""),
        )

    def test_author_metadata_recognizes_an_alternative_distribution(self):
        report = json.dumps(
            {
                "python_version": "3.12.1",
                "packages": {"py-cord": "2.6.0"},
                "imports": {"discord": ["py-cord"]},
            }
        ).encode()
        dependency = inspect({"main.py": b"import discord"}, report).dependencies[0]
        self.assertEqual(dependency.package, "py-cord")
        self.assertEqual(dependency.environment_version, "2.6.0")

    def test_namespace_provider_lists_do_not_mix_different_imports(self):
        report = json.dumps(
            {
                "python_version": "3.12.1",
                "packages": {"google-cloud-storage": "2.0.0", "protobuf": "5.0.0"},
                "imports": {"google": ["google-cloud-storage", "protobuf"]},
            }
        ).encode()
        result = inspect(
            {
                "main.py": b"from google.cloud import storage\nimport google.protobuf",
                "requirements.txt": b"google-cloud-storage>=2\nprotobuf>=5",
            },
            report,
        )
        self.assertEqual(
            {dep.package: dep.kind for dep in result.dependencies},
            {"google-cloud-storage": "external", "protobuf": "external"},
        )

    def test_namespace_with_multiple_author_providers_is_not_guessed(self):
        report = json.dumps(
            {
                "python_version": "3.12.1",
                "packages": {"one-sdk": "1.0", "two-sdk": "2.0"},
                "imports": {"research_widget": ["one-sdk", "two-sdk"]},
            }
        ).encode()
        dep = inspect({"main.py": b"import research_widget"}, report).dependencies[0]
        self.assertEqual(dep.kind, "unknown")
        self.assertFalse(dep.lookup_allowed)

    def test_standard_external_and_local_are_separated(self):
        result = inspect(
            {
                "main.py": b"import pathlib\nimport pandas\nfrom helpers import clean",
                "helpers.py": b"def clean(): pass",
            }
        )
        found = {d.module: d for d in result.dependencies}
        self.assertEqual(found["pathlib"].kind, "standard")
        self.assertEqual(found["helpers"].kind, "local")
        self.assertEqual(found["pandas"].package, "pandas")
        self.assertEqual(found["pandas"].version, "")
        self.assertEqual(found["pandas"].evidence[0].line, 2)

    def test_import_name_is_mapped_to_distribution(self):
        result = inspect(
            {"main.py": b"from PIL import Image\nimport sklearn\nimport cv2"}
        )
        self.assertEqual(
            {d.package for d in result.dependencies},
            {"Pillow", "scikit-learn", "opencv-python"},
        )

    def test_unknown_name_is_not_guessed(self):
        result = inspect({"main.py": b"import internal_widget"})
        self.assertEqual(result.dependencies[0].kind, "unknown")
        self.assertEqual(result.dependencies[0].package, "")

    def test_context_dependent_local_import_stays_ambiguous(self):
        files = {
            "first/main.py": b"import helpers",
            "first/helpers.py": b"pass",
            "second/main.py": b"import helpers",
        }
        result = inspect(files)
        self.assertEqual(len(result.dependencies), 1)
        self.assertEqual(result.dependencies[0].kind, "unknown")
        self.assertEqual(len(result.dependencies[0].evidence), 2)
        self.assertIn(
            "УТОЧНИТЬ",
            generate(files, result, options(result))["requirements.txt"],
        )

    def test_relative_import_and_src_layout(self):
        result = inspect(
            {
                "src/app/__init__.py": b"",
                "src/app/main.py": b"from .helpers import clean\nimport app.helpers",
                "src/app/helpers.py": b"def clean(): pass",
            }
        )
        self.assertTrue(all(d.kind == "local" for d in result.dependencies))

    def test_missing_relative_import_is_not_a_pypi_package(self):
        result = inspect({"app/main.py": b"from .helpers import clean"})
        self.assertEqual(result.dependencies[0].kind, "unknown")
        self.assertTrue(any(w.code == "missing_local" for w in result.warnings))

    def test_dynamic_import_is_reported_and_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"
            code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n__import__(name)"
            result = inspect({"main.py": code.encode()})
            self.assertFalse(marker.exists())
            self.assertTrue(any(w.code == "dynamic_import" for w in result.warnings))

    def test_declared_versions_and_additional_dependencies_are_preserved(self):
        result = inspect(
            {
                "main.py": b"import pandas",
                "requirements.txt": b"pandas==2.2.3\nopenpyxl>=3",
            }
        )
        found = {d.package: d.version for d in result.dependencies}
        self.assertEqual(found, {"pandas": "==2.2.3", "openpyxl": ">=3"})

    def test_pep621_python_and_dependencies(self):
        result = inspect(
            {
                "main.py": b"import requests",
                "pyproject.toml": b'[project]\nname="demo"\nrequires-python=">=3.11"\ndependencies=["requests>=2.31"]',
            }
        )
        self.assertEqual(result.python_requirement, ">=3.11")
        self.assertEqual(result.dependencies[0].version, ">=2.31")

    def test_conda_packages_are_not_silently_converted_to_pip(self):
        result = inspect(
            {
                "main.py": b"import numpy",
                "environment.yml": b"name: demo\ndependencies:\n  - python=3.11\n  - numpy=1.26\n  - pip:\n      - requests==2.32.3\n",
            }
        )
        self.assertIn("numpy=1.26", result.conda_dependencies)
        self.assertEqual(
            next(d for d in result.dependencies if d.package == "numpy").version, ""
        )
        self.assertEqual(
            next(d for d in result.dependencies if d.package == "requests").version,
            "==2.32.3",
        )

    def test_invalid_manifest_and_syntax_return_warnings(self):
        result = inspect({"main.py": b"def broken(", "pyproject.toml": b"[project"})
        self.assertTrue(
            {"syntax", "manifest_parse"}.issubset({w.code for w in result.warnings})
        )

    def test_syntax_lower_bound_is_explained(self):
        result = inspect({"main.py": b"match value:\n    case 1: pass"})
        self.assertEqual(result.python_requirement, ">=3.10")
        self.assertIn("синтаксис", result.python_source)

    def test_environment_report_is_evidence_not_a_pin(self):
        environment = json.dumps(
            {"python_version": "3.12.1", "packages": {"pandas": "2.2.3"}}
        ).encode()
        dependency = inspect({"main.py": b"import pandas"}, environment).dependencies[0]
        self.assertEqual(dependency.environment_version, "2.2.3")
        self.assertEqual(dependency.version, "")

    def test_missing_data_is_reported(self):
        result = inspect({"main.py": b"import pandas as pd\npd.read_csv('absent.csv')"})
        self.assertTrue(any(w.code == "missing_data" for w in result.warnings))


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache.clear()

    async def test_found_package_is_a_suggestion_not_an_install_requirement(self):
        calls = []

        def respond(request):
            calls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "info": {
                        "name": "emoji",
                        "summary": "Emoji for Python",
                        "version": "999.0",
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            suggestion = await query_package("emoji", client)
            await query_package("emoji", client)
        self.assertEqual(suggestion["package"], "emoji")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], "https://pypi.org/pypi/emoji/json")
        files = {"main.py": b"import emoji"}
        result = inspect(files)
        self.assertEqual(result.dependencies[0].package, "")
        requirements = generate(files, result, options(result))["requirements.txt"]
        self.assertIn("УТОЧНИТЬ", requirements)
        self.assertNotIn("999.0", requirements)

    async def test_unknown_package_and_network_failure_are_distinct(self):
        def respond(request):
            if request.url.path.endswith("/missing/json"):
                return httpx.Response(404)
            raise httpx.ConnectError("offline")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(
                await query_package("missing", client), {"status": "not_found"}
            )
            self.assertEqual(
                await query_package("offline", client), {"status": "unavailable"}
            )

    async def test_invalid_names_and_foreign_metadata_are_not_trusted(self):
        def respond(request):
            return httpx.Response(
                200, json={"info": {"name": "different-package", "summary": ""}}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(
                await query_package("../private", client), {"status": "not_found"}
            )
            self.assertEqual(
                await query_package("example", client), {"status": "unavailable"}
            )

    async def test_local_ambiguity_is_not_sent_to_pypi(self):
        result = inspect(
            {
                "first/main.py": b"import helpers",
                "first/helpers.py": b"pass",
                "second/main.py": b"import helpers",
            }
        )
        self.assertEqual(await search_unknowns(result.dependencies), {})


class ArchiveTests(unittest.TestCase):
    def test_rar_keeps_python_and_data(self):
        # Небольшой RAR с main.py и data/sample.csv, без сжатия.
        source = base64.b64decode(
            "UmFyIRoHAM+QcwAADQAAAAAAAACUCXQAgCwACQAAAAkAAAADBh7hPAAAAAAUMAwApIEAAGRlbW8v"
            "bWFpbi5weXByaW50KDEpCjikdACANAAEAAAABAAAAAMVPNCkAAAAABQwFACkgQAAZGVtby9kYXRh"
            "L3NhbXBsZS5jc3Z4CjEKxD17AEAHAA=="
        )
        files, skipped = read_uploads([("project.rar", source)])
        self.assertEqual(
            files, {"main.py": b"print(1)\n", "data/sample.csv": b"x\n1\n"}
        )
        self.assertEqual(skipped, [])

    def test_bad_rar_and_rar_traversal_are_rejected(self):
        traversal = base64.b64decode(
            "UmFyIRoHAM+QcwAADQAAAAAAAAAUTXQAgCoABAAAAAQAAAADJNRwzgAAAAAUMAoApIEAAC4uL21haW4ucHlwYXNzxD17AEAHAA=="
        )
        for data in (b"not a rar", traversal):
            with self.subTest(data=data), self.assertRaises(UploadError):
                read_uploads([("project.rar", data)])

    def test_media_alone_is_rejected_but_project_media_are_preserved(self):
        image = b"\x89PNG\r\n\x1a\n\0example"
        music = b"ID3\0example"
        with self.assertRaises(UploadError):
            read_uploads([("picture.png", image), ("sound.mp3", music)])
        files, _ = read_uploads(
            [
                ("demo/main.py", b"pass"),
                ("demo/assets/picture.png", image),
                ("demo/assets/sound.mp3", music),
            ]
        )
        self.assertEqual(files["assets/picture.png"], image)
        self.assertEqual(files["assets/sound.mp3"], music)

    def test_renamed_binary_is_rejected_and_syntax_errors_are_allowed(self):
        with self.assertRaises(UploadError):
            read_uploads([("main.py", b"ID3\0example")])
        files, _ = read_uploads([("main.py", b"def broken(")])
        self.assertEqual(files["main.py"], b"def broken(")
        source = b"# coding: cp1251\n# \xcf\xf0\xe8\xe2\xe5\xf2\nprint(1)"
        files, _ = read_uploads([("main.py", source)])
        self.assertEqual(files["main.py"], source)

    def test_only_the_outer_folder_is_removed(self):
        files, _ = read_uploads([("demo/src/main.py", b"pass")])
        self.assertEqual(files, {"src/main.py": b"pass"})

    def test_outer_folder_is_removed_and_nested_files_preserved(self):
        files, _ = read_uploads(
            [
                (
                    "demo.zip",
                    zip_bytes(
                        {
                            "demo/main.py": "import helper",
                            "demo/data/sample.csv": "x\n1",
                        }
                    ),
                )
            ]
        )
        self.assertEqual(set(files), {"main.py", "data/sample.csv"})

    def test_traversal_and_absolute_paths_are_rejected(self):
        for name in ["../main.py", "/tmp/main.py", "C:/main.py", "folder\\main.py"]:
            with self.subTest(name=name), self.assertRaises(UploadError):
                read_uploads([("bad.zip", zip_bytes({name: "print(1)"}))])

    def test_symlink_is_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            item = zipfile.ZipInfo("main.py")
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, "/etc/passwd")
        with self.assertRaises(UploadError):
            read_uploads([("bad.zip", output.getvalue())])

    def test_duplicate_names_and_bad_zip_are_rejected(self):
        with self.assertRaises(UploadError):
            read_uploads([("main.py", b""), ("main.py", b"pass")])
        with self.assertRaises(UploadError):
            read_uploads([("bad.zip", b"not a zip")])

    def test_oversized_expanded_file_is_rejected(self):
        with self.assertRaises(UploadError):
            read_uploads([("bad.zip", zip_bytes({"main.py": b"#" * (MAX_FILE + 1)}))])

    def test_environments_are_skipped(self):
        files, skipped = read_uploads(
            [("demo.zip", zip_bytes({"main.py": "pass", ".venv/lib.py": "pass"}))]
        )
        self.assertEqual(set(files), {"main.py"})
        self.assertEqual(skipped, [".venv/lib.py"])

    def test_file_and_directory_cannot_share_a_path(self):
        with self.assertRaises(UploadError):
            read_uploads(
                [("main.py", b"pass"), ("data", b"x"), ("data/sample.csv", b"x")]
            )


class GenerationTests(unittest.TestCase):
    def test_originals_preserved_and_secret_omitted(self):
        files = {
            "main.py": b"pass",
            "README.md": b"original",
            ".env": b"PASSWORD=not-a-real-secret",
            "data/sample.csv": b"x\n1",
        }
        result = inspect(files)
        settings = options(result)
        generated = generate(files, result, settings)
        with zipfile.ZipFile(
            io.BytesIO(build_zip(files, generated, settings))
        ) as archive:
            self.assertEqual(archive.read("originals/README.md"), b"original")
            self.assertIn("python main.py", archive.read("README.md").decode())
            self.assertNotIn(".env", archive.namelist())
            self.assertIn("data/sample.csv", archive.namelist())
            self.assertIn("Dockerfile", archive.namelist())
            self.assertNotIn("not-a-real-secret", archive.read("REPORT.md").decode())

    def test_existing_metadata_is_backed_up_without_overwriting_other_files(self):
        files = {
            "main.py": b"pass",
            "README.md": b"original readme",
            "requirements.txt": b"requests==2.32.3",
            "Dockerfile": b"original dockerfile",
            "REPORT.md": b"original report",
            "originals/notes.txt": b"keep this too",
        }
        result = inspect(files)
        settings = options(result)
        generated = generate(files, result, settings)
        with zipfile.ZipFile(
            io.BytesIO(build_zip(files, generated, settings))
        ) as archive:
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            for name in ("README.md", "requirements.txt", "Dockerfile", "REPORT.md"):
                self.assertEqual(archive.read("originals-2/" + name), files[name])
                self.assertEqual(archive.read(name).decode(), generated[name])
            self.assertEqual(archive.read("originals/notes.txt"), b"keep this too")
            self.assertIn(
                "pip install -r requirements.txt", archive.read("README.md").decode()
            )

    def test_package_has_source_data_and_instructions_in_root(self):
        files = {"main.py": b"pass", "data/sample.csv": b"x\n1"}
        result = inspect(files)
        settings = options(result)
        generated = generate(files, result, settings)
        with zipfile.ZipFile(
            io.BytesIO(build_zip(files, generated, settings))
        ) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "main.py",
                    "data/sample.csv",
                    "README.md",
                    "requirements.txt",
                    "REPORT.md",
                    "Dockerfile",
                },
            )
            self.assertIn("COPY requirements.txt", archive.read("Dockerfile").decode())

    def test_readme_contains_a_folder_tree(self):
        files = {"main.py": b"pass", "data/input.csv": b"x", "utils/helper.py": b"pass"}
        result = inspect(files)
        readme = generate(files, result, options(result))["README.md"]
        self.assertIn("├── data/", readme)
        self.assertIn("input.csv", readme)
        self.assertIn("helper.py", readme)

    def test_unknown_dependency_is_a_comment_and_local_is_not_installed(self):
        files = {"main.py": b"import mystery\nimport helpers", "helpers.py": b"pass"}
        result = inspect(files)
        requirements = generate(files, result, options(result))["requirements.txt"]
        install_lines = [
            line
            for line in requirements.splitlines()
            if line and not line.startswith("#")
        ]
        self.assertEqual(install_lines, [])
        self.assertIn("mystery", requirements)
        self.assertNotIn("helpers", requirements)

    def test_manual_package_and_version_are_used(self):
        files = {"main.py": b"import mystery"}
        result = inspect(files)
        settings = options(result)
        settings.dependencies[0] = settings.dependencies[0].model_copy(
            update={"package": "example-package", "version": "1.2.3"}
        )
        self.assertIn(
            "example-package==1.2.3",
            generate(files, result, settings)["requirements.txt"],
        )

    def test_requirement_and_shell_injection_are_rejected(self):
        files = {"main.py": b"import pandas"}
        result = inspect(files)
        settings = options(result)
        settings.dependencies[0] = settings.dependencies[0].model_copy(
            update={"package": "pandas\n--index-url evil"}
        )
        with self.assertRaises(ValueError):
            generate(files, result, settings)
        settings = options(inspect(files))
        settings.command = "python main.py; touch unwanted"
        with self.assertRaises(ValueError):
            generate(files, result, settings)

    def test_docker_can_be_omitted(self):
        files = {"main.py": b"pass"}
        result = inspect(files)
        self.assertNotIn(
            "Dockerfile",
            generate(files, result, options(result, docker=False)),
        )


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.context = TestClient(app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def test_environment_helper_is_downloadable(self):
        response = self.client.get("/api/environment-script")
        self.assertEqual(response.status_code, 200)
        self.assertIn("environment_report.py", response.headers["content-disposition"])
        self.assertIn("packages_distributions", response.text)

    def test_pypi_lookup_requires_an_existing_upload(self):
        response = self.client.get("/api/projects/missing/pypi")
        self.assertEqual(response.status_code, 404)

    def test_folder_paths_survive_analysis_and_download(self):
        sources = {
            "demo/main.py": b"from utils.helpers import message",
            "demo/utils/helpers.py": b"message = 'hello'",
            "demo/data/input.txt": b"first",
            "demo/backup/input.txt": b"second",
            "demo/data/attachment.zip": b"saved as data, not extracted",
            "demo/assets/logo.png": b"\x89PNG\r\n\x1a\n\0image",
            "demo/assets/music.mp3": b"ID3\0audio",
        }
        uploads = [("files", (path, data)) for path, data in sources.items()]
        structure = self.client.post("/api/inspect", files=uploads)
        self.assertEqual(structure.status_code, 200, structure.text)
        self.assertEqual(structure.json()["name"], "demo")
        result = self.client.post("/api/analyze", files=uploads)
        self.assertEqual(result.status_code, 200, result.text)
        analysis = result.json()
        self.assertEqual(analysis["name"], "demo")
        self.assertEqual(
            next(dep for dep in analysis["dependencies"] if dep["module"] == "utils")[
                "kind"
            ],
            "local",
        )
        package = self.client.post(
            "/api/package",
            json={
                "analysis_id": analysis["id"],
                "dependencies": analysis["dependencies"],
                "command": "python main.py",
                "docker": False,
            },
        )
        self.assertEqual(package.status_code, 200, package.text)
        with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
            for path, data in sources.items():
                self.assertEqual(archive.read(path.removeprefix("demo/")), data)
        self.client.delete(f"/api/projects/{analysis['id']}")

    def test_structure_is_available_before_analysis(self):
        source = zip_bytes(
            {
                "project/main.py": "pass",
                "project/data/input.csv": "x",
                "project/utils/helpers.py": "pass",
                "project/.venv/cache.py": "pass",
            }
        )
        response = self.client.post(
            "/api/inspect", files=[("files", ("project.zip", source))]
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["files"], ["data/input.csv", "main.py", "utils/helpers.py"]
        )
        self.assertEqual(response.json()["skipped"], 1)

    def test_structure_rejects_invalid_archives(self):
        response = self.client.post(
            "/api/inspect", files=[("files", ("bad.zip", b"broken"))]
        )
        self.assertEqual(response.status_code, 400)

    def test_validation_rejects_media_alone_but_allows_broken_source(self):
        for endpoint in ("/api/inspect", "/api/analyze"):
            for filename in ("photo.png", "main.py"):
                response = self.client.post(
                    endpoint, files=[("files", (filename, b"\x89PNG\0binary"))]
                )
                self.assertEqual(response.status_code, 400, response.text)
        response = self.client.post(
            "/api/analyze", files=[("files", ("main.py", b"def broken("))]
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            any(warning["code"] == "syntax" for warning in response.json()["warnings"])
        )
        self.client.delete(f"/api/projects/{response.json()['id']}")

    def test_analyze_preview_download_delete(self):
        response = self.client.post(
            "/api/analyze",
            files=[("files", ("main.py", b"import pandas", "text/plain"))],
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        payload = {
            "analysis_id": result["id"],
            "dependencies": result["dependencies"],
            "command": "python main.py",
        }
        preview = self.client.post("/api/preview", json=payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        package = self.client.post("/api/package", json=payload)
        self.assertEqual(package.status_code, 200)
        self.assertEqual(package.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
            self.assertEqual(
                sorted(archive.namelist()), preview.json()["archive_files"]
            )
            self.assertEqual(archive.read("main.py"), b"import pandas")
            self.assertEqual(
                archive.read("requirements.txt").decode(),
                preview.json()["files"]["requirements.txt"],
            )
        self.client.delete(f"/api/projects/{result['id']}")
        self.assertEqual(
            self.client.post("/api/preview", json=payload).status_code, 404
        )

    def test_bad_upload_and_expired_session_have_explanations(self):
        response = self.client.post(
            "/api/analyze", files=[("files", ("empty.zip", b"bad", "application/zip"))]
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("ZIP", response.json()["detail"])
        response = self.client.post(
            "/api/preview", json={"analysis_id": "missing", "dependencies": []}
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_limit_rejected_before_parsing(self):
        response = self.client.post(
            "/api/analyze",
            content=b"",
            headers={"content-length": str(24 * 1024 * 1024)},
        )
        self.assertEqual(response.status_code, 413)

    def test_invalid_environment_is_rejected(self):
        response = self.client.post(
            "/api/analyze",
            files=[
                ("files", ("main.py", b"pass")),
                ("environment", ("report.json", b"{}")),
            ],
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("отчёт", response.json()["detail"])

    def test_preview_tree_matches_backups_and_secret_exclusion(self):
        source = zip_bytes(
            {
                "main.py": "pass",
                "README.md": "original instructions",
                "requirements.txt": "requests==2.32.3",
                "data/sample.csv": "x",
                ".env": "TOKEN=example-secret-value",
            }
        )
        response = self.client.post(
            "/api/analyze", files=[("files", ("demo.zip", source))]
        )
        result = response.json()
        payload = {
            "analysis_id": result["id"],
            "dependencies": result["dependencies"],
            "command": "python main.py",
        }
        preview = self.client.post("/api/preview", json=payload).json()
        self.assertEqual(preview["preserved_files"]["README.md"], "originals/README.md")
        self.assertNotIn(".env", preview["archive_files"])
        package = self.client.post("/api/package", json=payload)
        with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
            self.assertEqual(sorted(archive.namelist()), preview["archive_files"])
            self.assertEqual(
                archive.read("originals/README.md"), b"original instructions"
            )


if __name__ == "__main__":
    unittest.main()
