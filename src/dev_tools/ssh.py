"""Static SSH queries, source editing and native interactive connections."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .cli_errors import RemoteError, TargetError, exit_code
from .command_help import add_config, add_environment
from .i18n import message, t
from .presentation import format_table, warning
from .schemas import CLI_SCHEMA_VERSION
from .settings import editor_command
from .ssh_config import collect_hosts

WARNINGS = {
    "include_cycle": "SSH include cycle or nesting limit",
    "scan_limit": "SSH configuration exceeds the read limit",
    "unreadable": "SSH configuration could not be read as UTF-8",
    "invalid_config": "Invalid SSH configuration syntax",
    "dynamic_include": "SSH include uses an unsupported dynamic path; skipped",
}


def native_identity() -> tuple[Path, str, Path]:
    if os.name == "nt":
        return (
            Path.home(),
            getpass.getuser(),
            Path(os.environ.get("PROGRAMDATA", Path.home().anchor + "ProgramData"))
            / "ssh/ssh_config",
        )
    from .runtimes.platforms.preferences import invoking_user

    user = invoking_user()
    return Path(user.pw_dir), user.pw_name, Path("/etc/ssh/ssh_config")


def _safe(value: object) -> str:
    return "".join(
        f"\\x{ord(char):02x}" if ord(char) < 32 or ord(char) == 127 else char for char in str(value)
    )


def print_hosts(value: dict) -> None:
    print(
        t(
            "SSH hosts ({environment})",
            environment="Windows" if value["environment"] == "windows" else "WSL",
        )
    )
    if value["hosts"]:
        rows = []
        for host in value["hosts"]:
            proxy = (
                t("Needs evaluation")
                if "proxy" in host["unresolved_fields"]
                else "ProxyCommand"
                if host["proxy_command"]
                else host["proxy_jump"] or "-"
            )
            rows.append(
                (
                    *(
                        _safe(item)
                        for item in (
                            host["name"],
                            host["hostname"] or t("Needs evaluation"),
                            host["user"] or t("Needs evaluation"),
                            host["port"] if host["port"] is not None else t("Needs evaluation"),
                            proxy,
                        )
                    ),
                    warning(t("Needs evaluation"))
                    if host["conditional"] or host["unresolved_fields"]
                    else t("Static"),
                )
            )
        print(
            format_table(
                tuple(
                    t(key)
                    for key in ("Alias", "Address", "User", "Port", "Jump host", "Configuration")
                ),
                rows,
            )
        )
    else:
        print(t("No matching SSH aliases. Add a concrete Host alias to ~/.ssh/config."))
    if any(host["unresolved_fields"] or host["conditional"] for host in value["hosts"]):
        print(
            warning(
                t(
                    "Conditional SSH values need evaluation when connecting; no commands were executed."
                )
            )
        )
    for item in value["warnings"]:
        print(
            warning(
                t(
                    "{source}:{line}: {detail}",
                    source=_safe(item["source"]),
                    line=item["line"],
                    detail=t(
                        WARNINGS.get(item["code"], "SSH configuration could not be read as UTF-8")
                    ),
                ),
                stream=sys.stderr,
            ),
            file=sys.stderr,
        )


def _valid_inventory(value: dict, environment: str, *, operation: str = "list") -> bool:
    hosts = value.get("hosts") if operation == "list" else [value.get("host")]
    if (
        value.get("operation") != operation
        or value.get("environment") != ("windows" if environment == "win" else "wsl")
        or not isinstance(hosts, list)
        or not isinstance(value.get("files"), list)
        or not all(isinstance(path, str) for path in value["files"])
        or not isinstance(value.get("warnings"), list)
        or type(value.get("complete")) is not bool
    ):
        return False
    for host in hosts:
        if (
            not isinstance(host, dict)
            or not all(isinstance(host.get(key), str) for key in ("name", "source"))
            or not all(
                key in host and (host[key] is None or isinstance(host[key], str))
                for key in ("hostname", "user", "proxy_jump")
            )
            or "port" not in host
            or host["port"] is not None
            and (type(host["port"]) is not int or not 1 <= host["port"] <= 65535)
            or "proxy_command" not in host
            or host["proxy_command"] is not None
            and type(host["proxy_command"]) is not bool
            or type(host.get("conditional")) is not bool
            or type(host.get("line")) is not int
            or not isinstance(host.get("unresolved_fields"), list)
            or any(
                field not in ("hostname", "user", "port", "proxy")
                for field in host["unresolved_fields"]
            )
        ):
            return False
    return all(
        isinstance(item, dict)
        and isinstance(item.get("code"), str)
        and isinstance(item.get("source"), str)
        and type(item.get("line")) is int
        for item in value["warnings"]
    )


def _alias(value: str) -> str:
    if (
        not value
        or value.startswith("-")
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(message("Invalid SSH alias or destination"))
    return value


def _find_host(value: dict, alias: str) -> dict:
    matches = [host for host in value["hosts"] if host["name"].casefold() == alias.casefold()]
    if not matches:
        raise ValueError(
            message("SSH alias was not found: {alias}; use ssh list or ssh edit", alias=alias)
        )
    if len(matches) != 1:
        raise ValueError(
            message("SSH alias is ambiguous: {alias}; inspect its configuration files", alias=alias)
        )
    return matches[0]


def _print_show(value: dict) -> None:
    host = value["host"]
    print_hosts({**value, "hosts": [host]})
    print(t("SSH declaration: {path}:{line}", path=_safe(host["source"]), line=host["line"]))
    print(t("SSH configuration files:"))
    for path in value["files"]:
        print(f"  {_safe(path)}")


def _create_user_config(path: Path) -> None:
    """Create only the native user's config, without changing existing permissions."""
    if path.exists() or path.is_symlink():
        return
    missing, parent = [], path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    created = []
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
            created.append(directory)
        except FileExistsError:
            pass
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "# Host my-server\n#     HostName server.example.com\n#     User deploy\n#     Port 22\n"
        )
    if os.name != "nt":
        from .runtimes.platforms.preferences import own_user_file

        own_user_file(path, created)


def _edit(args) -> int:
    home, username, system = native_identity()
    line = None
    if args.alias:
        value = collect_hosts(home, username, system)
        host = _find_host(value, _alias(args.alias))
        path, line = Path(host["source"]), host["line"]
    else:
        path = home / ".ssh/config"
        _create_user_config(path)
    if not path.is_file():
        raise ValueError(message("SSH configuration is not a regular file: {path}", path=path))
    command = editor_command(args.settings, path, line=line)
    print(t("SSH declaration: {path}:{line}", path=_safe(path), line=line or 1), flush=True)
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise ValueError(
            message(
                "SSH editor exited with code {code}; changes remain in the original file",
                code=result.returncode,
            )
        )
    if sys.stdin.isatty() or os.environ.get("DEV_TOOLS_TERMINAL") == "1":
        try:
            input(
                t(
                    "Save and close the SSH editor, then press Enter to check the static configuration: "
                )
            )
        except EOFError:
            raise ValueError(
                message("Cannot wait for the SSH editor; run ssh list after saving")
            ) from None
    value = collect_hosts(home, username, system)
    print_hosts({"environment": "windows" if os.name == "nt" else "wsl", **value})
    return 0 if value["complete"] else 1


def _client_status(code: int) -> int:
    if code == 0xC000013A:
        return 130
    if code < 0:
        return 128 - code
    return code if 0 <= code <= 255 else 1


def _connect(args) -> int:
    alias = _alias(args.alias)
    client = shutil.which("ssh.exe" if os.name == "nt" else "ssh")
    if client is None:
        raise ValueError(
            message(
                "OpenSSH client is missing; install it in the selected environment before connecting"
            )
        )
    command = [client, "--", alias]
    if os.name != "nt":
        from .runtimes.platforms.preferences import user_command

        command = user_command(command, preserve=("SSH_AUTH_SOCK",))
    return _client_status(subprocess.run(command, check=False).returncode)


def run(args: argparse.Namespace) -> int:
    from .runtimes.diagnostics import preflight, response_payload, update_instruction
    from .runtimes.router import forward_remote

    native = "win" if os.name == "nt" else "wsl"
    operation = args.ssh_action
    if operation in {"show", "edit", "connect"} and args.alias:
        _alias(args.alias)
    if operation in {"edit", "connect"}:
        if args.env == native:
            return _edit(args) if operation == "edit" else _connect(args)
        version = preflight(args.env, ["ssh", operation], args.settings)
        supported = forward_remote(
            args.env,
            ["ssh", operation, "--help"],
            settings=args.settings,
            capture=True,
            stdin=subprocess.DEVNULL,
        )
        if supported.returncode:
            raise TargetError(
                "target_command_unsupported" if supported.returncode == 2 else "target_unavailable",
                message(
                    "Target {environment} does not support ssh {operation}; update with: {command}",
                    environment=args.env,
                    operation=operation,
                    command=update_instruction(args.env),
                )
                if supported.returncode == 2
                else message(
                    "Could not query SSH capabilities in {environment}; check the target controller",
                    environment=args.env,
                ),
                environment=args.env,
                target_version=version,
            )
        arguments = ["ssh", operation]
        if args.alias:
            arguments.extend(["--", args.alias])
        result = forward_remote(
            args.env, arguments, settings=args.settings, capture=False, timeout=None
        )
        return _client_status(result) if operation == "connect" else exit_code(result)
    if args.env != native:
        version = preflight(args.env, ["ssh", operation], args.settings)
        arguments = ["ssh", operation, "--json"]
        query = args.query if operation == "list" else args.alias
        if query:
            arguments.extend(["--", query])
        result = forward_remote(args.env, arguments, settings=args.settings, capture=True)
        if result.returncode == 2:
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            raise TargetError(
                "target_command_unsupported",
                message(
                    "Target {environment} does not support ssh {operation}; update with: {command}",
                    environment=args.env,
                    operation=operation,
                    command=update_instruction(args.env),
                ),
                environment=args.env,
                target_version=version,
            )
        value = response_payload(result, args.env, "ssh")
        if "error" in value:
            raise RemoteError(value, result.returncode)
        if not _valid_inventory(value, args.env, operation=operation) or (
            operation == "show" and value["host"]["name"].casefold() != args.alias.casefold()
        ):
            raise TargetError(
                "target_response_invalid",
                message(
                    "Target controller returned an invalid response; check both controller versions"
                ),
                environment=args.env,
            )
        code = exit_code(result.returncode)
    else:
        value = {
            "schema_version": CLI_SCHEMA_VERSION,
            "action": "ssh",
            "operation": operation,
            "environment": "windows" if native == "win" else "wsl",
            **collect_hosts(*native_identity(), query=args.query if operation == "list" else ""),
        }
        if operation == "show":
            value["host"] = _find_host(value, args.alias)
            del value["hosts"]
        code = 0 if value["complete"] else 1
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        (_print_show if operation == "show" else print_hosts)(value)
    return code


def add_commands(commands) -> None:
    ssh = commands.add_parser(
        "ssh",
        help=t("List, inspect, edit and connect to SSH hosts"),
        group=t("工具箱"),
        project=False,
    )
    add_environment(ssh)
    ssh.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    actions = ssh.add_subparsers(dest="ssh_action", required=True, metavar=t("操作"))
    listing = actions.add_parser(
        "list",
        help=t("List SSH hosts"),
        description=t(
            "List concrete SSH aliases; defaults and Include are read without executing commands."
        ),
        allow_abbrev=False,
    )
    add_environment(listing)
    add_config(listing)
    listing.add_argument(
        "query",
        nargs="?",
        default="",
        metavar=t("关键词"),
        help=t("Filter by alias, address or user"),
    )
    listing.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help=t("以 JSON 输出结果")
    )
    listing.set_defaults(func=run)
    for name, description in (
        ("show", "Show static SSH details and the declaration source"),
        ("edit", "Edit the user SSH config or an alias declaration file"),
        ("connect", "Connect using the target environment's native SSH client"),
    ):
        command = actions.add_parser(
            name, help=t(description), description=t(description), allow_abbrev=False
        )
        add_environment(command)
        add_config(command)
        command.add_argument(
            "alias",
            nargs="?" if name == "edit" else None,
            metavar=t("Alias"),
            help=t("SSH alias; edit without an alias opens ~/.ssh/config"),
        )
        command.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help=t("JSON output; supported by list and show only"),
        )
        command.set_defaults(func=run)
