from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from ..i18n import message
from .config import parse_project
from .detection import (
    _find,
    _frontend_port,
    _maven_dependency_paths,
    _package_details,
    _package_root,
    _python_root,
    _spring_boot_module,
    _spring_boot_properties,
    _spring_boot_version,
)
from .models import ProjectError

COMPOSE_NAMES = {"compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"}


def project_name(root: Path) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", root.name.lower()).strip("-._")[:63] or "project"


def _identifier(prefix: str, path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    slug = re.sub(r"[^a-z0-9._-]+", "-", relative.lower()).strip("-.")
    identity = prefix + ("-" + slug if slug else "")
    return (
        identity
        if len(identity) <= 63
        else identity[:54] + "-" + hashlib.sha256(relative.encode()).hexdigest()[:8]
    )


def discover_project(
    root: Path, *, name: str | None = None, runtime: str = "auto", toolchain: str = "mise"
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    raw: dict[str, Any] = {
        "schema": 1,
        "name": name or project_name(root),
        "toolchain": toolchain,
        "rebuild_on_branch": True,
        "tasks": {},
        "services": {},
        "builds": {},
    }
    tasks, services, builds = raw["tasks"], raw["services"], raw["builds"]
    identifiers = {}

    def identifier(prefix: str, path: Path) -> str:
        name = _identifier(prefix, path, root)
        identity = prefix, path.relative_to(root).as_posix()
        if name in identifiers and identifiers[name] != identity:
            raise ProjectError(
                message(
                    "ambiguous generated name {name}; declare these tasks/services explicitly",
                    name=name,
                )
            )
        identifiers[name] = identity
        return name

    compose_files = _find(root, COMPOSE_NAMES)
    if runtime not in {"auto", "host", "compose"}:
        raise ProjectError(message("runtime must be auto, host or compose"))
    if runtime == "compose" and not compose_files:
        raise ProjectError(message("Compose requested but no Compose declaration was found"))
    if compose_files and runtime in {"auto", "compose"}:
        for path in compose_files:
            key = identifier("compose", path.parent)
            if key in services:
                raise ProjectError(
                    message(
                        "multiple Compose base files in {parent}; declare compose.files explicitly",
                        parent=path.parent,
                    )
                )
            services[key] = {
                "driver": "compose",
                "workdir": path.parent.relative_to(root).as_posix(),
                "compose": {"files": [path.name], "build": True, "pull": False},
                "restart": "on-failure",
            }
        parse_project(raw, "windows")
        parse_project(raw, "wsl")
        return raw
    for package in _find(root, {"package.json"}):
        value = json.loads(package.read_text(encoding="utf-8-sig"))
        framework, manager = _package_details(package, root)
        workspace, classic = _package_root(package, root, manager)
        task = identifier("install-node", workspace)
        frozen = {
            "npm": ["ci"],
            "pnpm": ["install", "--frozen-lockfile"],
            "yarn": ["install", "--frozen-lockfile" if classic else "--immutable"],
            "bun": ["install", "--frozen-lockfile"],
        }[manager]
        tasks.setdefault(
            task,
            {
                "command": [manager, *frozen],
                "manager": manager,
                "workdir": workspace.relative_to(root).as_posix(),
                "tools": ["node", *([] if manager == "npm" else [manager])],
            },
        )
        scripts = value.get("scripts", {})
        script = "dev" if "dev" in scripts else "start" if "start" in scripts else ""
        if not script:
            continue
        key = identifier("web", package.parent)
        command = [manager, "run", script]
        port = _frontend_port(package, framework)
        if port is None and framework in {"next", "vite", "react-scripts"}:
            base = 5173 if framework == "vite" else 3000
            occupied = {item.get("health", {}).get("tcp") for item in services.values()}
            port = next(candidate for candidate in range(base, 65536) if candidate not in occupied)
        env = {}
        if framework in {"next", "vite"}:
            command.extend(["--", "--port", str(port)])
        elif framework == "react-scripts" and port:
            env = {"PORT": str(port), "HOST": "0.0.0.0"}
        services[key] = {
            "command": command,
            "workdir": package.parent.relative_to(root).as_posix(),
            "prepare": [task],
            "tools": ["node"],
            "env": env,
            "health": {"tcp": port} if port else {},
            "restart": "on-failure",
        }
    poms = _find(root, {"pom.xml"})
    for pom in poms:
        ancestors = [
            item for item in poms if item.parent == pom.parent or item.parent in pom.parents
        ]
        aggregator = min(ancestors, key=lambda path: len(path.relative_to(root).parts))
        workdir = aggregator.parent.relative_to(root).as_posix()
        task = identifier("install-maven", aggregator.parent)
        java = {"maven": {"repository": "user"}}
        tasks.setdefault(
            task,
            {
                "command": ["{maven}", "-B", "-ntp", "-DskipTests", "install"],
                "manager": "maven",
                "workdir": workdir,
                "tools": ["java"],
                "java": java,
            },
        )
        if _spring_boot_module([pom]) is None:
            continue
        key = identifier("java", pom.parent)
        port, profile = _spring_boot_properties(pom)
        version = _spring_boot_version(poms, pom)
        watched, modules = _maven_dependency_paths(poms, pom, root)
        selector = (
            []
            if pom == aggregator
            else ["-pl", pom.parent.relative_to(aggregator.parent).as_posix()]
        )
        spring = {"classpath_modules": [path.as_posix() for path in modules]}
        if version:
            spring["devtools"] = f"org.springframework.boot:spring-boot-devtools:{version}"
        else:
            spring["unresolved"] = "Spring Boot version is not declared; set java.spring.devtools"
        java = {"maven": {"repository": "user"}, "spring": spring}
        command = ["{maven}", "-B", "-ntp", *selector, "spring-boot:run"]
        if profile:
            command.append(f"-Dspring-boot.run.profiles={profile}")
        command.append("-Dspring-boot.run.additional-classpath-elements={spring_classpath}")
        command.append(
            "-Dspring-boot.run.jvmArguments=-Dspring.devtools.restart.trigger-file=dev-tools-reload.trigger"
        )
        services[key] = {
            "command": command,
            "workdir": workdir,
            "prepare": [task],
            "tools": ["java"],
            "java": java,
            "health": {"tcp": port},
        }
        build_tasks = []
        for label, goals in (
            ("compile", ["compile"]),
            ("resources", ["install"]),
            ("clean", ["clean", "install"]),
        ):
            task_key = f"{label}-{key}"
            if len(task_key) > 63:
                task_key = task_key[:54] + "-" + hashlib.sha256(task_key.encode()).hexdigest()[:8]
            tasks[task_key] = {
                "command": ["{maven}", "-B", "-ntp", *selector, "-am", "-DskipTests", *goals],
                "workdir": workdir,
                "tools": ["java"],
                "java": java,
                "role": "build",
            }
            build_tasks.append(task_key)
        builds[key] = {
            "watch": list(
                dict.fromkeys(
                    [
                        *[path.as_posix() for path in watched],
                        *[(path.parents[1] / "pom.xml").as_posix() for path in watched],
                        aggregator.relative_to(root).as_posix(),
                        (aggregator.parent / ".mvn").relative_to(root).as_posix(),
                        (aggregator.parent / "mvnw").relative_to(root).as_posix(),
                        (aggregator.parent / "mvnw.cmd").relative_to(root).as_posix(),
                    ]
                )
            ),
            "source_task": build_tasks[0],
            "resource_task": build_tasks[1],
            "structural_task": build_tasks[2],
        }
    for path in _find(root, {"pyproject.toml"}):
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        workspace, use_uv = _python_root(path, root)
        relative = workspace.relative_to(root).as_posix()
        python = {"root": relative}
        task = identifier("install-python", workspace)
        if use_uv:
            tasks.setdefault(
                task,
                {
                    "command": ["uv", "sync", "--locked", "--python", "{python}"],
                    "workdir": relative,
                    "manager": "uv",
                    "tools": ["python", "uv"],
                    "env": {"UV_PYTHON_DOWNLOADS": "never"},
                },
            )
        else:
            create = identifier("create-venv", workspace)
            tasks.setdefault(
                create,
                {
                    "command": ["{python}", "-m", "venv", ".venv"],
                    "workdir": relative,
                    "tools": ["python"],
                },
            )
            tasks.setdefault(
                task,
                {
                    "command": ["{venv_python}", "-m", "pip", "install", "-e", "."],
                    "workdir": relative,
                    "depends_on": [create],
                    "manager": "pip",
                    "python": python,
                },
            )
        dependencies = value.get("project", {}).get("dependencies", [])
        if not any(str(item).lower().startswith("fastapi") for item in dependencies):
            continue
        key = identifier("python", path.parent)
        occupied = {item.get("health", {}).get("tcp") for item in services.values()}
        port = next(candidate for candidate in range(8000, 65536) if candidate not in occupied)
        module = "main:app" if (path.parent / "main.py").is_file() else "app.main:app"
        services[key] = {
            "command": [
                "{venv_python}",
                "-m",
                "uvicorn",
                module,
                "--host",
                "0.0.0.0",
                "--port",
                str(port),
                "--reload",
            ],
            "workdir": path.parent.relative_to(root).as_posix(),
            "prepare": [task],
            "tools": ["python"],
            "python": python,
            "health": {"tcp": port},
        }
    parse_project(raw, "windows")
    parse_project(raw, "wsl")
    return raw
