"""Static SSH inventory. Never run ssh, Match exec, proxies or local commands."""

from __future__ import annotations

import glob
import os
import re
import stat
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

MAX_FILES = 128
MAX_BYTES = 1024 * 1024
MAX_DEPTH = 16
FIELDS = ("hostname", "user", "port", "proxy")
UNKNOWN = object()


@dataclass
class Directive:
    key: str
    arguments: list[str]
    source: Path
    line: int
    includes: list[list[Directive]] | None = None


def _tokens(value: str) -> list[str]:
    """Split SSH arguments, preserving ordinary Windows path backslashes."""
    tokens, word = [], []
    quote = None
    started = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value) and value[index + 1] in "\\\"'":
            index += 1
            word.append(value[index])
        elif quote:
            if char == quote:
                quote = None
            else:
                word.append(char)
        elif char in "\"'":
            quote = char
            started = True
        elif char == "#":
            break
        elif char.isspace():
            if started or word:
                tokens.append("".join(word))
                word, started = [], False
        else:
            word.append(char)
        index += 1
    if quote:
        raise ValueError("unclosed quote")
    if started or word:
        tokens.append("".join(word))
    return tokens


def _matches(alias: str, patterns: list[str]) -> bool:
    matched = False
    for pattern in patterns:
        negative = pattern.startswith("!")
        pattern = pattern.removeprefix("!")
        expression = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
        if re.fullmatch(expression, alias, re.IGNORECASE):
            if negative:
                return False
            matched = True
    return matched


def _expand(value: str, tokens: dict[str, str]) -> str | None:
    unresolved = False

    def token(match):
        nonlocal unresolved
        key = match[0][1:]
        if key not in tokens:
            unresolved = True
        return tokens.get(key, "")

    expanded = re.sub(r"%.|%$", token, value)
    return None if unresolved else expanded


class Inventory:
    def __init__(
        self,
        home: Path,
        username: str,
        overrides: dict[Path, bytes] | None = None,
        *,
        snapshot: bool = False,
    ):
        self.home, self.username = home, username
        self.files: list[str] = []
        self.warnings: list[dict] = []
        self.aliases: dict[str, Directive] = {}
        self._cache: dict[tuple[Path, Path], list[Directive]] = {}
        self.contents: dict[Path, bytes | None] = {}
        self.paths: dict[Path, Path] = {}
        self.overrides = overrides or {}
        self.snapshot = snapshot

    def warn(self, code: str, source: Path, line: int = 0) -> None:
        item = {"code": code, "source": str(source), "line": line}
        if item not in self.warnings:
            self.warnings.append(item)

    def read(
        self, path: Path, base: Path, stack: tuple[Path, ...] = (), *, optional: bool = False
    ) -> list[Directive]:
        try:
            original = path.absolute()
            path = path.resolve()
            if self.snapshot:
                self.paths[original] = path
            if path in stack or len(stack) >= MAX_DEPTH:
                self.warn("include_cycle", path)
                return [Directive("unknown", [], path, 0)]
            if (path, base) in self._cache:
                return self._cache[path, base]
            if len(self.files) >= MAX_FILES:
                self.warn("scan_limit", path)
                return [Directive("unknown", [], path, 0)]
            if path in self.overrides:
                data = self.overrides[path]
            else:
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                    self.warn("scan_limit", path)
                    return [Directive("unknown", [], path, 0)]
                with path.open("rb") as stream:
                    data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                self.warn("scan_limit", path)
                return [Directive("unknown", [], path, 0)]
            content = data.decode("utf-8-sig")
            if self.snapshot:
                self.contents[path] = data
        except FileNotFoundError:
            if self.snapshot:
                self.contents[path] = None
            if optional:
                return []
            self.warn("unreadable", path)
            return [Directive("unknown", [], path, 0)]
        except OSError, UnicodeError, ValueError:
            self.warn("unreadable", path)
            return [Directive("unknown", [], path, 0)]
        self.files.append(str(path))
        directives = []
        for line, text in enumerate(content.splitlines(), 1):
            if not text.strip() or text.lstrip().startswith("#"):
                continue
            match = re.fullmatch(r"\s*([A-Za-z][A-Za-z0-9]*)\s*(?:=\s*)?(.*)", text)
            if not match:
                self.warn("invalid_config", path, line)
                directives.append(Directive("unknown", [], path, line))
                continue
            key, value = match.groups()
            key = key.lower()
            # Do not retain arbitrary commands, key material or unrelated options.
            if key not in {
                "hostname",
                "user",
                "port",
                "host",
                "match",
                "include",
                "proxyjump",
                "proxycommand",
            }:
                continue
            try:
                args = _tokens(value)
                if not args or any(not arg or any(ord(c) < 32 for c in arg) for arg in args):
                    raise ValueError
                if key in {"hostname", "user", "port", "proxyjump"} and len(args) != 1:
                    raise ValueError
                if key == "port" and (not args[0].isdigit() or not 1 <= int(args[0]) <= 65535):
                    raise ValueError
            except ValueError:
                self.warn("invalid_config", path, line)
                directives.append(Directive("unknown", [], path, line))
                continue
            if key == "proxycommand":
                args = ["none"] if len(args) == 1 and args[0].lower() == "none" else []
            directive = Directive(key, args, path, line)
            directives.append(directive)
            if key == "host":
                for alias in args:
                    if not any(char in alias for char in "!*?"):
                        self.aliases.setdefault(alias, directive)
            elif key == "include":
                directive.includes = []
                for value in args:
                    # Host-dependent tokens cannot be resolved during inventory.
                    expanded = _expand(value, {"%": "%", "d": str(self.home), "u": self.username})
                    if expanded is not None:
                        expanded = re.sub(
                            r"\$\{([^}]+)\}",
                            lambda m: (
                                str(self.home) if m[1] == "HOME" else os.environ.get(m[1], m[0])
                            ),
                            expanded,
                        )
                    if (
                        expanded is None
                        or "$" in expanded
                        or expanded.startswith("~")
                        and not expanded.startswith("~/")
                    ):
                        self.warn("dynamic_include", path, line)
                        directive.includes.append([Directive("unknown", [], path, line)])
                        continue
                    include = (
                        self.home / expanded[2:] if expanded.startswith("~/") else Path(expanded)
                    )
                    if not include.is_absolute():
                        include = base / include
                    filenames = list(islice(glob.iglob(str(include)), MAX_FILES + 1))
                    if len(filenames) > MAX_FILES:
                        self.warn("scan_limit", path, line)
                        directive.includes.append([Directive("unknown", [], path, line)])
                    for filename in sorted(filenames[:MAX_FILES]):
                        directive.includes.append(self.read(Path(filename), base, (*stack, path)))
        self._cache[path, base] = directives
        return directives

    def host(self, alias: str, roots: list[list[Directive]]) -> dict | None:
        values = {}
        origins = {}
        source = None
        conditional = True

        def visit(directives: list[Directive], gate: bool | None = True):
            nonlocal source, conditional
            active = gate
            for item in directives:
                if item.key == "host":
                    active = gate if _matches(alias, item.arguments) else False
                    if active is not False and alias in item.arguments:
                        source = source or item
                        if active is True:
                            conditional = False
                elif item.key == "match":
                    active = (
                        gate
                        if len(item.arguments) == 1 and item.arguments[0].lower() == "all"
                        else False
                        if gate is False
                        else None
                    )
                elif active is not False:
                    if item.key == "include":
                        for child in item.includes or []:
                            visit(child, active)
                    elif item.key == "unknown":
                        for field in FIELDS:
                            if field not in values:
                                origins[field] = {"source": str(item.source), "line": item.line}
                            values.setdefault(field, UNKNOWN)
                    elif item.key in {"hostname", "user", "port", "proxyjump", "proxycommand"}:
                        field = "proxy" if item.key.startswith("proxy") else item.key
                        value = (
                            {
                                "kind": "none" if item.arguments == ["none"] else "command",
                                "value": None,
                            }
                            if item.key == "proxycommand"
                            else {"kind": "jump", "value": item.arguments[0]}
                            if item.key == "proxyjump"
                            else item.arguments[0]
                        )
                        # ProxyJump none reserves the jump option but leaves
                        # ProxyCommand available; an enabled jump reserves both.
                        previous = values.get(field)
                        if (
                            item.key == "proxycommand"
                            and isinstance(previous, dict)
                            and previous["kind"] == "jump"
                            and previous["value"].lower() == "none"
                        ):
                            values[field] = value if active is True else UNKNOWN
                            origins[field] = {"source": str(item.source), "line": item.line}
                        else:
                            if field not in values:
                                origins[field] = {"source": str(item.source), "line": item.line}
                            values.setdefault(field, value if active is True else UNKNOWN)

        for root in roots:
            visit(root)
        if source is None:
            return None
        values.setdefault("hostname", alias)
        values.setdefault("user", self.username)
        values.setdefault("port", "22")
        values.setdefault("proxy", {"kind": "none", "value": None})
        hostname = values["hostname"]
        if isinstance(hostname, str):
            hostname = _expand(hostname, {"%": "%", "h": alias})
            if hostname is None:
                hostname = UNKNOWN
        values["hostname"] = hostname
        unresolved = [field for field in FIELDS if values[field] is UNKNOWN]
        proxy = values["proxy"]
        return {
            "name": alias,
            "hostname": None if hostname is UNKNOWN else hostname,
            "user": None if values["user"] is UNKNOWN else values["user"],
            "port": None if values["port"] is UNKNOWN else int(values["port"]),
            "proxy_jump": None
            if proxy is UNKNOWN or proxy["kind"] != "jump" or proxy["value"].lower() == "none"
            else proxy["value"],
            "proxy_command": None if proxy is UNKNOWN else proxy["kind"] == "command",
            "source": str(source.source),
            "line": source.line,
            "unresolved_fields": unresolved,
            "conditional": conditional,
            "field_sources": {field: origins.get(field) for field in FIELDS},
        }

    def collect(self, system_config: Path, *, query: str = "") -> dict:
        roots = [
            self.read(self.home / ".ssh/config", self.home / ".ssh", optional=True),
            self.read(system_config, system_config.parent, optional=True),
        ]
        hosts = []
        for alias in sorted(self.aliases, key=str.casefold):
            host = self.host(alias, roots)
            if (
                host
                and query.casefold()
                in " ".join(str(host[k] or "") for k in ("name", "hostname", "user")).casefold()
            ):
                hosts.append(host)
        return {
            "hosts": hosts,
            "files": self.files,
            "warnings": self.warnings,
            "complete": not self.warnings,
        }


def collect_hosts(home: Path, username: str, system_config: Path, *, query: str = "") -> dict:
    return Inventory(home, username).collect(system_config, query=query)
