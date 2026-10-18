"""Windows hosts toolbox, with staged editing and reversible profile switches."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from .cli_errors import TargetError, exit_code
from .command_help import add_config, add_environment
from .hosts_config import (
    Document,
    HostsList,
    HostsStore,
    digest,
    entry_conflicts,
    managed_block,
    parse_entries,
    read_bytes,
    switch_profile,
    validate_content,
    validate_edit,
)
from .i18n import message, t
from .list_editor import edit_list, field
from .presentation import colored, format_table, warning
from .projects.models import ProjectError
from .runtimes.platforms.locking import operation_lock
from .schemas import CLI_SCHEMA_VERSION
from .settings import default_path, editor_command


def native_paths() -> tuple[Path, HostsStore]:
    from .runtimes.platforms.windows_hosts import system_hosts_path

    return system_hosts_path(), HostsStore(default_path().parent / "hosts")


def _safe(value: object) -> str:
    return "".join(f"\\x{ord(c):02x}" if ord(c) < 32 or ord(c) == 127 else c for c in str(value))


def _diff(before: bytes, after: bytes) -> str:
    return "".join(
        difflib.unified_diff(
            Document.decode(before).text.splitlines(keepends=True),
            Document.decode(after).text.splitlines(keepends=True),
            fromfile="hosts",
            tofile="hosts (updated)",
        )
    )


@contextmanager
def _lock(store: HostsStore):
    try:
        with operation_lock(store.root, timeout=15):
            yield
    except ProjectError:
        raise ValueError(message("Another hosts operation is in progress; retry shortly")) from None


def _apply(
    path: Path, store: HostsStore, before: bytes, after: bytes, operation: str, *, as_json: bool
) -> dict:
    from .runtimes.platforms.windows_hosts import write_system_hosts

    if before == after:
        return {"changed": False, "backup": None, "dns_flushed": None}
    if len(after) > 10 * 1024 * 1024:
        raise ValueError(message("Hosts file exceeds the 10 MiB limit"))
    if not as_json:
        for line in _diff(before, after).splitlines():
            print(
                colored(_safe(line), "red" if line.startswith("-") else "green", stream=sys.stderr)
                if line.startswith(("-", "+"))
                else _safe(line),
                file=sys.stderr,
            )
    with _lock(store):
        if read_bytes(path) != before:
            raise ValueError(message("Hosts changed during editing; reopen the file and retry"))
        backup = store.backup(before, after, operation)
        if not as_json:
            print(t("Hosts backup: {backup}", backup=backup), file=sys.stderr)
        flushed = write_system_hosts(before, after)
    return {"changed": True, "backup": backup, "dns_flushed": flushed}


def _edit(args, path: Path, store: HostsStore) -> dict:
    before = read_bytes(path)
    if args.profile:
        destination = store.profile(args.profile)
        original = read_bytes(destination) if destination.exists() else None
        content = (
            Document.decode(original).text
            if original is not None
            else "# dev-tools hosts profile\n# 127.0.0.1 app.test\n"
        )
    else:
        destination = path
        original = before
        content = Document.decode(before).text
    drafts = store.root / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    draft = drafts / (uuid.uuid4().hex + ".hosts")
    draft.write_text(content, encoding="utf-8", newline="")
    if not args.json:
        print(t("Edit and save the hosts draft: {path}", path=draft))
    result = subprocess.run(editor_command(args.settings, draft), check=False)
    if result.returncode:
        raise ValueError(message("Editor failed; hosts draft is preserved at {path}", path=draft))
    if sys.stdin.isatty() or os.environ.get("DEV_TOOLS_TERMINAL") == "1":
        try:
            input(t("Save and close the editor, then press Enter to apply (Ctrl+C cancels): "))
        except EOFError:
            raise ValueError(
                message("Cannot wait for the editor; draft is preserved at {path}", path=draft)
            ) from None
    edited = Document.decode(read_bytes(draft)).text
    try:
        if args.profile:
            validate_content(edited)
            if managed_block(edited):
                raise ValueError(message("Hosts profiles must not contain managed markers"))
            with _lock(store):
                current = read_bytes(destination) if destination.exists() else None
                if current != original:
                    raise ValueError(
                        message("Hosts profile changed during editing; draft was preserved")
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                draft.replace(destination)
            return {
                "changed": current != read_bytes(destination),
                "path": str(destination),
                "profile": args.profile,
                "applied": False,
            }
        document = Document.decode(before)
        validate_edit(document.text, edited)
        after = before if edited == document.text else document.encode(edited)
        value = _apply(path, store, before, after, "edit", as_json=args.json)
    except OSError, ValueError, subprocess.SubprocessError:
        print(
            warning(t("Hosts draft preserved: {path}", path=draft), stream=sys.stderr),
            file=sys.stderr,
        )
        raise
    draft.unlink(missing_ok=True)
    return value


def _edit_list(args, path: Path, store: HostsStore, before: bytes) -> dict:
    document = Document.decode(before)
    model = HostsList(document.text)
    result = {"changed": False}
    preserved = []

    def rows():
        return model.entries(args.query)

    def show():
        entries = rows()
        return [
            (
                item["address"],
                " ".join(item["names"]),
                item["comment"],
                t("Enabled") if item["enabled"] else t("Disabled"),
            )
            for item in entries
        ]

    def edit(index, column):
        old = rows()[index] if index is not None else None
        if old and column == 3:
            toggle(index)
            return
        entry = dict(old) if old else {"address": "", "names": [], "comment": "", "enabled": True}
        for number, (key, label) in enumerate(
            (("address", "Address"), ("names", "Domains"), ("comment", "Comment"))
        ):
            if old is None or column == number:
                current = " ".join(entry[key]) if key == "names" else entry[key]
                value = field(label, current, clear=key == "comment")
                entry[key] = value.split() if key == "names" else value
        model.put(entry, old["line"] if old else None)

    def toggle(index):
        item = rows()[index]
        model.put({**item, "enabled": not item["enabled"]}, item["line"])

    def after():
        validate_edit(document.text, model.text)
        return before if model.text == document.text else document.encode(model.text)

    def save():
        content = after()
        try:
            result.update(_apply(path, store, before, content, "list-edit", as_json=True))
        except OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt:
            drafts = store.root / "drafts"
            drafts.mkdir(parents=True, exist_ok=True)
            draft = drafts / (uuid.uuid4().hex + ".hosts")
            draft.write_bytes(content)
            preserved.append(draft)
            raise

    try:
        edit_list(
            headers=("Address", "Domains", "Comment", "状态"),
            rows=show,
            edit=edit,
            delete=lambda index: model.delete(rows()[index]["line"]),
            toggle=toggle,
            preview=lambda: _diff(before, after()),
            save=save,
        )
    finally:
        for draft in preserved:
            print(t("Hosts draft preserved: {path}", path=draft), file=sys.stderr)
    return result


def execute(args) -> dict:
    path, store = native_paths()
    operation = args.hosts_action
    result = {
        "schema_version": CLI_SCHEMA_VERSION,
        "action": "hosts",
        "operation": operation,
        "environment": "windows",
        "path": str(path),
    }
    before = read_bytes(path)
    document = Document.decode(before)
    if operation == "list" and getattr(args, "list_edit", False):
        result.update(_edit_list(args, path, store, before))
        return result
    if operation == "list":
        block = managed_block(document.text)
        entries, invalid = parse_entries(document.text)
        offsets, offset = [], 0
        for line in document.text.splitlines(keepends=True):
            offsets.append(offset)
            offset += len(line)
        for item in entries:
            offset = offsets[item["line"] - 1]
            item["profile"] = block[2] if block and block[0] <= offset < block[1] else None
        query = args.query.casefold()
        result.update(
            entries=[
                item
                for item in entries
                if query
                in (
                    item["address"] + " " + " ".join(item["names"]) + " " + item["comment"]
                ).casefold()
            ],
            profile=block[2] if block else None,
            invalid_lines=invalid,
            complete=not invalid,
            conflicts=entry_conflicts(entries),
        )
    elif operation == "profiles":
        block = managed_block(document.text)
        profiles = []
        for file in sorted((store.root / "profiles").glob("*.hosts")):
            try:
                content = store.load_profile(file.stem)
                active = bool(block and block[2].casefold() == file.stem.casefold())
                body = document.text[block[0] : block[1]].splitlines()[1:-1] if active else []
                profiles.append(
                    {
                        "name": file.stem,
                        "path": str(file),
                        "entries": len(parse_entries(content)[0]),
                        "status": "modified"
                        if active
                        and "\n".join(body).rstrip("\r\n")
                        != "\n".join(content.splitlines()).rstrip("\r\n")
                        else "active"
                        if active
                        else "inactive",
                    }
                )
            except OSError, ValueError:
                profiles.append(
                    {"name": file.stem, "path": str(file), "entries": None, "status": "invalid"}
                )
        result.update(profiles=profiles)
    elif operation == "backups":
        result.update(
            backups=[
                {**item, "can_restore": item["after"] == digest(before)} for item in store.backups()
            ]
        )
    elif operation == "edit":
        result.update(_edit(args, path, store))
    else:
        if operation == "restore":
            after = store.restore(before, args.backup)
        else:
            name = args.profile if operation == "use" else None
            text = switch_profile(document, name, store.load_profile(name) if name else "")
            after = document.encode(text)
        result.update(_apply(path, store, before, after, operation, as_json=args.json))
    return result


def _display_update(value: dict) -> None:
    print(t("Hosts updated.") if value["changed"] else t("Hosts already match; no changes."))
    if value.get("backup"):
        print(t("Hosts backup: {backup}", backup=value["backup"]))
    if value.get("dns_flushed") is False:
        print(
            warning(
                t(
                    "Hosts was saved but the DNS cache could not be flushed; retry with ipconfig /flushdns"
                )
            )
        )


def display(value: dict) -> None:
    operation = value["operation"]
    print(t("Windows hosts: {path}", path=value["path"]))
    if operation == "list" and "entries" not in value:
        if value.get("changed"):
            _display_update(value)
        return
    if operation == "list":
        conflicting_lines = {line for conflict in value["conflicts"] for line in conflict["lines"]}
        rows = [
            (
                item["address"],
                _safe(" ".join(item["names"])),
                _safe(item["comment"] or "-"),
                item["profile"] or t("Manual"),
                colored(
                    t("Conflict")
                    if item["line"] in conflicting_lines
                    else t("Enabled")
                    if item["enabled"]
                    else t("Disabled"),
                    "yellow"
                    if item["line"] in conflicting_lines
                    else "green"
                    if item["enabled"]
                    else "muted",
                ),
            )
            for item in value["entries"]
        ]
        print(
            format_table(
                tuple(t(key) for key in ("Address", "Domains", "Comment", "Profile", "状态")), rows
            )
            if rows
            else t("No matching hosts entries.")
        )
        if value["invalid_lines"]:
            print(
                warning(
                    t(
                        "Invalid hosts entries on lines: {lines}",
                        lines=", ".join(map(str, value["invalid_lines"])),
                    )
                )
            )
        for conflict in value["conflicts"]:
            print(
                warning(
                    t(
                        "Hosts domain {domain} maps to conflicting IPv{version} addresses",
                        domain=conflict["domain"],
                        version=conflict["version"],
                    )
                )
            )
    elif operation == "profiles":
        rows = [
            (
                item["name"],
                str(item["entries"]) if item["entries"] is not None else "-",
                colored(
                    t(
                        {
                            "active": "Active",
                            "inactive": "Inactive",
                            "invalid": "Invalid",
                            "modified": "Modified after activation",
                        }[item["status"]]
                    ),
                    "green"
                    if item["status"] == "active"
                    else "muted"
                    if item["status"] == "inactive"
                    else "yellow",
                ),
            )
            for item in value["profiles"]
        ]
        print(
            format_table(tuple(t(key) for key in ("Profile", "Entries", "状态")), rows)
            if rows
            else t("No hosts profiles. Create one with dev-tools hosts edit dev.")
        )
    elif operation == "backups":
        rows = [
            (item["id"], _safe(item["operation"]), t("是") if item["can_restore"] else t("否"))
            for item in value["backups"]
        ]
        print(
            format_table(tuple(t(key) for key in ("Backup", "操作", "Can restore")), rows)
            if rows
            else t("No hosts backups.")
        )
    elif operation == "edit" and value.get("profile"):
        print(
            t(
                "Hosts profile saved: {profile}; activate with dev-tools hosts use {profile}",
                profile=value["profile"],
            )
        )
    else:
        _display_update(value)


def run(args) -> int:
    if args.env != "win":
        raise ValueError(message("Hosts commands manage Windows only; use -e win from WSL"))
    if os.name != "nt":
        from .runtimes.diagnostics import preflight, response_payload
        from .runtimes.router import forward_remote

        version = preflight("win", ["hosts", args.hosts_action], args.settings)
        arguments = ["hosts", args.hosts_action]
        interactive = args.hosts_action == "list" and getattr(args, "list_edit", False)
        if interactive:
            from .list_editor import require_terminal

            require_terminal()
            arguments.append("--edit")
        for name in ("profile", "backup", "query"):
            if getattr(args, name, None):
                arguments.extend(["--", getattr(args, name)])
        if args.json:
            arguments.insert(2, "--json")

        def forward():
            return forward_remote(
                "win",
                arguments,
                settings=args.settings,
                capture=args.json,
                timeout=None
                if interactive or args.hosts_action in {"edit", "use", "off", "restore"}
                else 30,
            )

        if interactive:
            from .terminal import raw_input

            with raw_input():
                result = forward()
        else:
            result = forward()
        code = result.returncode if args.json else result
        if code == 2:
            raise TargetError(
                "target_command_unsupported",
                message(
                    "Windows target does not support hosts commands; update the Windows controller"
                ),
                environment="win",
                target_version=version,
            )
        if args.json:
            value = response_payload(result, "win", "hosts")
            if "error" not in value and (
                value.get("environment") != "windows" or value.get("operation") != args.hosts_action
            ):
                raise TargetError(
                    "target_response_invalid",
                    message(
                        "Target controller returned an invalid response; check both controller versions"
                    ),
                    environment="win",
                )
            print(json.dumps(value, ensure_ascii=False, indent=2))
        return exit_code(code)
    value = execute(args)
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        display(value)
    return 1 if value.get("complete") is False else 0


def add_commands(commands) -> None:
    hosts = commands.add_parser(
        "hosts",
        help=t("View, edit and switch Windows hosts profiles"),
        group=t("工具箱"),
        project=False,
    )
    add_environment(hosts)
    hosts.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    actions = hosts.add_subparsers(dest="hosts_action", required=True, metavar=t("操作"))
    descriptions = {
        "list": "List Windows hosts entries",
        "edit": "Edit Windows hosts or a named profile",
        "profiles": "List saved hosts profiles",
        "use": "Activate a hosts profile",
        "off": "Remove the dev-tools managed hosts block",
        "backups": "List automatic hosts backups",
        "restore": "Restore hosts from a matching backup",
    }
    for name, description in descriptions.items():
        command = actions.add_parser(
            name, help=t(description), description=t(description), allow_abbrev=False
        )
        add_environment(command)
        add_config(command)
        command.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help=t("以 JSON 输出结果")
        )
        if name == "list":
            command.add_argument(
                "--edit",
                dest="list_edit",
                action="store_true",
                help=t("Edit entries in an interactive list"),
            )
            command.add_argument(
                "query",
                nargs="?",
                default="",
                metavar=t("关键词"),
                help=t("Filter by address, domain or comment"),
            )
        if name in {"edit", "use"}:
            command.add_argument(
                "profile",
                nargs="?" if name == "edit" else None,
                metavar=t("Profile"),
                help=t("Hosts profile to activate")
                if name == "use"
                else t("Profile name; omit for direct hosts editing"),
            )
        if name == "restore":
            command.add_argument(
                "backup",
                nargs="?",
                metavar=t("Backup"),
                help=t("Backup ID; omit for the latest matching backup"),
            )
        command.set_defaults(func=run)
