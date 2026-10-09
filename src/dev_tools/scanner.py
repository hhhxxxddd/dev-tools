from __future__ import annotations

import json
import os
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from .i18n import message, t
from .metadata import (
    TOOL_ORDER,
    MetadataError,
    _relative,
    _scan_gradle,
    _scan_maven_wrapper,
    _scan_mise,
    _scan_package,
    _scan_pom,
    _scan_python_project,
    _scan_sdkman,
    _scan_tool_versions,
    _scan_uv_lock,
    _scan_version_file,
)
from .project_models import (
    Conflict,
    Diagnostic,
    Evidence,
    ScanResult,
    UnresolvedRequirement,
    Wrapper,
)

IGNORED_DIRECTORIES = {
    ".codegraph",
    ".dev-tools",
    ".git",
    ".gradle",
    ".idea",
    ".mvn-cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "host-mise",
    "node_modules",
    "out",
    "target",
    "venv",
}
KNOWN_FILES = {
    ".java-version",
    ".mise.toml",
    ".node-version",
    ".nvmrc",
    ".python-version",
    ".sdkmanrc",
    ".tool-versions",
    "build.gradle",
    "build.gradle.kts",
    "mise.toml",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "pom.xml",
    "pyproject.toml",
    "uv.lock",
}


def _project_files(root: Path, max_depth: int = 4) -> list[Path]:
    result: list[Path] = []
    for current, directories, files in os.walk(root):
        base = Path(current)
        depth = len(base.relative_to(root).parts)
        directories[:] = [
            name
            for name in directories
            if name not in IGNORED_DIRECTORIES and not name.startswith(".cache")
        ]
        if depth >= max_depth:
            directories.clear()
        for name in files:
            if name in KNOWN_FILES or name == "maven-wrapper.properties":
                result.append(base / name)
    return sorted(result)


def _select(evidence: list[Evidence]) -> tuple[dict[str, Evidence], list[Conflict], list[str]]:
    tools: dict[str, Evidence] = {}
    conflicts: list[Conflict] = []
    warnings: list[str] = []
    for name in TOOL_ORDER:
        candidates = [item for item in evidence if item.tool == name]
        if not candidates:
            continue
        top_priority = max(item.priority for item in candidates)
        top = [item for item in candidates if item.priority == top_priority]
        if any(not item.version for item in top):
            continue
        versions: dict[str, list[str]] = {}
        for item in top:
            versions.setdefault(item.version, []).append(item.source)
        if len(versions) > 1:
            conflicts.append(Conflict(name, versions))
            continue
        selected = min(top, key=lambda item: item.source)
        tools[name] = selected
        lower = sorted(
            {item.version or item.raw for item in candidates if item.version != selected.version}
        )
        if lower:
            warnings.append(
                message(
                    "{name}: selected {version} from {value}; lower-priority declarations also suggest {lower}",
                    name=name,
                    version=selected.version,
                    value=selected.source,
                    lower=", ".join(lower),
                )
            )
    return tools, conflicts, warnings


def scan_project(path: str | Path) -> ScanResult:
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(message("project path is not a directory: {root}", root=root))
    files = _project_files(root)
    diagnostics: list[Diagnostic] = []
    root_tools: tuple[str, ...] = ()
    evidence: list[Evidence] = []
    wrappers: dict[str, Wrapper] = {}
    requirements: dict[str, list[str]] = {}

    def require(name: str, file: Path) -> None:
        requirements.setdefault(name, []).append(_relative(file, root))

    existing = next(
        (
            candidate
            for candidate in (root / "mise.toml", root / ".mise.toml")
            if candidate.is_file()
        ),
        None,
    )
    for file in files:
        try:
            if file.name in {"mise.toml", ".mise.toml"}:
                declared = _scan_mise(file, root, evidence)
                if file == existing:
                    root_tools = declared
            elif file.name == ".tool-versions":
                _scan_tool_versions(file, root, evidence)
            elif file.name in {".java-version", ".node-version", ".nvmrc", ".python-version"}:
                _scan_version_file(file, root, evidence)
            elif file.name == ".sdkmanrc":
                _scan_sdkman(file, root, evidence)
            elif file.name == "package.json":
                manager = _scan_package(file, root, evidence)
                require("node", file)
                if manager in {"pnpm", "yarn", "bun"}:
                    require(manager, file)
            elif file.name == "pom.xml":
                _scan_pom(file, root, evidence)
                require("java", file)
                require("maven", file)
            elif file.name in {"build.gradle", "build.gradle.kts"}:
                _scan_gradle(file, root, evidence)
                require("java", file)
            elif file.name == "pyproject.toml":
                _scan_python_project(file, root, evidence)
                require("python", file)
            elif file.name == "uv.lock":
                _scan_uv_lock(file, root, evidence)
                require("python", file)
                require("uv", file)
            elif file.name in {"pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb"}:
                require(
                    {
                        "pnpm-lock.yaml": "pnpm",
                        "yarn.lock": "yarn",
                        "bun.lock": "bun",
                        "bun.lockb": "bun",
                    }[file.name],
                    file,
                )
            elif file.name == "maven-wrapper.properties":
                _scan_maven_wrapper(file, root, wrappers)
        except (OSError, UnicodeError) as exc:
            diagnostics.append(Diagnostic(_relative(file, root), "read-error", str(exc)))
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, ET.ParseError) as exc:
            diagnostics.append(Diagnostic(_relative(file, root), "parse-error", str(exc)))
        except MetadataError as exc:
            diagnostics.append(Diagnostic(_relative(file, root), "invalid-metadata", exc.args[0]))
    tools, conflicts, warnings = _select(evidence)
    if "maven" in wrappers:
        conflicts = [conflict for conflict in conflicts if conflict.tool != "maven"]
        if "maven" in tools:
            warnings.append(
                message(
                    "maven: Maven Wrapper owns the project Maven version; the mise Maven declaration is optional"
                )
            )
        tools.pop("maven", None)
    unresolved: list[UnresolvedRequirement] = []
    for name in TOOL_ORDER:
        candidates = [item for item in evidence if item.tool == name]
        priority = max((item.priority for item in candidates), default=0)
        if name in wrappers:
            continue
        for item in candidates:
            if item.priority == priority and not item.version:
                unresolved.append(
                    UnresolvedRequirement(
                        name,
                        item.source,
                        item.raw,
                        message("cannot safely infer a version from this declaration"),
                    )
                )
        if name in requirements and name not in tools and name not in wrappers and not candidates:
            unresolved.append(
                UnresolvedRequirement(
                    name,
                    ", ".join(requirements[name]),
                    "",
                    message("required tool has no version declaration"),
                )
            )
    return ScanResult(
        root,
        existing,
        tools,
        evidence,
        conflicts,
        warnings,
        wrappers,
        requirements,
        unresolved,
        diagnostics,
        root_tools,
    )


def render_mise(result: ScanResult) -> str:
    lines = [
        t("# 由 dev-tools init 生成。"),
        t("# 请检查此文件，并按需随项目提交。"),
        "",
        "[tools]",
    ]
    for name in TOOL_ORDER:
        item = result.tools.get(name)
        if item is None:
            continue
        encoded = json.dumps(item.version, ensure_ascii=False)
        lines.append(f"{name} = {encoded} # {item.source}")
    return "\n".join(lines) + "\n"
