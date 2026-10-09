from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .i18n import message
from .project_models import Evidence, Wrapper
from .versions import normalize_version

TOOL_ORDER = ("java", "maven", "node", "npm", "pnpm", "yarn", "bun", "python", "uv")


class MetadataError(ValueError):
    pass


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise MetadataError(message("expected a JSON object"))
    return value


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _tool_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        return str(value["version"])
    return None


def _add(
    values: list[Evidence],
    tool: str,
    raw: Any,
    source: str,
    priority: int,
    confidence: str,
) -> None:
    if not isinstance(raw, (str, int, float)):
        return
    raw_value = str(raw).strip()
    version = normalize_version(tool, raw_value)
    if raw_value:
        values.append(Evidence(tool, version, source, priority, confidence, raw_value))


def _scan_mise(path: Path, root: Path, evidence: list[Evidence]) -> tuple[str, ...]:
    tools = _read_toml(path).get("tools", {})
    if not isinstance(tools, dict):
        raise MetadataError(message("mise tools must be a table"))
    source = _relative(path, root)
    for name in TOOL_ORDER:
        if name not in tools:
            continue
        value = _tool_value(tools.get(name))
        if not value or not value.strip():
            raise MetadataError(
                message("mise tools.{name} must declare one nonempty version string", name=name)
            )
        _add(evidence, name, value, source, 100, "exact")
    return tuple(tools)


def _scan_tool_versions(path: Path, root: Path, evidence: list[Evidence]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        value = line.split("#", 1)[0].strip()
        if not value:
            continue
        fields = value.split()
        if len(fields) >= 2 and fields[0] in TOOL_ORDER:
            _add(evidence, fields[0], fields[1], _relative(path, root), 95, "exact")


def _scan_version_file(path: Path, root: Path, evidence: list[Evidence]) -> None:
    mapping = {
        ".java-version": "java",
        ".node-version": "node",
        ".nvmrc": "node",
        ".python-version": "python",
    }
    tool = mapping[path.name]
    fields = path.read_text(encoding="utf-8").split()
    if not fields:
        raise MetadataError(message("empty version declaration"))
    raw = fields[0]
    _add(evidence, tool, raw, _relative(path, root), 90, "exact")


def _scan_sdkman(path: Path, root: Path, evidence: list[Evidence]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#") or "=" not in value:
            continue
        name, version = (part.strip() for part in value.split("=", 1))
        if name in {"java", "maven"}:
            _add(evidence, name, version, _relative(path, root), 90, "exact")


def _scan_package(path: Path, root: Path, evidence: list[Evidence]) -> str:
    value = _read_json(path)
    source = _relative(path, root)
    package_manager = value.get("packageManager")
    if isinstance(package_manager, str) and "@" in package_manager:
        name, version = package_manager.rsplit("@", 1)
        if name in {"npm", "pnpm", "yarn", "bun"}:
            _add(evidence, name, version, f"{source}:packageManager", 92, "exact")
    volta = value.get("volta", {})
    if isinstance(volta, dict):
        for name in ("node", "npm", "pnpm", "yarn"):
            _add(evidence, name, volta.get(name), f"{source}:volta.{name}", 92, "exact")
    dev_engines = value.get("devEngines", {})
    if isinstance(dev_engines, dict):
        for field in ("runtime", "packageManager"):
            entry = dev_engines.get(field)
            if isinstance(entry, dict):
                name = entry.get("name")
                if name in {"node", "npm", "pnpm", "yarn", "bun"}:
                    _add(
                        evidence,
                        str(name),
                        entry.get("version"),
                        f"{source}:devEngines.{field}",
                        88,
                        "exact",
                    )
    engines = value.get("engines", {})
    if isinstance(engines, dict):
        for name in ("node", "npm", "pnpm", "yarn"):
            _add(evidence, name, engines.get(name), f"{source}:engines.{name}", 70, "range")
    return package_manager.split("@", 1)[0] if isinstance(package_manager, str) else ""


def _resolve_property(raw: str, properties: dict[str, str]) -> str:
    match = re.fullmatch(r"\$\{([^}]+)\}", raw.strip())
    return properties.get(match.group(1), raw) if match else raw


def _scan_pom(path: Path, root: Path, evidence: list[Evidence]) -> None:
    tree = ET.parse(path)
    properties: dict[str, str] = {}
    for element in tree.iter():
        if _local_name(element.tag) != "properties":
            continue
        for child in element:
            if child.text and child.text.strip():
                properties[_local_name(child.tag)] = child.text.strip()
    source = _relative(path, root)
    for name in ("maven.compiler.release", "java.version", "maven.compiler.source"):
        if name in properties:
            raw = _resolve_property(properties[name], properties)
            before = len(evidence)
            _add(evidence, "java", raw, f"{source}:{name}", 80, "compatible")
            if len(evidence) > before:
                return
    for element in tree.iter():
        if _local_name(element.tag) not in {"release", "source"}:
            continue
        if element.text and element.text.strip():
            raw = _resolve_property(element.text, properties)
            before = len(evidence)
            _add(evidence, "java", raw, f"{source}:maven-compiler-plugin", 75, "compatible")
            if len(evidence) > before:
                return


def _scan_gradle(path: Path, root: Path, evidence: list[Evidence]) -> None:
    content = path.read_text(encoding="utf-8")
    patterns = (
        r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)",
        r"jvmToolchain\(\s*(\d+)\s*\)",
        r"JavaVersion\.VERSION_(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            _add(
                evidence,
                "java",
                match.group(1),
                f"{_relative(path, root)}:toolchain",
                80,
                "compatible",
            )
            return


def _scan_python_project(path: Path, root: Path, evidence: list[Evidence]) -> None:
    value = _read_toml(path)
    source = _relative(path, root)
    project = value.get("project", {})
    if isinstance(project, dict):
        _add(
            evidence,
            "python",
            project.get("requires-python"),
            f"{source}:project.requires-python",
            70,
            "range",
        )
    poetry = value.get("tool", {})
    if isinstance(poetry, dict):
        poetry = poetry.get("poetry", {})
    if isinstance(poetry, dict):
        dependencies = poetry.get("dependencies", {})
        if isinstance(dependencies, dict):
            _add(
                evidence,
                "python",
                dependencies.get("python"),
                f"{source}:tool.poetry.dependencies.python",
                70,
                "range",
            )
    tool = value.get("tool", {})
    uv = tool.get("uv", {}) if isinstance(tool, dict) else {}
    if isinstance(uv, dict):
        _add(
            evidence,
            "uv",
            uv.get("required-version"),
            f"{source}:tool.uv.required-version",
            85,
            "exact",
        )


def _scan_uv_lock(path: Path, root: Path, evidence: list[Evidence]) -> None:
    value = _read_toml(path)
    _add(
        evidence,
        "python",
        value.get("requires-python"),
        f"{_relative(path, root)}:requires-python",
        72,
        "range",
    )


def _scan_maven_wrapper(path: Path, root: Path, wrappers: dict[str, Wrapper]) -> None:
    content = path.read_text(encoding="utf-8")
    match = re.search(r"apache-maven-([0-9][0-9.]+)-bin\.(?:zip|tar\.gz)", content)
    wrappers["maven"] = Wrapper(match.group(1) if match else "managed", _relative(path, root))
