from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ..i18n import message

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


class ProjectError(ValueError):
    """Invalid declaration or failed project operation."""


def validate_name(value: str) -> str:
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *[f"com{index}" for index in range(1, 10)],
        *[f"lpt{index}" for index in range(1, 10)],
    }
    if not NAME_PATTERN.fullmatch(value) or value.split(".")[0] in reserved:
        raise ProjectError(
            message("invalid project, task or service name: {value}", value=repr(value))
        )
    return value


def relative_path(value: str) -> str:
    if (
        not value
        or PureWindowsPath(value).drive
        or PureWindowsPath(value).is_absolute()
        or PurePosixPath(value).is_absolute()
        or ".." in PurePosixPath(value.replace("\\", "/")).parts
    ):
        raise ProjectError(
            message(
                "project paths must be relative and stay inside the project: {value}",
                value=repr(value),
            )
        )
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def within(root: Path, relative: str) -> Path:
    candidate = (root / relative_path(relative)).resolve()
    boundary = root.resolve()
    if candidate != boundary and boundary not in candidate.parents:
        raise ProjectError(message("path escapes project: {relative}", relative=relative))
    return candidate


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...] = ()
    script: str = ""
    shell: str = ""

    def as_dict(self) -> dict[str, Any]:
        if self.argv:
            return {"command": list(self.argv)}
        return {"command": self.script, "shell": self.shell}


@dataclass(frozen=True)
class TaskSpec:
    name: str
    command: CommandSpec
    workdir: str = "."
    depends_on: tuple[str, ...] = ()
    manager: str = ""
    tools: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    role: str = "prepare"

    def as_dict(self) -> dict[str, Any]:
        result = dict(self.options)
        result.update(self.command.as_dict())
        result.update(
            workdir=self.workdir,
            depends_on=list(self.depends_on),
            manager=self.manager,
            tools=list(self.tools),
            env=self.env,
            role=self.role,
        )
        return result


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    command: CommandSpec
    workdir: str = "."
    driver: str = "process"
    prepare: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    restart: str = "on-failure"
    options: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result = dict(self.options)
        result.update(self.command.as_dict())
        result.update(
            workdir=self.workdir,
            driver=self.driver,
            prepare=list(self.prepare),
            depends_on=list(self.depends_on),
            tools=list(self.tools),
            env=self.env,
            health=self.health,
            restart=self.restart,
        )
        return result


@dataclass(frozen=True)
class BuildSpec:
    service: str
    watch: tuple[str, ...]
    source_task: str
    resource_task: str
    structural_task: str
    extensions: tuple[str, ...] = (".java", ".xml", ".yaml", ".yml", ".properties")
    resources: tuple[str, ...] = (".xml", ".yaml", ".yml", ".properties")
    structural: tuple[str, ...] = (
        "pom.xml",
        "**/pom.xml",
        ".mvn/**",
        "**/.mvn/**",
        "mvnw",
        "mvnw.cmd",
        "**/mvnw",
        "**/mvnw.cmd",
    )


@dataclass(frozen=True)
class ProjectSpec:
    name: str
    tasks: dict[str, TaskSpec]
    services: dict[str, ServiceSpec]
    builds: tuple[BuildSpec, ...] = ()
    toolchain: str = "mise"
    rebuild_on_branch: bool = True
    sync_exclude: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def service_order(self) -> tuple[str, ...]:
        return dependency_order({name: item.depends_on for name, item in self.services.items()})

    def task_order(self, selected: tuple[str, ...] | None = None) -> tuple[str, ...]:
        return dependency_order(
            {name: item.depends_on for name, item in self.tasks.items()}, selected
        )


def dependency_order(
    graph: dict[str, tuple[str, ...]], selected: tuple[str, ...] | None = None
) -> tuple[str, ...]:
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name not in graph:
            raise ProjectError(message("unknown dependency: {name}", name=name))
        if name in visiting:
            raise ProjectError(message("cyclic dependency involving: {name}", name=name))
        if name in ordered:
            return
        visiting.add(name)
        for dependency in graph[name]:
            visit(dependency)
        visiting.remove(name)
        ordered.append(name)

    for name in graph if selected is None else selected:
        visit(name)
    return tuple(ordered)


@dataclass(frozen=True)
class ProjectBinding:
    name: str
    environment: str
    source: Path
    workspace: Path
    state: Path
    run_user: str = ""
    cache_root: Path | None = None

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "environment": self.environment,
            "source": str(self.source),
            "workspace": str(self.workspace),
            "state": str(self.state),
            "run_user": self.run_user,
            "cache_root": str(self.cache_root) if self.cache_root else "",
        }
