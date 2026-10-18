"""Conservative, lossless SSH list edits with static precedence validation."""

from __future__ import annotations

import codecs
import difflib
import json
import os
import re
import stat
import uuid
from pathlib import Path

from .i18n import message, render, t
from .list_editor import edit_list, field
from .runtimes.platforms.locking import operation_lock
from .settings import default_path
from .ssh_config import MAX_BYTES, Inventory, _tokens

FIELDS = {"hostname": "HostName", "user": "User", "port": "Port", "proxy_jump": "ProxyJump"}
KEYS = ("name", *FIELDS)
LABELS = ("Alias", "Address", "User", "Port", "Jump host")


def _parts(line):
    match = re.match(r"\s*([A-Za-z][A-Za-z0-9]*)\s*(?:=\s*)?(.*)", line.rstrip("\r\n"))
    return (match[1].lower(), match[2]) if match else ("", "")


def _comment(line):
    quote, escaped = None, False
    for index, char in enumerate(line.rstrip("\r\n")):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            return line[index:].rstrip("\r\n")
    return ""


def _private_file(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        from .runtimes.platforms.preferences import own_user_file

        own_user_file(path, [])


def _mkdir(path):
    missing, parent = [], path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for item in reversed(missing):
        item.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt" and missing:
        from .runtimes.platforms.preferences import own_user_file

        own_user_file(missing[0], missing)


def _replace(path: Path, content: bytes, before: bytes | None):
    """Atomic per-file replacement; retain existing ownership and Windows ACLs."""
    _mkdir(path.parent)
    temporary = path.with_name(".dev-tools-ssh-" + uuid.uuid4().hex)
    try:
        _private_file(temporary, content)
        if (
            path.is_symlink()
            or path.resolve() != path
            or (path.read_bytes() if path.exists() else None) != before
        ):
            raise ValueError(message("SSH configuration changed during editing; reopen the list"))
        if before is None:
            # Exclusive publication refuses an independently created destination.
            os.link(temporary, path)
        elif os.name == "nt":
            import ctypes
            from ctypes import wintypes

            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.ReplaceFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.LPVOID,
            ]
            if not api.ReplaceFileW(str(path), str(temporary), None, 0, None, None):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            info = path.stat()
            current = temporary.stat()
            if (current.st_uid, current.st_gid) != (info.st_uid, info.st_gid):
                os.chown(temporary, info.st_uid, info.st_gid)
            temporary.chmod(stat.S_IMODE(info.st_mode))
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SshList:
    def __init__(self, home: Path, username: str, system: Path):
        self.home, self.username, self.system = home, username, system
        self.inventory = Inventory(home, username, snapshot=True)
        self.value = self.inventory.collect(system)
        if not self.value["complete"]:
            raise ValueError(
                message(
                    "SSH configuration is incomplete; repair it with ssh edit before list editing"
                )
            )
        self.before = dict(self.inventory.contents)
        self.changes: dict[Path, bytes] = {}
        self.recovery: Path | None = None

    def rows(self, query=""):
        return [
            host
            for host in self.value["hosts"]
            if query.casefold()
            in " ".join(str(host[key] or "") for key in ("name", "hostname", "user")).casefold()
        ]

    def _block(self, host, changes=None):
        path = Path(host["source"])
        if (
            not path.is_relative_to((self.home / ".ssh").absolute())
            or path == self.system.resolve()
            or host["conditional"]
            or host["unresolved_fields"]
        ):
            raise ValueError(
                message(
                    "This SSH row is read-only in the list; use ssh edit {alias}",
                    alias=host["name"],
                )
            )
        data = (self.changes if changes is None else changes).get(path, self.before.get(path))
        if data is None:
            raise ValueError(message("SSH declaration is unavailable"))
        lines = data.decode("utf-8-sig").splitlines(keepends=True)
        start, end = host["line"] - 1, len(lines)
        key, value = _parts(lines[start])
        if (
            key != "host"
            or _tokens(value) != [host["name"]]
            or any(_parts(line)[0] == "match" for line in lines)
        ):
            raise ValueError(
                message(
                    "This SSH row is read-only in the list; use ssh edit {alias}",
                    alias=host["name"],
                )
            )
        fields = {}
        for index in range(start + 1, len(lines)):
            key, value = _parts(lines[index])
            if key == "host":
                end = index
                break
            if key in {"include", "proxycommand"} or key in fields:
                raise ValueError(
                    message(
                        "This SSH row is read-only in the list; use ssh edit {alias}",
                        alias=host["name"],
                    )
                )
            if key in {name.lower() for name in FIELDS.values()}:
                fields[key] = index
        return path, data, lines, start, end, fields

    def local(self, host):
        _, _, lines, _, _, fields = self._block(host)
        return {
            "name": host["name"],
            **{
                key: _tokens(_parts(lines[fields[label.lower()]])[1])[0]
                if label.lower() in fields
                else ""
                for key, label in FIELDS.items()
            },
        }

    @staticmethod
    def _validate(values):
        name = values["name"]
        if len(name) > 255 or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name):
            raise ValueError(
                message(
                    "Use a concrete SSH alias containing letters, digits, dots, underscores or hyphens"
                )
            )
        for key, value in values.items():
            if len(value) > 1024 or any(
                c.isspace() or ord(c) < 32 or ord(c) == 127 or c in "#\"'" for c in value
            ):
                raise ValueError(message("SSH list fields must be single static arguments"))
            if (
                key == "port"
                and value
                and (not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535)
            ):
                raise ValueError(message("SSH port must be between 1 and 65535"))

    def _accept(self, changes, old_name, new_name, explicit=None):
        inventory = Inventory(self.home, self.username, changes)
        result = inventory.collect(self.system)
        if not result["complete"]:
            raise ValueError(message("List edits would produce an incomplete SSH configuration"))
        previous = {host["name"]: host for host in self.value["hosts"]}
        current = {host["name"]: host for host in result["hosts"]}
        keys = (
            "name",
            "hostname",
            "user",
            "port",
            "proxy_jump",
            "proxy_command",
            "conditional",
            "unresolved_fields",
        )
        for name in previous.keys() | current.keys():
            if name in (old_name, new_name):
                continue
            if (
                name not in previous
                or name not in current
                or any(previous[name][key] != current[name][key] for key in keys)
            ):
                raise ValueError(message("List edits would affect other SSH aliases; use ssh edit"))
        if (
            new_name is None
            and old_name in current
            or new_name is not None
            and new_name not in current
            or old_name != new_name
            and old_name in current
        ):
            raise ValueError(message("SSH alias has multiple declarations; use ssh edit"))
        if new_name and explicit:
            host = current[new_name]
            path, _, _, _, _, locations = self._block(host, changes)
            for key, value in explicit.items():
                if key == "name" or not value:
                    continue
                source = host["field_sources"]["proxy" if key == "proxy_jump" else key]
                if source != {"source": str(path), "line": locations[FIELDS[key].lower()] + 1}:
                    raise ValueError(
                        message("An earlier SSH rule overrides this field; use ssh edit")
                    )
        self.changes, self.value = changes, result

    def put(self, host, values):
        self._validate(values)
        if any(
            item["name"].casefold() == values["name"].casefold()
            and (host is None or item["name"] != host["name"])
            for item in self.value["hosts"]
        ):
            raise ValueError(message("SSH alias already exists"))
        explicit = dict(values)
        if host:
            old = self.local(host)
            path, data, lines, start, _, locations = self._block(host)
            if old == values:
                return
            explicit = {key: value for key, value in values.items() if old[key] != value}
            newline = "\r\n" if b"\r\n" in data else "\n"
            replacements, additions = {}, []
            for key, label in (("name", "Host"), *FIELDS.items()):
                if key not in explicit:
                    continue
                index = start if key == "name" else locations.get(label.lower())
                value = values[key]
                if index is None:
                    if value:
                        additions.append("    " + label + " " + value + newline)
                else:
                    raw = lines[index]
                    ending = raw[len(raw.rstrip("\r\n")) :]
                    comment = _comment(raw)
                    indent = raw[: len(raw) - len(raw.lstrip())]
                    replacements[index] = (
                        indent + label + " " + value + (" " + comment if comment else "") + ending
                        if value
                        else indent + comment + ending
                        if comment
                        else ""
                    )
            output = []
            for index, line in enumerate(lines):
                output.append(replacements.get(index, line))
                if index == start and additions:
                    if not output[-1].endswith(("\n", "\r")):
                        output[-1] += newline
                    output.extend(additions)
            text = "".join(output)
        else:
            path = (self.home / ".ssh/config").resolve()
            if not path.is_relative_to((self.home / ".ssh").absolute()):
                raise ValueError(message("SSH user config resolves outside ~/.ssh; use ssh edit"))
            data = self.changes.get(path, self.before.get(path)) or b""
            text = data.decode("utf-8-sig")
            newline = "\r\n" if b"\r\n" in data else "\n"
            if text and not text.endswith(("\r", "\n")):
                text += newline
            text += "Host " + values["name"] + newline
            text += "".join(
                "    " + label + " " + values[key] + newline
                for key, label in FIELDS.items()
                if values[key]
            )
        content = (codecs.BOM_UTF8 if data.startswith(codecs.BOM_UTF8) else b"") + text.encode(
            "utf-8"
        )
        if len(content) > MAX_BYTES:
            raise ValueError(message("SSH configuration exceeds the read limit"))
        self._accept(
            self.changes | {path: content}, host["name"] if host else None, values["name"], explicit
        )

    def delete(self, host):
        path, data, lines, start, end, _ = self._block(host)
        # Keep all full-line comments, including annotations before the next Host.
        kept = [
            line for line in lines[start:end] if not line.strip() or line.lstrip().startswith("#")
        ]
        text = "".join(lines[:start] + kept + lines[end:])
        content = (codecs.BOM_UTF8 if data.startswith(codecs.BOM_UTF8) else b"") + text.encode(
            "utf-8"
        )
        self._accept(self.changes | {path: content}, host["name"], None)

    def pending(self):
        return {
            path: data
            for path, data in self.changes.items()
            if data != self.before.get(path) and (data or self.before.get(path) is not None)
        }

    def preview(self):
        result = []
        for path, data in self.pending().items():
            result.extend(
                difflib.unified_diff(
                    (self.before.get(path) or b"").decode("utf-8-sig").splitlines(),
                    data.decode("utf-8-sig").splitlines(),
                    fromfile=str(path),
                    tofile=str(path),
                    n=0,
                    lineterm="",
                )
            )
        # Never display command bodies from removed custom directives.
        return "\n".join(
            re.sub(
                r"(?i)(\b(?:ProxyCommand|LocalCommand|RemoteCommand|KnownHostsCommand)\b).*",
                r"\1 " + t("Command omitted"),
                line,
            )
            for line in result
        )

    def save(self, store: Path):
        pending = self.pending()
        if not pending:
            return None
        _mkdir(store)
        with operation_lock(store, timeout=15):
            if os.name != "nt":
                from .runtimes.platforms.preferences import own_user_file

                own_user_file(store / "operation.lock", [])
            current = Inventory(self.home, self.username, snapshot=True)
            value = current.collect(self.system)
            if (
                not value["complete"]
                or current.contents != self.before
                or current.paths != self.inventory.paths
            ):
                raise ValueError(
                    message("SSH configuration changed during editing; reopen the list")
                )
            folder = store / "backups" / uuid.uuid4().hex
            _mkdir(folder)
            self.recovery = folder
            records = []
            for index, (path, data) in enumerate(pending.items()):
                _private_file(folder / f"{index}.before", self.before.get(path) or b"")
                _private_file(folder / f"{index}.draft", data)
                records.append(
                    {
                        "path": str(path),
                        "existed": self.before.get(path) is not None,
                        "backup": f"{index}.before",
                        "draft": f"{index}.draft",
                    }
                )
            _private_file(
                folder / "files.json", json.dumps(records, ensure_ascii=False, indent=2).encode()
            )
            completed = []
            try:
                for path, data in pending.items():
                    _replace(path, data, self.before.get(path))
                    completed.append(path)
            except (OSError, ValueError, KeyboardInterrupt) as exc:
                for path in reversed(completed):
                    try:
                        if self.before.get(path) is None:
                            if path.read_bytes() == pending[path]:
                                path.unlink()
                        else:
                            _replace(path, self.before[path], pending[path])
                    except OSError, ValueError:
                        pass
                if isinstance(exc, KeyboardInterrupt):
                    raise
                raise ValueError(
                    message(
                        "SSH save failed; backups and drafts are preserved at {path}", path=folder
                    )
                ) from None
        return folder


def run_list(home, username, system, query):
    model = SshList(home, username, system)

    def rows():
        return [tuple(str(host[key] or "") for key in KEYS) for host in model.rows(query)]

    def edit(index, column):
        host = model.rows(query)[index] if index is not None else None
        values = model.local(host) if host else dict.fromkeys(KEYS, "")
        for number, (key, label) in enumerate(zip(KEYS, LABELS, strict=True)):
            if host is None or number == column:
                values[key] = field(label, values[key], clear=key != "name")
        model.put(host, values)

    def describe(index, column):
        host = model.rows(query)[index]
        key = KEYS[column]
        source = (
            {"source": host["source"], "line": host["line"]}
            if key == "name"
            else host["field_sources"].get("proxy" if key == "proxy_jump" else key)
        )
        source_path = Path(source["source"]) if source else None
        display_path = (
            "~/" + source_path.relative_to(home).as_posix()
            if source_path and source_path.is_relative_to(home)
            else str(source_path)
        )
        return (
            t("Field source: {path}:{line}", path=display_path, line=source["line"])
            if source
            else t("Field source: SSH defaults")
        )

    def save():
        try:
            folder = model.save(default_path().parent / "ssh")
        except (ValueError, KeyboardInterrupt) as exc:
            if isinstance(exc, KeyboardInterrupt) and model.recovery:
                notices.append(t("SSH backups: {path}", path=model.recovery))
                raise
            notices.append(render(exc))
            raise
        if folder:
            # The terminal status must not hide recovery locations after exiting.
            backups.append(folder)

    backups = []
    notices = []
    try:
        edit_list(
            headers=LABELS,
            rows=rows,
            edit=edit,
            delete=lambda index: model.delete(model.rows(query)[index]),
            preview=model.preview,
            save=save,
            describe=describe,
        )
    finally:
        for folder in backups:
            print(t("SSH backups: {path}", path=folder))
        for notice in notices:
            print(notice)
    return 0
