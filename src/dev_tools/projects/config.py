from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from ..i18n import message
from .models import (
    BuildSpec,
    CommandSpec,
    ProjectError,
    ProjectSpec,
    ServiceSpec,
    TaskSpec,
    relative_path,
    validate_name,
)

CONFIG_NAME = "dev-tools.toml"


def _table(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectError(message("{label} must be a table", label=label))
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ProjectError(message("{label} must be an array of nonempty strings", label=label))
    return tuple(value)


def _env(value: Any, label: str) -> dict[str, str]:
    result = _table(value, label)
    if any(not isinstance(item, str) or "\x00" in item for item in result.values()):
        raise ProjectError(message("{label} values must be strings", label=label))
    if any(not key or "=" in key or "\x00" in key for key in result):
        raise ProjectError(message("invalid environment variable in {label}", label=label))
    return result


def _options(item: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key in ("java", "python", "compose"):
        if key in item:
            result[key] = _table(item[key], key)
    java = result.get("java", {})
    _keys(java, {"maven", "spring"}, "java")
    maven = _table(java.get("maven", {}), "java.maven")
    if maven.get("repository", "user") not in {"user", "project"}:
        raise ProjectError(message("java.maven.repository must be user or project"))
    if set(maven) - {"repository"}:
        raise ProjectError(message("unknown java.maven option"))
    spring = _table(java.get("spring", {}), "java.spring")
    _keys(
        spring, {"devtools", "unresolved", "classpath_modules", "classpath_entries"}, "java.spring"
    )
    for path in _strings(spring.get("classpath_modules", []), "classpath modules"):
        relative_path(path)
    for entry in _strings(spring.get("classpath_entries", []), "classpath entries"):
        if entry in {".", ".."} or "/" in entry or "\\" in entry:
            raise ProjectError(message("classpath entries must name immediate directories"))
    for key in ("devtools", "unresolved"):
        if key in spring and not isinstance(spring[key], str):
            raise ProjectError(message("java.spring.{key} must be a string", key=key))
    _keys(result.get("python", {}), {"root"}, "python")
    relative_path(str(result.get("python", {}).get("root", ".")))
    compose = result.get("compose", {})
    _keys(compose, {"files", "profiles", "build", "pull"}, "compose")
    for path in _strings(compose.get("files", ["compose.yaml"]), "compose files"):
        relative_path(path)
    _strings(compose.get("profiles", []), "compose profiles")
    if any(type(compose[key]) is not bool for key in ("build", "pull") if key in compose):
        raise ProjectError(message("Compose build and pull must be boolean"))
    return result


def _keys(item: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(item) - allowed
    if unknown:
        raise ProjectError(
            message(
                "{label}: unknown fields: {value}", label=label, value=", ".join(sorted(unknown))
            )
        )


def command_spec(raw: dict[str, Any], label: str) -> CommandSpec:
    value = raw.get("command", [])
    shell = raw.get("shell", "")
    if isinstance(value, list):
        if shell:
            raise ProjectError(message("{label}: argv commands must not set shell", label=label))
        return CommandSpec(argv=_strings(value, f"{label}.command"))
    if isinstance(value, str) and value.strip() and shell in {"bash", "pwsh"}:
        return CommandSpec(script=value, shell=shell)
    raise ProjectError(
        message("{label}: use an argv array or a script with explicit shell=bash|pwsh", label=label)
    )


def _overlay(raw: dict[str, Any], platform: str) -> dict[str, Any]:
    result = {key: value for key, value in raw.items() if key != "platforms"}
    platforms = _table(raw.get("platforms", {}), "platforms")
    if set(platforms) - {"windows", "wsl"}:
        raise ProjectError(message("platform overrides support windows and wsl"))
    for key, value in _table(platforms.get(platform, {}), f"platforms.{platform}").items():
        if key == "env":
            result[key] = {**_table(result.get(key, {}), "env"), **_table(value, "env")}
        else:
            result[key] = value
    return result


def parse_project(raw: dict[str, Any], platform: str) -> ProjectSpec:
    if platform not in {"windows", "wsl"}:
        raise ProjectError(message("platform must be windows or wsl"))
    _keys(
        raw,
        {
            "schema",
            "name",
            "toolchain",
            "rebuild_on_branch",
            "sync_exclude",
            "tasks",
            "services",
            "builds",
            "sync_include",
        },
        "project",
    )
    if type(raw.get("schema")) is not int or raw.get("schema") != 1:
        raise ProjectError(message("dev-tools.toml requires schema = 1"))
    name = validate_name(str(raw.get("name", "")))
    toolchain = raw.get("toolchain", "mise")
    if toolchain not in {"mise", "system"}:
        raise ProjectError(message("toolchain must be mise or system"))
    tasks: dict[str, TaskSpec] = {}
    for key, definition in _table(raw.get("tasks", {}), "tasks").items():
        validate_name(key)
        item = _overlay(_table(definition, f"tasks.{key}"), platform)
        _keys(
            item,
            {
                "command",
                "shell",
                "workdir",
                "depends_on",
                "manager",
                "tools",
                "env",
                "java",
                "python",
                "compose",
                "role",
            },
            f"tasks.{key}",
        )
        role = item.get("role", "prepare")
        if role not in {"prepare", "build"}:
            raise ProjectError(message("task {key}: role must be prepare or build", key=key))
        tasks[key] = TaskSpec(
            key,
            command_spec(item, f"tasks.{key}"),
            relative_path(str(item.get("workdir", "."))),
            _strings(item.get("depends_on", []), "task dependencies"),
            str(item.get("manager", "")),
            _strings(item.get("tools", []), "task tools"),
            _env(item.get("env", {}), "task env"),
            _options(item),
            role,
        )
        if not tasks[key].command.argv and not tasks[key].command.script:
            raise ProjectError(message("task {key} needs a command", key=key))
    services: dict[str, ServiceSpec] = {}
    for key, definition in _table(raw.get("services", {}), "services").items():
        validate_name(key)
        item = _overlay(_table(definition, f"services.{key}"), platform)
        _keys(
            item,
            {
                "command",
                "shell",
                "workdir",
                "driver",
                "prepare",
                "depends_on",
                "tools",
                "env",
                "health",
                "restart",
                "java",
                "python",
                "compose",
            },
            f"services.{key}",
        )
        driver = item.get("driver", "process")
        restart = item.get("restart", "on-failure")
        if driver not in {"process", "compose"} or restart not in {"never", "on-failure", "always"}:
            raise ProjectError(message("service {key}: invalid driver or restart policy", key=key))
        command = command_spec(item, f"services.{key}")
        if driver == "process" and not command.argv and not command.script:
            raise ProjectError(message("service {key} needs a command", key=key))
        health = _table(item.get("health", {}), "health")
        if set(health) - {"tcp", "http", "timeout"}:
            raise ProjectError(message("service {key}: unknown health check", key=key))
        port = health.get("tcp")
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise ProjectError(message("service {key}: invalid TCP health port", key=key))
        timeout = health.get("timeout", 15)
        if (
            not isinstance(timeout, int | float)
            or isinstance(timeout, bool)
            or not 0 < timeout <= 300
        ):
            raise ProjectError(message("service {key}: invalid health timeout", key=key))
        if "http" in health and (
            not isinstance(health["http"], str)
            or not health["http"].startswith(("http://", "https://"))
        ):
            raise ProjectError(
                message("service {key}: HTTP health check must be an HTTP(S) URL", key=key)
            )
        services[key] = ServiceSpec(
            key,
            command,
            relative_path(str(item.get("workdir", "."))),
            driver,
            _strings(item.get("prepare", []), "service prepare tasks"),
            _strings(item.get("depends_on", []), "service dependencies"),
            _strings(item.get("tools", []), "service tools"),
            _env(item.get("env", {}), "service env"),
            health,
            restart,
            _options(item),
        )
        for task in services[key].prepare:
            if task not in tasks:
                raise ProjectError(
                    message(
                        "service {key} requires unknown preparation task {task}", key=key, task=task
                    )
                )
        compose = services[key].options.get("compose", {})
        if driver == "compose":
            for filename in _strings(
                _table(compose, "compose").get("files", ["compose.yaml"]), "compose files"
            ):
                relative_path(filename)
    builds = []
    for service, value in _table(raw.get("builds", {}), "builds").items():
        if service not in services:
            raise ProjectError(
                message("build policy references unknown service: {service}", service=service)
            )
        item = _table(value, f"builds.{service}")
        _keys(
            item,
            {
                "watch",
                "source_task",
                "resource_task",
                "structural_task",
                "extensions",
                "resources",
                "structural",
            },
            f"builds.{service}",
        )
        watched = tuple(
            relative_path(path) for path in _strings(item.get("watch", []), "watch roots")
        )
        if not watched:
            raise ProjectError(message("builds.{service}.watch must not be empty", service=service))
        names = [
            str(item.get(key, "")) for key in ("source_task", "resource_task", "structural_task")
        ]
        for task in names:
            if task not in tasks:
                raise ProjectError(
                    message(
                        "builds.{service} references unknown task: {task}",
                        service=service,
                        task=task,
                    )
                )
        builds.append(
            BuildSpec(
                service,
                watched,
                *names,
                _strings(
                    item.get(
                        "extensions", list(BuildSpec.__dataclass_fields__["extensions"].default)
                    ),
                    "extensions",
                ),
                _strings(
                    item.get(
                        "resources", list(BuildSpec.__dataclass_fields__["resources"].default)
                    ),
                    "resources",
                ),
                _strings(
                    item.get(
                        "structural", list(BuildSpec.__dataclass_fields__["structural"].default)
                    ),
                    "structural patterns",
                ),
            )
        )
    branch = raw.get("rebuild_on_branch", True)
    if not isinstance(branch, bool):
        raise ProjectError(message("rebuild_on_branch must be boolean"))
    spec = ProjectSpec(
        name,
        tasks,
        services,
        tuple(builds),
        toolchain,
        branch,
        _strings(raw.get("sync_exclude", []), "sync exclusions"),
        raw,
        _strings(raw.get("sync_include", []), "sync inclusions"),
    )
    spec.service_order()
    spec.task_order()
    return spec


def load_project(source: Path, platform: str) -> ProjectSpec:
    with (source / CONFIG_NAME).open("rb") as stream:
        return parse_project(tomllib.load(stream), platform)


def render_toml(raw: dict[str, Any]) -> str:
    """Serialize the declaration subset without an extra runtime dependency."""
    lines: list[str] = []

    def scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, int | float):
            return str(value)
        if isinstance(value, list | tuple):
            return "[" + ", ".join(scalar(item) for item in value) + "]"
        raise ProjectError(
            message("cannot serialize configuration value: {value}", value=repr(value))
        )

    def table(value: dict[str, Any], path: tuple[str, ...]) -> None:
        if path:
            lines.extend(["", "[" + ".".join(json.dumps(part) for part in path) + "]"])
        for key, item in value.items():
            if not isinstance(item, dict):
                lines.append(f"{json.dumps(key)} = {scalar(item)}")
        for key, item in value.items():
            if isinstance(item, dict):
                table(item, (*path, key))

    table(raw, ())
    return "\n".join(lines) + "\n"
