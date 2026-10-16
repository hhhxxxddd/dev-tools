"""Read-only project boundaries and a metadata snapshot for one inspection operation."""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import stat
import tomllib
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .frameworks import related_scopes
from .i18n import message
from .project_models import Diagnostic

IGNORED_DIRECTORIES = frozenset(
    {
        ".codegraph",
        ".dev-tools",
        ".git",
        ".gradle",
        ".idea",
        ".local",
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
)
METADATA_NAMES = frozenset(
    {
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
        "maven-wrapper.properties",
        "dev-tools.toml",
        "pnpm-workspace.yaml",
        "package-lock.json",
        "npm-shrinkwrap.json",
        ".npmrc",
        ".yarnrc.yml",
        "uv.toml",
        "requirements.txt",
        "compose.yaml",
        "compose.yml",
        "docker-compose.yaml",
        "docker-compose.yml",
        "mvnw",
        "mvnw.cmd",
        "jvm.config",
        "maven.config",
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain",
        "rust-toolchain.toml",
    }
)
_current: ContextVar[Inspection | None] = ContextVar("dev_tools_inspection", default=None)


class InspectionError(ValueError):
    pass


def _relative(value: str, *, pattern: bool = False) -> str:
    if not isinstance(value, str):
        raise InspectionError(
            message("scan paths must stay inside the project: {value}", value=repr(value))
        )
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not value
        or "\x00" in value
        or ":" in value
        or path.is_absolute()
        or PureWindowsPath(value).drive
        or ".." in path.parts
        or (not pattern and any(char in normalized for char in "*?[]"))
        or (pattern and normalized.startswith("!"))
    ):
        raise InspectionError(
            message("scan paths must stay inside the project: {value}", value=value)
        )
    return path.as_posix()


def _strings(value, label: str, *, pattern: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise InspectionError(message("{label} must be an array of nonempty strings", label=label))
    return tuple(dict.fromkeys(_relative(item, pattern=pattern) for item in value))


@dataclass(frozen=True)
class ScanPolicy:
    roots: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    max_depth: int = 4

    @classmethod
    def parse(cls, value) -> ScanPolicy:
        if not isinstance(value, dict):
            raise InspectionError(message("{label} must be a table", label="scan"))
        unknown = set(value) - {"roots", "exclude", "max_depth"}
        if unknown:
            raise InspectionError(
                message("scan: unknown fields: {value}", value=", ".join(sorted(unknown)))
            )
        depth = value.get("max_depth", 4)
        if type(depth) is not int or not 0 <= depth <= 32:
            raise InspectionError(message("scan.max_depth must be an integer from 0 to 32"))
        return cls(
            _strings(value["roots"], "scan.roots") if "roots" in value else None,
            _strings(value.get("exclude", []), "scan.exclude", pattern=True),
            depth,
        )

    def excluded(self, relative: str) -> bool:
        parts = PurePosixPath(relative).parts
        for index in range(1, len(parts) + 1):
            prefix = PurePosixPath(*parts[:index])
            for pattern in self.exclude:
                if prefix.full_match(pattern, case_sensitive=True) or (
                    pattern.endswith("/**") and prefix.full_match(pattern[:-3], case_sensitive=True)
                ):
                    return True
        return False


def read_bytes(path: Path) -> bytes:
    active = _current.get()
    return (
        active.bytes(path)
        if active is not None and path.is_relative_to(active.root)
        else path.read_bytes()
    )


def read_text(path: Path, *, encoding="utf-8", errors="strict") -> str:
    return read_bytes(path).decode(encoding, errors)


def read_json(path: Path):
    active = _current.get()
    return (
        active.document(path, "json")
        if active is not None and path.is_relative_to(active.root)
        else json.loads(read_text(path, encoding="utf-8-sig"))
    )


def read_toml(path: Path) -> dict:
    active = _current.get()
    return (
        active.document(path, "toml")
        if active is not None and path.is_relative_to(active.root)
        else tomllib.loads(read_text(path, encoding="utf-8-sig"))
    )


def read_xml(path: Path) -> ET.Element:
    active = _current.get()
    return (
        active.document(path, "xml")
        if active is not None and path.is_relative_to(active.root)
        else ET.fromstring(read_bytes(path))
    )


def current_inspection(root: Path) -> Inspection | None:
    active = _current.get()
    return active if active is not None and root.resolve() == active.root else None


class Inspection:
    def __init__(
        self, root: Path, *, project: dict | None = None, workdirs=None, platform: str | None = None
    ):
        self.root = root.resolve(strict=True)
        self._bytes: dict[Path, bytes] = {}
        self._documents: dict[tuple[Path, str], object] = {}
        self.files: set[Path] = set()
        self.scopes: dict[Path, set[str]] = {}
        self.truncated: set[str] = set()
        self.diagnostics: list[Diagnostic] = []
        self._expanded: set[Path] = set()
        self._direct_seen: set[Path] = set()
        self._walked: set[tuple[Path, int]] = set()
        self.policy = ScanPolicy()
        with self.activate():
            try:
                config = self.root / "dev-tools.toml"
                if not self.safe(config):
                    raise InspectionError(
                        message("unsafe metadata path: {path}", path="dev-tools.toml")
                    )
                if project is None:
                    project = read_toml(config) if self.safe(config) and config.is_file() else {}
                self.policy = ScanPolicy.parse(project.get("scan", {}))
                self.scope(self.root, "root")
                directories = (
                    list(workdirs) if workdirs is not None else self._workdirs(project, platform)
                )
                for relative in directories:
                    self.scope(self.root / _relative(relative), "workflow")
                roots = self.policy.roots
                if roots is None and (not project or project.get("discovery") == "auto"):
                    roots = (".",)
                for relative in roots or ():
                    directory = self.root / relative
                    if not self.safe(directory) or not directory.is_dir():
                        raise InspectionError(
                            message("scan root is not a safe directory: {path}", path=relative)
                        )
                    self.scope(
                        directory, "scan.roots" if self.policy.roots is not None else "discovery"
                    )
                    self.walk(directory, self.policy.max_depth)
            except (OSError, UnicodeError, ValueError, ET.ParseError) as exc:
                self.diagnostics.append(
                    Diagnostic(
                        "dev-tools.toml", "scan-policy", exc.args[0] if exc.args else str(exc)
                    )
                )

    @contextmanager
    def activate(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    def bytes(self, path: Path) -> bytes:
        if path not in self._bytes:
            if not self.safe(path) or not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                raise InspectionError(message("unsafe metadata path: {path}", path=path))
            self._bytes[path] = path.read_bytes()
        return self._bytes[path]

    def document(self, path: Path, kind: str):
        key = path, kind
        if key not in self._documents:
            content = self.bytes(path)
            self._documents[key] = (
                ET.fromstring(content)
                if kind == "xml"
                else json.loads(content.decode("utf-8-sig"))
                if kind == "json"
                else tomllib.loads(content.decode("utf-8-sig"))
            )
        return self._documents[key]

    def safe(self, path: Path) -> bool:
        try:
            parts = path.relative_to(self.root).parts
        except ValueError:
            return False
        current = self.root
        for part in parts:
            if part in {"..", ".git"}:
                return False
            current /= part
            if current.is_symlink() or current.is_junction():
                return False
        return True

    @staticmethod
    def _workdirs(project: dict, platform: str | None = None) -> list[str]:
        result = []
        for section in ("tasks", "services"):
            definitions = project.get(section, {})
            if not isinstance(definitions, dict):
                raise InspectionError(message("{label} must be a table", label=section))
            for item in definitions.values():
                if not isinstance(item, dict):
                    raise InspectionError(message("{label} must be a table", label=section))
                overrides = item.get("platforms", {})
                if not isinstance(overrides, dict):
                    raise InspectionError(message("{label} must be a table", label="platforms"))
                variants = [item] if platform is None else []
                for override in (
                    [overrides.get(platform, {})] if platform is not None else overrides.values()
                ):
                    if not isinstance(override, dict):
                        raise InspectionError(message("{label} must be a table", label="platforms"))
                    variants.append(item | override)
                for variant in variants:
                    workdir = variant.get("workdir", ".")
                    result.append(workdir)
                    if isinstance(variant.get("python"), dict):
                        result.append(variant["python"].get("root", workdir))
        return result

    def _excluded_required(self, path: Path) -> None:
        relative = path.relative_to(self.root).as_posix()
        if self.policy.excluded(relative):
            raise InspectionError(
                message(
                    "scan.exclude conflicts with a declared project path: {path}", path=relative
                )
            )

    def scope(self, directory: Path, reason: str) -> None:
        self._excluded_required(directory)
        if not self.safe(directory):
            raise InspectionError(
                message("unsafe metadata path: {path}", path=directory.relative_to(self.root))
            )
        if not directory.is_dir():
            return
        self.scopes.setdefault(directory, set()).add(reason)
        current = directory
        while True:
            self._direct(current)
            if current not in self._expanded:
                self._expanded.add(current)
                self._members(current)
            if current == self.root:
                break
            current = current.parent

    def _direct(self, directory: Path) -> None:
        if directory in self._direct_seen:
            return
        self._direct_seen.add(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name not in METADATA_NAMES:
                    continue
                path = directory / entry.name
                if not self.safe(path):
                    self.diagnostics.append(
                        Diagnostic(
                            path.relative_to(self.root).as_posix(),
                            "unsafe-path",
                            message(
                                "unsafe metadata path: {path}", path=path.relative_to(self.root)
                            ),
                        )
                    )
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                self._excluded_required(path)
                self.files.add(path)
        maven = directory / ".mvn"
        if self.safe(maven) and maven.is_dir():
            self._direct(maven)
        wrapper = directory / ".mvn/wrapper"
        if self.safe(wrapper) and wrapper.is_dir():
            for name in ("maven-wrapper.properties",):
                path = wrapper / name
                if path.is_file() and self.safe(path):
                    self._excluded_required(path)
                    self.files.add(path)

    def walk(self, directory: Path, depth: int) -> None:
        key = directory, depth
        if key in self._walked:
            return
        self._walked.add(key)
        for current, directories, files in os.walk(directory):
            base = Path(current)
            level = len(base.relative_to(directory).parts)
            directories[:] = sorted(
                name
                for name in directories
                if name not in IGNORED_DIRECTORIES
                and not name.startswith(".cache")
                and self.safe(base / name)
                and not self.policy.excluded((base / name).relative_to(self.root).as_posix())
                and not (base / name / ".git").exists()
                and not (base / name / "dev-tools.toml").is_file()
            )
            if level >= depth:
                self.truncated.update(
                    (base / name).relative_to(self.root).as_posix() for name in directories
                )
                directories.clear()
            matches = set(files) & METADATA_NAMES
            for name in matches:
                path = base / name
                if (
                    self.safe(path)
                    and path.is_file()
                    and not self.policy.excluded(path.relative_to(self.root).as_posix())
                ):
                    self.files.add(path)
            if matches:
                self.scopes.setdefault(base, set()).add("discovery")
                if base not in self._expanded:
                    self._expanded.add(base)
                    self._members(base)

    def _members(self, directory: Path) -> None:
        declarations: list[tuple[list[str], list[str]]] = []
        source = directory
        try:
            package = directory / "package.json"
            if package in self.files:
                source = package
                value = read_json(package)
                if isinstance(value, dict):
                    workspace = value.get("workspaces", [])
                    if isinstance(workspace, dict):
                        workspace = workspace.get("packages", [])
                    if not isinstance(workspace, list) or any(
                        not isinstance(item, str) for item in workspace
                    ):
                        raise InspectionError(
                            message("package.json workspaces must be an array of paths")
                        )
                    declarations.append((workspace, []))
            python = directory / "pyproject.toml"
            if python in self.files:
                source = python
                value = read_toml(python)
                tool = value.get("tool", {})
                uv = tool.get("uv", {}) if isinstance(tool, dict) else {}
                workspace = uv.get("workspace", {}) if isinstance(uv, dict) else {}
                if isinstance(workspace, dict):
                    declarations.append(
                        (
                            list(
                                _strings(
                                    workspace.get("members", []),
                                    "uv.workspace.members",
                                    pattern=True,
                                )
                            ),
                            list(
                                _strings(
                                    workspace.get("exclude", []),
                                    "uv.workspace.exclude",
                                    pattern=True,
                                )
                            ),
                        )
                    )
            pnpm = directory / "pnpm-workspace.yaml"
            if pnpm in self.files:
                source = pnpm
                patterns = _pnpm_packages(read_text(pnpm))
                declarations.append(
                    (
                        [item for item in patterns if not item.startswith("!")],
                        [item[1:] for item in patterns if item.startswith("!")],
                    )
                )
            pom = directory / "pom.xml"
            if pom in self.files:
                source = pom
                tree = read_xml(pom)
                modules = [
                    node.text.strip()
                    for node in tree.findall("./{*}modules/{*}module")
                    if node.text and node.text.strip()
                ]
                declarations.append((modules, []))
            cargo = directory / "Cargo.toml"
            if cargo in self.files:
                source = cargo
                workspace = read_toml(cargo).get("workspace", {})
                if not isinstance(workspace, dict):
                    raise InspectionError(
                        message("{label} must be a table", label="Cargo workspace")
                    )
                declarations.append(
                    (
                        list(
                            _strings(
                                workspace.get("members", []),
                                "Cargo.workspace.members",
                                pattern=True,
                            )
                        ),
                        list(
                            _strings(
                                workspace.get("exclude", []),
                                "Cargo.workspace.exclude",
                                pattern=True,
                            )
                        ),
                    )
                )
        except (OSError, UnicodeError, ValueError, ET.ParseError) as exc:
            code = (
                "read-error"
                if isinstance(exc, (OSError, UnicodeError))
                else "parse-error"
                if isinstance(exc, (json.JSONDecodeError, tomllib.TOMLDecodeError, ET.ParseError))
                else "invalid-metadata"
            )
            self.diagnostics.append(
                Diagnostic(
                    source.relative_to(self.root).as_posix(),
                    code,
                    exc.args[0] if exc.args else str(exc),
                )
            )
            return
        for members, excluded in declarations:
            for pattern in members:
                for member in self._glob_directories(directory, _relative(pattern, pattern=True)):
                    relative = member.relative_to(directory).as_posix()
                    if not any(
                        PurePosixPath(relative).full_match(item, case_sensitive=True)
                        for item in excluded
                    ):
                        self.scope(member, "workspace")
        metadata = {name for name in METADATA_NAMES if directory / name in self.files}
        for related in related_scopes(metadata):
            candidate = directory / related.directory
            if self.safe(candidate) and all(
                (candidate / name).is_file() for name in related.metadata
            ):
                self.scope(candidate, related.reason)

    def _glob_directories(self, base: Path, pattern: str):
        parts = PurePosixPath(pattern).parts
        pending = [(base, 0)]
        seen = set()
        while pending:
            directory, index = pending.pop()
            if (directory, index) in seen:
                continue
            seen.add((directory, index))
            if index == len(parts):
                if self.safe(directory) and directory.is_dir():
                    yield directory
                continue
            part = parts[index]
            if not any(char in part for char in "*?["):
                child = directory / part
                if not self.safe(child):
                    raise InspectionError(
                        message("unsafe metadata path: {path}", path=child.relative_to(self.root))
                    )
                if child.is_dir():
                    pending.append((child, index + 1))
                elif not any(char in pattern for char in "*?["):
                    raise InspectionError(
                        message(
                            "declared workspace directory is missing: {path}",
                            path=child.relative_to(self.root),
                        )
                    )
                continue
            if part == "**":
                pending.append((directory, index + 1))
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda item: item.name)
            for entry in children:
                if entry.name in IGNORED_DIRECTORIES or entry.name.startswith(".cache"):
                    continue
                child = directory / entry.name
                if (
                    self.safe(child)
                    and entry.is_dir(follow_symlinks=False)
                    and (part == "**" or fnmatch.fnmatchcase(entry.name, part))
                ):
                    pending.append((child, index if part == "**" else index + 1))

    def find(self, names: set[str], directory: Path | None = None) -> list[Path]:
        directory = (directory or self.root).resolve()
        return sorted(
            (path for path in self.files if path.name in names and path.is_relative_to(directory)),
            key=lambda path: (len(path.relative_to(self.root).parts), path.as_posix()),
        )

    def description(self) -> dict:
        return {
            "scopes": [
                {"path": path.relative_to(self.root).as_posix(), "reasons": sorted(reasons)}
                for path, reasons in sorted(self.scopes.items())
            ],
            "files": [path.relative_to(self.root).as_posix() for path in sorted(self.files)],
            "truncated": sorted(self.truncated),
        }


def _pnpm_packages(content: str) -> list[str]:
    """Read only a static packages sequence; no YAML tags, anchors or evaluation."""
    result = []
    active = False
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"packages:\s*(.*?)\s*", line)
        if match:
            active = True
            raw = match.group(1)
            if raw and not raw.startswith("#"):
                try:
                    values = ast.literal_eval(raw)
                    if not isinstance(values, list) or any(
                        not isinstance(item, str) or not item for item in values
                    ):
                        raise ValueError("expected string paths")
                    return values
                except (SyntaxError, ValueError) as exc:
                    raise InspectionError(
                        message("pnpm packages must be a static sequence of quoted paths")
                    ) from exc
            continue
        if active:
            item = re.fullmatch(r"\s*-\s+(['\"]?)(.*?)\1\s*(?:#.*)?", line)
            if item:
                raw = item.group(2).strip()
                if not raw or (not item.group(1) and raw.startswith(("&", "*", "!<", "{"))):
                    raise InspectionError(
                        message("pnpm packages must be a static sequence of quoted paths")
                    )
                result.append(raw)
            elif line[0].isspace():
                raise InspectionError(
                    message("pnpm packages must be a static sequence of quoted paths")
                )
            else:
                active = False
    return result
