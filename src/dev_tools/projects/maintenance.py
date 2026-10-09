"""Pure rediscovery and native dependency-resolution steps for preparation plans."""

from __future__ import annotations

import copy
import uuid
from dataclasses import replace
from pathlib import Path

from ..i18n import message
from .config import parse_project
from .discovery import discover_project
from .models import CommandSpec, ProjectError, ProjectSpec, TaskSpec, within

LOCKFILES = {
    "npm": ("package-lock.json", "npm-shrinkwrap.json"),
    "pnpm": ("pnpm-lock.yaml",),
    "yarn": ("yarn.lock",),
    "bun": ("bun.lock", "bun.lockb"),
    "uv": ("uv.lock",),
}


def atomic_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ProjectError(message("refusing symbolic-link update: {path}", path=path))
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def merge_generated(previous: dict, current: dict, incoming: dict) -> dict:
    """Three-way merge: only replace values still equal to their generated baseline."""
    result = copy.deepcopy(current)
    for key in sorted(previous.keys() | incoming.keys()):
        if key not in previous:
            if key not in current and key in incoming:
                result[key] = copy.deepcopy(incoming[key])
        elif key in current:
            if current[key] == previous[key]:
                if key in incoming:
                    result[key] = copy.deepcopy(incoming[key])
                else:
                    result.pop(key, None)
            elif key in incoming and all(
                isinstance(value, dict) for value in (previous[key], current[key], incoming[key])
            ):
                result[key] = merge_generated(previous[key], current[key], incoming[key])
    return result


def discovery_plan(
    spec: ProjectSpec, source: Path, state: dict, platform: str
) -> tuple[ProjectSpec, dict | None]:
    if spec.discovery != "auto":
        return spec, None
    previous = state.get("discovery_baseline")
    runtime = (
        "host"
        if (
            any(item.driver == "process" for item in spec.services.values())
            or any(
                item.get("driver", "process") == "process"
                for item in (previous or {}).get("services", {}).values()
            )
        )
        else "auto"
    )
    incoming = discover_project(source, name=spec.name, toolchain=spec.toolchain, runtime=runtime)
    if not previous:
        # Existing declarations remain authoritative on the first adoption.
        return spec, incoming
    raw = copy.deepcopy(spec.raw)
    for key in ("tasks", "services", "builds"):
        raw[key] = merge_generated(previous.get(key, {}), raw.get(key, {}), incoming.get(key, {}))
    for target in ("windows", "wsl"):
        parse_project(raw, target)
    return parse_project(raw, platform), incoming


def resolution_task(task: TaskSpec) -> TaskSpec | None:
    argv = task.command.argv
    manager = task.manager
    if manager not in LOCKFILES or not argv or argv[0] != manager:
        return None
    # Only standard install commands are managed. Custom package selections or flags
    # remain the user's workflow, rather than silently resolving a different scope.
    if any(
        argument
        not in {
            "ci",
            "install",
            "sync",
            "--frozen-lockfile",
            "--immutable",
            "--locked",
            "--python",
            "{python}",
            "--ignore-scripts",
            "--non-interactive",
            "--no-audit",
            "--no-fund",
        }
        for argument in argv[1:]
    ):
        return None
    if manager == "npm" and argv[1:2] in {("ci",), ("install",)}:
        command = (
            "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
            *(argument for argument in argv if argument in {"--no-audit", "--no-fund"}),
        )
    elif manager == "pnpm" and argv[1:2] == ("install",):
        command = ("pnpm", "install", "--lockfile-only", "--ignore-scripts", "--no-frozen-lockfile")
    elif manager == "yarn" and argv[1:2] == ("install",):
        command = (
            ("yarn", "install", "--ignore-scripts", "--non-interactive")
            if "--frozen-lockfile" in argv
            else ("yarn", "install", "--mode=update-lockfile")
        )
    elif manager == "bun" and argv[1:2] == ("install",):
        command = ("bun", "install", "--lockfile-only", "--ignore-scripts")
    elif manager == "uv" and argv[1:2] == ("sync",):
        command = ("uv", "lock", "--python", "{python}")
    else:
        return None
    return replace(task, name="resolve-" + task.name, command=CommandSpec(argv=command))


def clean_venv_task(task: TaskSpec, root: Path) -> TaskSpec:
    argv = task.command.argv
    if len(argv) == 4 and argv[:3] == ("{python}", "-m", "venv"):
        if (within(root, task.workdir) / argv[3]).is_symlink():
            raise ProjectError(message("refusing symbolic-link update: {path}", path=argv[3]))
        target = within(within(root, task.workdir), argv[3])
        if target == root.resolve() or target.name != ".venv":
            return task
        return replace(task, command=CommandSpec(argv=(*argv[:3], "--clear", argv[3])))
    return task
