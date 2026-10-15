"""Shared command registration, transport validation and presentation for utilities."""

from __future__ import annotations

import argparse
import json
import os
import re

from ..cli_errors import RemoteError, TargetError
from ..command_help import add_config, add_environment
from ..i18n import message, t
from ..presentation import format_table, warning
from ..schemas import CLI_SCHEMA_VERSION
from .network import check_target, collect_info, parse_target
from .paths import collect_paths
from .ports import collect_ports

WARNINGS = {
    "socket_table_unavailable": "Some socket tables could not be read",
    "socket_owner_unavailable": "Some socket owners are unavailable; permissions or process exits may limit the snapshot",
    "project_details_unavailable": "Some project identities could not be read; no project ownership was guessed",
    "process_details_unavailable": "Some process details could not be read",
    "path_unreadable": "Some PATH entries could not be read",
    "resolver_configuration_unreadable": "Resolver configuration could not be read",
    "wsl_configuration_unreadable": "WSL configuration could not be read",
}
PROBE_CODES = {
    "ok": "Probe succeeded",
    "timeout": "Probe timed out",
    "certificate_invalid": "TLS certificate verification failed",
    "connection_refused": "Connection was refused",
    "probe_failed": "Probe failed",
    "http_status": "HTTP endpoint returned an error status",
}


def _safe(value) -> str:
    return "".join(
        f"\\x{ord(char):02x}" if ord(char) < 32 or ord(char) == 127 else char for char in str(value)
    )


def _port(value: str) -> int:
    try:
        port = int(value)
        if not 1 <= port <= 65535:
            raise ValueError
        return port
    except ValueError as exc:
        raise argparse.ArgumentTypeError(t("Port must be between 1 and 65535")) from exc


def _command(value: str) -> str:
    if not re.fullmatch(r"[\w][\w.+-]*", value):
        raise argparse.ArgumentTypeError(t("Provide a command name without a path or arguments"))
    return value


def _target(value: str):
    try:
        return parse_target(value)
    except (ValueError, UnicodeError) as exc:
        raise argparse.ArgumentTypeError(
            t("Use host:port, [IPv6]:port or an HTTP(S) URL without user credentials")
        ) from exc


def _common(parser, *, root=False):
    add_environment(parser)
    if not root:
        add_config(parser)
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if root else argparse.SUPPRESS,
        help=t("以 JSON 输出结果"),
    )


def add_commands(commands):
    ports = commands.add_parser(
        "ports",
        help=t("Inspect listening ports and their native processes"),
        group=t("工具箱"),
        project=False,
    )
    _common(ports, root=True)
    actions = ports.add_subparsers(dest="ports_action", required=True, metavar=t("操作"))
    for name in ("list", "show"):
        description = (
            t("List listening TCP and bound UDP ports")
            if name == "list"
            else t("Show the processes using a port")
        )
        parser = actions.add_parser(
            name,
            help=description,
            description=description,
            allow_abbrev=False,
        )
        _common(parser)
        parser.add_argument(
            "--protocol",
            choices=("all", "tcp", "udp"),
            default="all",
            help=t("Socket protocol; default: all"),
        )
        if name == "show":
            parser.add_argument("port", type=_port, metavar=t("端口"))
        parser.set_defaults(func=run)
    which = commands.add_parser(
        "which",
        help=t("Trace command candidates and links on the target PATH"),
        group=t("工具箱"),
        project=False,
    )
    _common(which, root=True)
    which.add_argument("command_name", type=_command, metavar=t("命令"))
    which.set_defaults(func=run)
    net = commands.add_parser(
        "net",
        help=t("Inspect network settings and diagnose a connection"),
        group=t("工具箱"),
        project=False,
    )
    _common(net, root=True)
    actions = net.add_subparsers(dest="net_action", required=True, metavar=t("操作"))
    for name in ("info", "check"):
        description = (
            t("Show native DNS, environment proxies and WSL networking information")
            if name == "info"
            else t("Probe direct DNS, TCP, TLS and HTTP connectivity")
        )
        parser = actions.add_parser(
            name,
            help=description,
            description=description,
            allow_abbrev=False,
        )
        _common(parser)
        if name == "check":
            parser.add_argument("target", type=_target, metavar=t("目标"))
            parser.add_argument(
                "--timeout",
                type=int,
                choices=range(1, 61),
                default=5,
                metavar=t("秒数"),
                help=t("Timeout per diagnostic stage, 1-60 seconds; default: 5"),
            )
        parser.set_defaults(func=run)


def _valid(value, args) -> bool:
    if value.get("environment") != ("windows" if args.env == "win" else "wsl"):
        return False
    if (
        type(value.get("complete")) is not bool
        or not isinstance(value.get("warnings"), list)
        or not all(isinstance(item, str) for item in value["warnings"])
    ):
        return False
    if args.command == "which":
        return (
            value.get("command") == args.command_name
            and value.get("scope") == "path"
            and type(value.get("found")) is bool
            and isinstance(value.get("candidates"), list)
            and all(
                isinstance(row, dict)
                and {"path", "resolved", "origin", "selected"}.issubset(row)
                and all(isinstance(row.get(key), str) for key in ("path", "origin"))
                and (row.get("resolved") is None or isinstance(row["resolved"], str))
                and type(row.get("selected")) is bool
                and row["selected"] == (index == 0)
                for index, row in enumerate(value["candidates"])
            )
            and value["found"] == bool(value["candidates"])
        )
    if args.command == "ports":
        return (
            value.get("operation") == args.ports_action
            and value.get("port") == getattr(args, "port", None)
            and value.get("protocol") == args.protocol
            and isinstance(value.get("entries"), list)
            and all(
                isinstance(row, dict)
                and {
                    "protocol",
                    "family",
                    "address",
                    "port",
                    "pid",
                    "process",
                    "executable",
                    "projects",
                }.issubset(row)
                and row.get("protocol") in ("tcp", "udp")
                and (args.protocol == "all" or row["protocol"] == args.protocol)
                and row.get("family") in ("ipv4", "ipv6")
                and isinstance(row.get("address"), str)
                and type(row.get("port")) is int
                and 1 <= row["port"] <= 65535
                and (args.ports_action != "show" or row["port"] == args.port)
                and (row.get("pid") is None or type(row["pid"]) is int and row["pid"] >= 0)
                and (row.get("process") is None or isinstance(row["process"], str))
                and isinstance(row.get("projects"), list)
                and all(
                    isinstance(project, dict)
                    and all(isinstance(project.get(key), str) for key in ("project", "service"))
                    for project in row["projects"]
                )
                for row in value["entries"]
            )
        )
    if value.get("operation") != args.net_action or not _valid_proxies(value.get("proxies")):
        return False
    if args.net_action == "info":
        return (
            {
                "hostname",
                "dns_servers",
                "proxies",
                "proxy_scope",
                "wsl_networking_mode",
                "wsl_configuration",
            }.issubset(value)
            and isinstance(value["hostname"], str)
            and value["proxy_scope"] == "environment"
            and isinstance(value.get("dns_servers"), list)
            and all(isinstance(item, str) for item in value["dns_servers"])
            and isinstance(value.get("wsl_configuration"), dict)
            and all(
                isinstance(key, str) and isinstance(item, str)
                for key, item in value["wsl_configuration"].items()
            )
            and (
                value.get("wsl_networking_mode") is None
                or isinstance(value["wsl_networking_mode"], str)
            )
        )
    valid = (
        value.get("target") == args.target.display
        and value.get("connection_mode") == "direct"
        and value.get("timeout") == args.timeout
        and type(value.get("healthy")) is bool
        and isinstance(value.get("stages"), list)
        and all(
            isinstance(row, dict)
            and row.get("stage") in ("dns", "tcp", "tls", "http")
            and row.get("status") in ("ok", "failed")
            and isinstance(row.get("code"), str)
            and type(row.get("duration_ms")) is int
            and row["duration_ms"] >= 0
            and all(key not in row or isinstance(row[key], str) for key in ("peer", "version"))
            and (
                "addresses" not in row
                or isinstance(row["addresses"], list)
                and all(isinstance(item, str) for item in row["addresses"])
            )
            and (
                "http_status" not in row
                or type(row["http_status"]) is int
                and 100 <= row["http_status"] <= 599
            )
            for row in value["stages"]
        )
    )
    if not valid or not value["stages"]:
        return False
    expected = ["dns", "tcp"]
    if args.target.scheme == "https":
        expected.append("tls")
    if args.target.scheme:
        expected.append("http")
    stages = value["stages"]
    return (
        [row["stage"] for row in stages] == expected[: len(stages)]
        and all(row["status"] == "ok" for row in stages[:-1])
        and value["healthy"] == (len(stages) == len(expected) and stages[-1]["status"] == "ok")
    )


def _valid_proxies(proxies) -> bool:
    return isinstance(proxies, list) and all(
        isinstance(row, dict)
        and isinstance(row.get("variable"), str)
        and type(row.get("valid")) is bool
        and (
            not row["valid"]
            or (
                all(isinstance(row.get(key), str) for key in ("host", "scheme"))
                and (
                    row.get("port") is None
                    or type(row["port"]) is int
                    and 1 <= row["port"] <= 65535
                )
                and type(row.get("credentials")) is bool
            )
        )
        for row in proxies
    )


def _arguments(args):
    if args.command == "which":
        return ["which", "--json", "--", args.command_name]
    if args.command == "ports":
        return [
            "ports",
            args.ports_action,
            "--json",
            "--protocol",
            args.protocol,
            *([str(args.port)] if args.ports_action == "show" else []),
        ]
    return [
        "net",
        args.net_action,
        "--json",
        *(
            ["--timeout", str(args.timeout), "--", args.target.raw]
            if args.net_action == "check"
            else []
        ),
    ]


def run(args):
    from ..runtimes.diagnostics import preflight, response_payload, update_instruction
    from ..runtimes.router import forward_remote

    native = "win" if os.name == "nt" else "wsl"
    if args.env != native:
        version = preflight(args.env, [args.command], args.settings)
        result = forward_remote(
            args.env, _arguments(args), settings=args.settings, capture=True, timeout=300
        )
        if result.returncode == 2:
            raise TargetError(
                "target_command_unsupported",
                message(
                    "Target controller does not support {command}; update with: {update}",
                    command=args.command,
                    update=update_instruction(args.env),
                ),
                environment=args.env,
                target_version=version,
            )
        value = response_payload(result, args.env, args.command)
        if "error" in value:
            raise RemoteError(value, result.returncode)
        expected = (
            0
            if value.get("complete") and value.get("found", True) and value.get("healthy", True)
            else 1
        )
        if not _valid(value, args) or result.returncode != expected:
            raise TargetError(
                "target_response_invalid",
                message(
                    "Target controller returned an invalid response; check both controller versions"
                ),
                environment=args.env,
            )
        code = result.returncode
    else:
        environment = "windows" if native == "win" else "wsl"
        value = {
            "schema_version": CLI_SCHEMA_VERSION,
            "action": args.command,
            "environment": environment,
        }
        if args.command == "which":
            value.update(collect_paths(args.command_name))
            code = 0 if value["found"] and value["complete"] else 1
        elif args.command == "ports":
            value.update(
                operation=args.ports_action,
                port=getattr(args, "port", None),
                protocol=args.protocol,
            )
            value.update(collect_ports(environment, port=value["port"], protocol=args.protocol))
            code = 0 if value["complete"] else 1
        else:
            value["operation"] = args.net_action
            value.update(
                collect_info()
                if args.net_action == "info"
                else check_target(args.target, args.timeout)
            )
            code = 0 if value["complete"] and value.get("healthy", True) else 1
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        display(value)
    return code


def display(value):
    if value["action"] == "ports":
        rows = [
            tuple(
                _safe(item)
                for item in (
                    row["protocol"].upper(),
                    row["address"],
                    row["port"],
                    row["pid"] if row["pid"] is not None else "-",
                    row["process"] or "-",
                    ", ".join(
                        project["project"] + "/" + project["service"] for project in row["projects"]
                    )
                    or "-",
                )
            )
            for row in value["entries"]
        ]
        print(
            format_table(
                tuple(
                    t(key)
                    for key in ("Protocol", "Address", "Port", "PID", "Process", "Project/service")
                ),
                rows,
            )
        )
        if not rows:
            print(t("No matching listening or bound ports"))
    elif value["action"] == "which":
        rows = [
            (
                "*" if row["selected"] else "",
                _safe(row["path"]),
                _safe(row["resolved"] or "-"),
                _safe(row["origin"]),
            )
            for row in value["candidates"]
        ]
        print(
            format_table(
                tuple(t(key) for key in ("Selected", "PATH candidate", "Resolved path", "Source")),
                rows,
            )
        )
        print(
            t(
                "Static PATH lookup only; shell aliases, functions and dynamic shim targets are not evaluated"
            )
        )
        if not value["found"]:
            print(t("Command was not found on the target PATH"))
    elif value["operation"] == "info":
        print(t("DNS servers: {servers}", servers=_safe(", ".join(value["dns_servers"]) or "-")))
        print(
            t(
                "WSL effective networking mode: {mode}",
                mode=_safe(value["wsl_networking_mode"] or t("Unknown or not applicable")),
            )
        )
        for key, item in value["wsl_configuration"].items():
            print(t("WSL declared setting: {key} = {value}", key=_safe(key), value=_safe(item)))
        _proxies(value)
    else:
        print(t("Direct connection diagnosis: {target}", target=_safe(value["target"])))
        for row in value["stages"]:
            details = row.get(
                "http_status",
                row.get("version", row.get("peer", ", ".join(row.get("addresses", [])))),
            )
            print(
                f"  {row['stage'].upper()}: {t('Passed') if row['status'] == 'ok' else t('Failed')} ({row['duration_ms']} ms) {_safe(details)} {_safe(t(PROBE_CODES.get(row['code'], row['code'])))}"
            )
        _proxies(value)
        print(t("Each stage uses its own timeout; later stages are not attempted after a failure"))
    for code in value["warnings"]:
        print(warning(_safe(t(WARNINGS.get(code, code)))))


def _proxies(value):
    print(t("Environment proxies (credentials hidden; connection probes are direct):"))
    for proxy in value["proxies"]:
        endpoint = (
            f"{proxy.get('scheme', '?')}://{proxy.get('host', '?')}:{proxy.get('port') or '-'}"
            if proxy.get("valid")
            else t("Invalid proxy setting")
        )
        print(f"  {_safe(proxy['variable'])}: {_safe(endpoint)}")
    if not value["proxies"]:
        print("  -")
