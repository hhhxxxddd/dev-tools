from __future__ import annotations

import json
import os
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TOOL_ORDER = ("java", "maven", "node", "npm", "pnpm", "yarn", "python", "uv")
IGNORED_DIRECTORIES = {
    ".git",
    ".gradle",
    ".idea",
    ".mvn-cache",
    ".next",
    ".tox",
    ".venv",
    "build",
    "dist",
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
    "pom.xml",
    "pyproject.toml",
    "uv.lock",
}
SDKMAN_JAVA_VENDORS = {
    "amzn": "corretto",
    "librca": "liberica",
    "ms": "microsoft",
    "oracle": "oracle",
    "sem": "semeru",
    "tem": "temurin",
    "zulu": "zulu",
}


@dataclass(frozen=True)
class Evidence:
    tool: str
    version: str
    source: str
    priority: int
    confidence: str
    raw: str


@dataclass(frozen=True)
class Conflict:
    tool: str
    versions: dict[str, list[str]]


@dataclass
class ScanResult:
    root: Path
    existing_config: Path | None
    tools: dict[str, Evidence]
    evidence: list[Evidence]
    conflicts: list[Conflict]
    warnings: list[str]
    wrappers: dict[str, dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "existing_config": str(self.existing_config) if self.existing_config else None,
            "tools": {
                name: {
                    "version": item.version,
                    "source": item.source,
                    "confidence": item.confidence,
                }
                for name, item in self.tools.items()
            },
            "evidence": [asdict(item) for item in self.evidence],
            "conflicts": [asdict(item) for item in self.conflicts],
            "warnings": self.warnings,
            "wrappers": self.wrappers,
        }


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            value = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _tool_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        return str(value["version"])
    return None


def _numeric_version(raw: str) -> str | None:
    match = re.search(r"(?<!\d)v?(\d+(?:\.\d+){0,2})", raw)
    return match.group(1) if match else None


def _normalize_java(raw: str) -> str | None:
    value = raw.strip().strip("\"'")
    if not value or value.startswith("${"):
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9_-]*-\d", value):
        return value
    sdkman = re.fullmatch(r"(\d+(?:\.\d+){0,2})-([A-Za-z0-9]+)", value)
    if sdkman:
        vendor = SDKMAN_JAVA_VENDORS.get(sdkman.group(2).lower())
        return f"{vendor}-{sdkman.group(1)}" if vendor else None
    numeric = _numeric_version(value)
    if numeric is None:
        return value if value in {"latest", "lts"} else None
    if numeric.startswith("1."):
        numeric = numeric.split(".", 1)[1]
    if any(token in value for token in (">", "<", "^", "~", "*", "x", "X", "|")):
        numeric = numeric.split(".", 1)[0]
    return f"temurin-{numeric}"


def _normalize_version(tool: str, raw: str) -> str | None:
    if tool == "java":
        return _normalize_java(raw)
    value = raw.strip().strip("\"'").removeprefix("v")
    if not value:
        return None
    if value in {"latest", "lts", "system"} or value.startswith(("lts/", "ref:", "path:")):
        return value
    exact = re.fullmatch(r"(\d+(?:\.\d+){0,2})(?:\+.*)?", value)
    if exact:
        return exact.group(1)
    numeric = _numeric_version(value)
    if numeric is None:
        return None
    parts = numeric.split(".")
    if tool in {"python", "uv"} and len(parts) >= 2:
        return ".".join(parts[:2])
    return parts[0]


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
    version = _normalize_version(tool, raw_value)
    if version:
        values.append(Evidence(tool, version, source, priority, confidence, raw_value))


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


def _scan_mise(path: Path, root: Path, evidence: list[Evidence]) -> None:
    tools = _read_toml(path).get("tools", {})
    if not isinstance(tools, dict):
        return
    source = _relative(path, root)
    for name in TOOL_ORDER:
        value = _tool_value(tools.get(name))
        if value:
            _add(evidence, name, value, source, 100, "exact")


def _scan_tool_versions(path: Path, root: Path, evidence: list[Evidence]) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
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
    try:
        raw = path.read_text(encoding="utf-8").splitlines()[0].split()[0]
    except (OSError, UnicodeDecodeError, IndexError):
        return
    _add(evidence, tool, raw, _relative(path, root), 90, "exact")


def _scan_sdkman(path: Path, root: Path, evidence: list[Evidence]) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#") or "=" not in value:
            continue
        name, version = (part.strip() for part in value.split("=", 1))
        if name in {"java", "maven"}:
            _add(evidence, name, version, _relative(path, root), 90, "exact")


def _scan_package(path: Path, root: Path, evidence: list[Evidence]) -> None:
    value = _read_json(path)
    source = _relative(path, root)
    package_manager = value.get("packageManager")
    if isinstance(package_manager, str) and "@" in package_manager:
        name, version = package_manager.rsplit("@", 1)
        if name in {"npm", "pnpm", "yarn"}:
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
                if name in {"node", "npm", "pnpm", "yarn"}:
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


def _resolve_property(raw: str, properties: dict[str, str]) -> str:
    match = re.fullmatch(r"\$\{([^}]+)\}", raw.strip())
    return properties.get(match.group(1), raw) if match else raw


def _scan_pom(path: Path, root: Path, evidence: list[Evidence]) -> None:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return
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
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
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


def _scan_maven_wrapper(path: Path, root: Path, wrappers: dict[str, dict[str, str]]) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    match = re.search(r"apache-maven-([0-9][0-9.]+)-bin\.(?:zip|tar\.gz)", content)
    wrappers["maven"] = {
        "version": match.group(1) if match else "managed",
        "source": _relative(path, root),
    }


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
        versions: dict[str, list[str]] = {}
        for item in top:
            versions.setdefault(item.version, []).append(item.source)
        if len(versions) > 1:
            conflicts.append(Conflict(name, versions))
            continue
        selected = min(top, key=lambda item: item.source)
        tools[name] = selected
        lower = sorted({item.version for item in candidates if item.version != selected.version})
        if lower:
            warnings.append(
                f"{name}: selected {selected.version} from {selected.source}; "
                f"lower-priority declarations also suggest {', '.join(lower)}"
            )
    return tools, conflicts, warnings


def scan_project(path: str | Path) -> ScanResult:
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project path is not a directory: {root}")
    files = _project_files(root)
    evidence: list[Evidence] = []
    wrappers: dict[str, dict[str, str]] = {}
    existing = next(
        (
            candidate
            for candidate in (root / "mise.toml", root / ".mise.toml")
            if candidate.is_file()
        ),
        None,
    )
    for file in files:
        if file.name in {"mise.toml", ".mise.toml"}:
            _scan_mise(file, root, evidence)
        elif file.name == ".tool-versions":
            _scan_tool_versions(file, root, evidence)
        elif file.name in {".java-version", ".node-version", ".nvmrc", ".python-version"}:
            _scan_version_file(file, root, evidence)
        elif file.name == ".sdkmanrc":
            _scan_sdkman(file, root, evidence)
        elif file.name == "package.json":
            _scan_package(file, root, evidence)
        elif file.name == "pom.xml":
            _scan_pom(file, root, evidence)
        elif file.name in {"build.gradle", "build.gradle.kts"}:
            _scan_gradle(file, root, evidence)
        elif file.name == "pyproject.toml":
            _scan_python_project(file, root, evidence)
        elif file.name == "uv.lock":
            _scan_uv_lock(file, root, evidence)
        elif file.name == "maven-wrapper.properties":
            _scan_maven_wrapper(file, root, wrappers)
    tools, conflicts, warnings = _select(evidence)
    if "maven" in wrappers:
        conflicts = [conflict for conflict in conflicts if conflict.tool != "maven"]
        if "maven" in tools:
            warnings.append(
                "maven: Maven Wrapper owns the project Maven version; "
                "the mise Maven declaration is optional"
            )
        tools.pop("maven", None)
    return ScanResult(root, existing, tools, evidence, conflicts, warnings, wrappers)


def render_mise(result: ScanResult) -> str:
    lines = [
        "# Generated by dev-tools project init.",
        "# Review this file and commit it with the project when appropriate.",
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
