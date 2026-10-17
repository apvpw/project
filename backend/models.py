"""Данные, которыми обмениваются анализатор, генератор и интерфейс."""

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    file: str
    line: int = 0
    detail: str


class Dependency(BaseModel):
    module: str
    kind: Literal["standard", "local", "external", "unknown"]
    package: str = ""
    version: str = ""
    source: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    environment_version: str = ""
    lookup_allowed: bool = False


class Warning(BaseModel):
    code: str
    message: str
    file: str = ""
    line: int = 0
    level: Literal["info", "warning", "danger"] = "warning"


class EntryPoint(BaseModel):
    command: str
    reason: str
    file: str


class Analysis(BaseModel):
    id: str
    name: str
    files: list[str]
    dependencies: list[Dependency]
    warnings: list[Warning]
    entry_points: list[EntryPoint]
    python_requirement: str = ""
    python_source: str = ""
    stack: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    conda_dependencies: list[str] = Field(default_factory=list)
    environment_summary: str = ""


class PackageOptions(BaseModel):
    dependencies: list[Dependency]
    command: str = Field(default="", max_length=500)
    python_version: str = Field(default="3.12", pattern=r"^3\.(?:[9]|1[0-9])$")
    docker: bool = True
    exclude_suspected_secrets: bool = True


class PackageRequest(PackageOptions):
    analysis_id: str
