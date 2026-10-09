from __future__ import annotations

import fnmatch
import json
import os
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..i18n import message
from ..scanner import IGNORED_DIRECTORIES
from .models import ProjectError


def _find(source: Path, names: set[str], max_depth: int = 4) -> list[Path]:
    result: list[Path] = []
    for current, directories, files in os.walk(source):
        current_path = Path(current)
        depth = len(current_path.relative_to(source).parts)
        directories[:] = [
            name
            for name in directories
            if name not in IGNORED_DIRECTORIES
            and not name.startswith(".cache")
            and depth < max_depth
            and not (current_path / name).is_symlink()
        ]
        for name in files:
            if name in names and not (current_path / name).is_symlink():
                result.append(current_path / name)
    return sorted(result, key=lambda path: (len(path.relative_to(source).parts), str(path)))


def _package_details(path: Path, boundary: Path) -> tuple[str | None, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(message("cannot inspect {path}: {exc}", path=path, exc=exc)) from exc
    dependencies: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies"):
        item = value.get(key, {})
        if isinstance(item, dict):
            dependencies.update(item)
    if "next" in dependencies:
        framework = "next"
    elif "vite" in dependencies:
        framework = "vite"
    elif "react-scripts" in dependencies:
        framework = "react-scripts"
    elif "react" in dependencies:
        framework = "react"
    else:
        framework = None
    current = path.parent
    while current == boundary or boundary in current.parents:
        package_path = current / "package.json"
        if package_path == path:
            package_value = value
        elif package_path.is_file():
            try:
                package_value = json.loads(package_path.read_text(encoding="utf-8-sig"))
            except OSError, json.JSONDecodeError:
                package_value = {}
        else:
            package_value = {}
        configured = str(package_value.get("packageManager", "")).split("@", 1)[0]
        if configured in {"npm", "pnpm", "yarn", "bun"}:
            return framework, configured
        for filename, manager in (
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("bun.lock", "bun"),
            ("bun.lockb", "bun"),
            ("package-lock.json", "npm"),
        ):
            if (current / filename).exists():
                return framework, manager
        if current == boundary:
            break
        current = current.parent
    return framework, "npm"


def _frontend_port(package_file: Path, framework: str | None) -> int | None:
    root = package_file.parent
    variable = "VITE_PORT" if framework == "vite" else "PORT"
    for path in (root / ".env.local", root / ".env.development", root / ".env"):
        if not path.is_file():
            continue
        match = re.search(
            rf"(?m)^\s*{re.escape(variable)}\s*=\s*['\"]?(\d+)",
            path.read_text(encoding="utf-8-sig", errors="ignore"),
        )
        if match:
            return int(match.group(1))
    if framework != "vite":
        return None
    for path in sorted(root.glob("vite.config.*")):
        content = path.read_text(encoding="utf-8-sig", errors="ignore")
        for pattern in (
            r"['\"]VITE_PORT['\"]\s*,\s*['\"](\d+)['\"]",
            r"\bport\s*:\s*(\d+)",
        ):
            match = re.search(pattern, content)
            if match:
                return int(match.group(1))
    return None


def _xml_root(path: Path) -> ET.Element | None:
    try:
        return ET.parse(path).getroot()
    except OSError, ET.ParseError:
        return None


def _direct_text(root: ET.Element, name: str) -> str | None:
    node = root.find(f"{{*}}{name}")
    if node is None or not node.text or not node.text.strip():
        return None
    return node.text.strip()


def _pom_details(path: Path) -> tuple[str | None, str, set[str]]:
    root = _xml_root(path)
    if root is None:
        return None, "jar", set()
    artifact = _direct_text(root, "artifactId")
    packaging = _direct_text(root, "packaging") or "jar"
    dependencies = {
        node.text.strip()
        for node in root.findall("./{*}dependencies/{*}dependency/{*}artifactId")
        if node.text and node.text.strip()
    }
    return artifact, packaging, dependencies


def _spring_boot_module(poms: list[Path]) -> Path | None:
    candidates = []
    marker = "<artifactId>spring-boot-maven-plugin</artifactId>"
    for path in poms:
        content = path.read_text(encoding="utf-8-sig", errors="ignore")
        if marker not in content:
            continue
        has_application = any((path.parent / "src/main/resources").glob("application*"))
        candidates.append((not has_application, len(path.parts), str(path), path))
    return min(candidates)[-1] if candidates else None


def _spring_boot_properties(pom: Path) -> tuple[int, str | None]:
    resources = pom.parent / "src/main/resources"
    profile = "local" if any(resources.glob("application-local.*")) else None
    names = (
        "application-local.yaml",
        "application-local.yml",
        "application-dev.yaml",
        "application-dev.yml",
        "application.yaml",
        "application.yml",
        "application.properties",
    )
    for name in names:
        path = resources / name
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8-sig", errors="ignore")
        if path.suffix == ".properties":
            match = re.search(r"(?m)^\s*server\.port\s*=\s*(\d+)", content)
        else:
            match = re.search(r"(?ms)^server:\s*\n(?:[ \t]+.*\n)*?[ \t]+port:\s*(\d+)", content)
        if match:
            return int(match.group(1)), profile
    return 8080, profile


def _spring_boot_version(poms: list[Path], application: Path) -> str | None:
    relevant = [path for path in poms if path.parent in application.parents]
    properties: dict[str, str] = {}
    roots = []
    for path in relevant:
        root = _xml_root(path)
        if root is None:
            continue
        roots.insert(0, root)
        for node in root.findall("./{*}properties/*"):
            if node.text and node.text.strip():
                properties[node.tag.rsplit("}", 1)[-1]] = node.text.strip()
    for root in roots:
        candidates = []
        for node in root.findall(".//{*}plugin") + root.findall("./{*}parent"):
            if _direct_text(node, "artifactId") in {
                "spring-boot-maven-plugin",
                "spring-boot-starter-parent",
            }:
                candidates.append(_direct_text(node, "version"))
        for node in root.findall("./{*}dependencyManagement/{*}dependencies/{*}dependency"):
            if _direct_text(node, "artifactId") == "spring-boot-dependencies":
                candidates.append(_direct_text(node, "version"))
        candidates.extend(
            properties.get(name) for name in ("spring.boot.version", "spring-boot.version")
        )
        for candidate in candidates:
            for _ in range(8):
                match = re.fullmatch(r"\$\{([^}]+)\}", candidate or "")
                if not match:
                    break
                candidate = properties.get(match.group(1))
            if candidate and re.fullmatch(r"\d+\.\d+\.\d+(?:[.-][A-Za-z0-9.-]+)?", candidate):
                return candidate
    return None


def _package_root(package: Path, boundary: Path, manager: str) -> tuple[Path, bool]:
    names = {
        "npm": ("package-lock.json", "npm-shrinkwrap.json"),
        "pnpm": ("pnpm-lock.yaml",),
        "yarn": ("yarn.lock",),
        "bun": ("bun.lock", "bun.lockb"),
    }[manager]
    selected = package.parent
    current = selected
    while current == boundary or boundary in current.parents:
        manifest = current / "package.json"
        try:
            value = (
                json.loads(manifest.read_text(encoding="utf-8-sig")) if manifest.is_file() else {}
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(
                message("cannot inspect {path}: {exc}", path=manifest, exc=exc)
            ) from exc
        if not isinstance(value, dict):
            raise ProjectError(
                message("package manifest must be an object: {manifest}", manifest=manifest)
            )
        if any((current / name).is_file() for name in names):
            selected = current
            break
        if value.get("workspaces") or (current / "pnpm-workspace.yaml").is_file():
            selected = current
        if current == boundary:
            break
        current = current.parent
    classic = False
    if manager == "yarn":
        lock = selected / "yarn.lock"
        classic = lock.is_file() and "# yarn lockfile v1" in lock.read_text(encoding="utf-8-sig")
        manifest = selected / "package.json"
        if manifest.is_file():
            version = str(
                json.loads(manifest.read_text(encoding="utf-8-sig")).get("packageManager", "")
            )
            if version.startswith("yarn@"):
                classic = version.split("@", 1)[1].split(".", 1)[0] == "1"
    return selected, classic


def _python_root(project: Path, boundary: Path) -> tuple[Path, bool]:
    selected = project.parent
    for current in selected.parents:
        if current != boundary and boundary not in current.parents:
            break
        manifest = current / "pyproject.toml"
        if not manifest.is_file():
            continue
        try:
            value = tomllib.loads(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ProjectError(
                message("cannot inspect {path}: {exc}", path=manifest, exc=exc)
            ) from exc
        tool = value.get("tool", {})
        uv = tool.get("uv", {}) if isinstance(tool, dict) else {}
        workspace = uv.get("workspace", {}) if isinstance(uv, dict) else {}
        if not isinstance(workspace, dict):
            continue
        relative = selected.relative_to(current).as_posix()
        members = workspace.get("members", [])
        excluded = workspace.get("exclude", [])
        if (
            isinstance(members, list)
            and isinstance(excluded, list)
            and any(
                isinstance(pattern, str) and fnmatch.fnmatchcase(relative, pattern)
                for pattern in members
            )
            and not any(
                isinstance(pattern, str) and fnmatch.fnmatchcase(relative, pattern)
                for pattern in excluded
            )
        ):
            return current, True
    return selected, (selected / "uv.lock").is_file()


def _maven_dependency_paths(
    poms: list[Path], boot_pom: Path, source: Path
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    metadata = {path: _pom_details(path) for path in poms}
    by_artifact = {
        artifact: path
        for path, (artifact, _packaging, _dependencies) in metadata.items()
        if artifact
    }
    selected: set[Path] = {boot_pom}
    pending = [boot_pom]
    while pending:
        current = pending.pop()
        for artifact in metadata[current][2]:
            dependency = by_artifact.get(artifact)
            if dependency is not None and dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    source_roots = tuple(
        path.parent / "src/main"
        for path in sorted(selected, key=str)
        if (path.parent / "src/main").is_dir()
    )
    classpath = tuple(
        path.parent / "target/classes"
        for path in sorted(selected - {boot_pom}, key=str)
        if metadata[path][1] != "pom" and (path.parent / "src/main").is_dir()
    )
    return (
        tuple(path.relative_to(source) for path in source_roots),
        tuple(path.relative_to(source) for path in classpath),
    )
