from __future__ import annotations

import hashlib
import shutil
from dataclasses import asdict, dataclass
from typing import Any

from ..i18n import message
from ..scanner import scan_project
from . import SCHEMA_VERSION
from .config import CONFIG_NAME, load_project
from .detection import _find
from .drivers import maven_wrapper, spring_artifact
from .maintenance import LOCKFILES, clean_venv_task, resolution_task
from .models import ProjectBinding, ProjectError, ProjectSpec, TaskSpec, within
from .toolchain import root_runtime_versions


def fingerprint(
    binding: ProjectBinding,
    *,
    spec: ProjectSpec | None = None,
    root=None,
    include_locks: bool = True,
) -> str:
    source = (root or binding.source).resolve()
    digest = hashlib.sha256()
    names = {
        CONFIG_NAME,
        "mise.toml",
        ".mise.toml",
        ".nvmrc",
        ".node-version",
        ".python-version",
        ".tool-versions",
        ".npmrc",
        ".yarnrc.yml",
        "pom.xml",
        "maven-wrapper.properties",
        "mvnw",
        "mvnw.cmd",
        "package.json",
        "pnpm-workspace.yaml",
        "pyproject.toml",
        "uv.lock",
        "requirements.txt",
        "compose.yaml",
        "compose.yml",
        "docker-compose.yaml",
        "docker-compose.yml",
        *[name for files in LOCKFILES.values() for name in files],
    }
    if not include_locks:
        names.difference_update(name for files in LOCKFILES.values() for name in files)
    paths = set(_find(source, names))
    if (source / CONFIG_NAME).is_file():
        spec = spec or load_project(source, binding.environment)
        for item in [*spec.tasks.values(), *spec.services.values()]:
            directory = within(source, item.workdir)
            paths.update(directory / name for name in names if (directory / name).is_file())
    for path in sorted(paths):
        if path.is_file():
            within(source, path.relative_to(source).as_posix())
            digest.update(path.relative_to(source).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class PreparationPlan:
    binding: ProjectBinding
    spec: ProjectSpec
    tasks: tuple[str, ...]
    required_tools: tuple[str, ...]
    runtime_versions: tuple[str, ...]
    requirements: Any
    artifacts: tuple[dict[str, str], ...]
    unresolved: tuple[str, ...]
    revision: str
    resolution_tasks: tuple[TaskSpec, ...] = ()
    discovery_baseline: dict | None = None
    configuration_update: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "prepare",
            "name": self.binding.name,
            "environment": self.binding.environment,
            "binding": self.binding.as_dict(),
            "revision": self.revision,
            "required_tools": list(self.required_tools),
            "runtime_versions": list(self.runtime_versions),
            "platform_requirements": asdict(self.requirements),
            "tasks": [
                {
                    "name": name,
                    "cwd": str(within(self.binding.workspace, self.spec.tasks[name].workdir)),
                    **clean_venv_task(self.spec.tasks[name], self.binding.workspace).as_dict(),
                }
                for name in self.tasks
            ],
            "artifacts": list(self.artifacts),
            "dependency_mode": self.spec.dependency_mode,
            "resolution_tasks": [
                task.as_dict() | {"name": task.name} for task in self.resolution_tasks
            ],
            "configuration_update": self.configuration_update,
            "unresolved": list(self.unresolved),
            "ready": not self.unresolved,
        }


def preparation_plan(
    spec: ProjectSpec,
    binding: ProjectBinding,
    backend,
    *,
    state: dict | None = None,
    discovery_baseline: dict | None = None,
    configuration_update: bool = False,
) -> PreparationPlan:
    scan = scan_project(binding.source)
    unresolved = [
        message("{location}: {detail}", location=item.source, detail=item.message)
        for item in scan.diagnostics
    ]
    unresolved.extend(
        message("conflicting {tool} versions", tool=item.tool) for item in scan.conflicts
    )
    requirements = backend.requirements(spec)
    unresolved.extend(requirements.unresolved)
    selected = tuple(
        dict.fromkeys(
            [name for name, task in spec.tasks.items() if task.role == "prepare"]
            + [name for service in spec.services.values() for name in service.prepare]
        )
    )
    tasks = spec.task_order(selected)
    tools: set[str] = set()
    artifacts = []
    for item in [*spec.tasks.values(), *spec.services.values()]:
        tools.update(item.tools)
        standard = {
            "npm": "node",
            "npx": "node",
            "mvn": "maven",
            "{maven}": "java",
            "{python}": "python",
            "{venv_python}": "python",
            "node": "node",
            "java": "java",
            "uv": "uv",
            "python": "python",
            "python3": "python",
            "pnpm": "pnpm",
            "yarn": "yarn",
            "bun": "bun",
        }
        if item.command.argv and item.command.argv[0] in standard:
            tools.add(standard[item.command.argv[0]])
        if item.command.argv and item.command.argv[0] in {"uv", "pnpm", "yarn", "bun"}:
            tools.add("python" if item.command.argv[0] == "uv" else "node")
        if item.command.shell and (
            item.command.shell not in backend.native_shells
            or shutil.which(item.command.shell) is None
        ):
            unresolved.append(
                message(
                    "{name}: shell {shell} is unavailable on {environment}; use a platform override",
                    name=item.name,
                    shell=item.command.shell,
                    environment=binding.environment,
                )
            )
        if "{maven}" in item.command.argv and not maven_wrapper(
            binding.source, item.workdir, backend.wrapper_filename
        ):
            tools.add("maven")
        if item.command.argv and item.command.argv[0] in {"mvn", "{maven}"}:
            tools.add("java")
        within(binding.source, item.workdir)
        if not within(binding.source, item.workdir).is_dir():
            unresolved.append(
                message(
                    "{name}: workdir does not exist in source: {workdir}",
                    name=item.name,
                    workdir=item.workdir,
                )
            )
    resolution_tasks = []
    declarations_changed = (state or {}).get("dependency_revision") != fingerprint(
        binding, spec=spec, include_locks=False
    )
    for name in tasks:
        task = spec.tasks[name]
        clean_venv_task(task, binding.workspace)
        files = LOCKFILES.get(task.manager, ())
        directory = within(binding.source, task.workdir)
        for filename in files:
            if (directory / filename).is_symlink():
                unresolved.append(
                    message("refusing symbolic-link update: {path}", path=directory / filename)
                )
        missing_lock = files and not any((directory / filename).is_file() for filename in files)
        resolver = resolution_task(task) if spec.dependency_mode == "auto" else None
        if resolver and (declarations_changed or missing_lock):
            resolution_tasks.append(resolver)
        if missing_lock and not resolver:
            unresolved.append(
                message(
                    "{name}: {manager} requires a lockfile in {workdir}",
                    name=name,
                    manager=task.manager,
                    workdir=task.workdir,
                )
            )
    for service in spec.services.values():
        try:
            artifact = spring_artifact(binding, backend, service)
            if artifact:
                artifacts.append(
                    {"service": service.name, "coordinate": artifact[0], "path": str(artifact[1])}
                )
        except ProjectError as exc:
            unresolved.append(exc.args[0])
        if service.driver == "compose":
            for filename in service.options.get("compose", {}).get("files", ["compose.yaml"]):
                if not within(within(binding.source, service.workdir), filename).is_file():
                    unresolved.append(
                        message(
                            "{name}: missing Compose file: {filename}",
                            name=service.name,
                            filename=filename,
                        )
                    )
    ports = [
        service.health["tcp"] for service in spec.services.values() if service.health.get("tcp")
    ]
    if len(ports) != len(set(ports)):
        unresolved.append(message("services declare duplicate TCP ports; configure separate ports"))
    runtime_versions = ()
    if spec.toolchain == "mise":
        try:
            runtime_versions = root_runtime_versions(binding.source)
        except (ProjectError, ValueError) as exc:
            unresolved.append(exc.args[0])
        if not scan.existing_config:
            unresolved.append(message("root mise configuration is missing; run dev-tools init"))
        unresolved.extend(
            message("{tool}: {reason}", tool=item.tool, reason=item.reason)
            for item in scan.unresolved
            if item.tool in tools
        )
        for name in sorted(tools - set(scan.root_tools)):
            unresolved.append(message("root mise config has no declaration for: {name}", name=name))
        if shutil.which("mise") is None:
            unresolved.append(message("mise is unavailable; run the dev-tools installer"))
    else:
        commands = {
            "java": "java",
            "node": "node",
            "python": backend.python_command,
            "maven": "mvn",
        }
        for name in sorted(tools):
            if shutil.which(commands.get(name, name)) is None:
                unresolved.append(
                    message("system tool {name} is unavailable; declare it with mise", name=name)
                )
    return PreparationPlan(
        binding,
        spec,
        tasks,
        tuple(sorted(tools)),
        runtime_versions,
        requirements,
        tuple(artifacts),
        tuple(dict.fromkeys(unresolved)),
        fingerprint(binding, spec=spec),
        tuple(resolution_tasks),
        discovery_baseline,
        configuration_update,
    )
