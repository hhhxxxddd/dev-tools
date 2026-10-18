from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .cli_errors import emit_error, exit_code, request_scope, set_request
from .command_help import CommandCatalog, LocalizedArgumentParser, add_config, add_environment
from .controller import add_commands as add_controller_commands
from .controller import deployment_notice
from .hosts import add_commands as add_hosts_commands
from .i18n import language_scope, message, render, t
from .presentation import error, label, warning
from .project_models import ScanResult
from .projects.cli import add_commands
from .projects.initialization import initialize
from .projects.models import ProjectError, validate_name
from .report import collect_report, render_report
from .scanner import scan_project
from .settings import SettingsError, config_command, load_settings, split_config
from .ssh import add_commands as add_ssh_commands
from .sysinfo import collect_sysinfo, print_sysinfo
from .toolbox.cli import add_commands as add_toolbox_commands


def _print_human(result: ScanResult) -> None:
    print(t("项目：{root}", root=result.root))
    if result.existing_config:
        print(t("现有配置：{existing_config}", existing_config=result.existing_config))
    if result.tools:
        print(t("识别到的工具："))
        for name, item in result.tools.items():
            print(f"  {name} = {item.version}  ({item.source}, {label(item.confidence)})")
    else:
        print(t("未识别到可确定的开发工具版本。"))
    for name, wrapper in result.wrappers.items():
        print(
            t(
                "  {name} 项目包装器 = {version}  ({value})",
                name=name,
                version=wrapper.version,
                value=wrapper.source,
            )
        )
    for notice in result.warnings:
        print(warning(t("警告：{warning}", warning=render(notice))))
    for item in result.unresolved:
        print(
            warning(
                t(
                    "待确定：{tool} ({value}): {raw} {reason}",
                    tool=item.tool,
                    value=item.source,
                    raw=item.raw,
                    reason=render(item.reason),
                )
            )
        )
    for diagnostic in result.diagnostics:
        print(
            error(
                t(
                    "元数据错误：{value}: {message}",
                    value=diagnostic.source,
                    message=render(diagnostic.message),
                ),
                stream=sys.stderr,
            ),
            file=sys.stderr,
        )
    for conflict in result.conflicts:
        print(error(t("冲突：{tool}", tool=conflict.tool), stream=sys.stderr), file=sys.stderr)
        for version, sources in conflict.versions.items():
            print(f"  {version}: {', '.join(sources)}", file=sys.stderr)


def _payload(result: ScanResult, *, action: str, content: str | None = None) -> dict:
    value = result.as_dict()
    value["action"] = action
    if content is not None:
        value["content"] = content
    return value


def cmd_scan(args: argparse.Namespace) -> int:
    result = scan_project(args.path)
    if args.json:
        print(json.dumps(_payload(result, action="scan"), ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 1 if result.blocked else 0


def cmd_init(args: argparse.Namespace) -> int:
    payload, blocked = initialize(
        args.path,
        name=getattr(args, "name", None),
        runtime=getattr(args, "runtime", "auto"),
        toolchain=getattr(args, "toolchain", "mise"),
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(t("项目：{root}", root=args.path))
        print(t("mise 配置：{action}", action=label(payload["action"])))
        project_config = payload["project_config"]
        print(
            t(
                "项目配置：{action}；{path}",
                action=label(project_config["action"]),
                path=project_config["path"],
            )
        )
        for diagnostic in payload.get("diagnostics", []):
            print(f"{diagnostic['source']}: {render(diagnostic['message'])}", file=sys.stderr)
        for item in payload.get("unresolved", []):
            print(f"{item['tool']}: {render(item['reason'])}", file=sys.stderr)
    return 1 if blocked else 0


def cmd_sysinfo(args: argparse.Namespace) -> int:
    settings = getattr(args, "settings", None) or load_settings(args.config)
    print_sysinfo(collect_sysinfo(settings=settings), as_json=args.json)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    value = collect_report(
        args.config,
        refresh=args.refresh,
        timeout=args.timeout,
        settings=getattr(args, "settings", None),
    )
    output = json.dumps(value, ensure_ascii=False, indent=2) if args.json else render_report(value)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    print(output)
    incomplete = bool(value.get("discovery_issues")) or any(
        collector.get("status") in {"failed", "timeout", "partial", "parse-error"}
        for collector in value.get("collectors", {}).values()
    )
    incomplete = incomplete or any(
        repository.get("fetch", {}).get("status") in {"failed", "timeout", "partial", "parse-error"}
        or repository.get("worktree") == "error"
        or repository.get("tracking") == "tracking-error"
        for repository in value.get("repositories", [])
    )
    return 1 if incomplete else 0


def parser() -> argparse.ArgumentParser:
    result = LocalizedArgumentParser(
        prog="dev-tools",
        description=t("项目发现、准备与 Windows/WSL 运行控制"),
        allow_abbrev=False,
    )
    result.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help=t("显示程序版本并退出"),
    )
    add_environment(result, root=True)
    add_config(result, root=True)
    commands = CommandCatalog(result)
    config = commands.add_parser(
        "config", help=t("查看、编辑或校验 dev-tools 本机配置"), group=t("辅助命令"), project=False
    )
    add_environment(config)
    config.add_argument(
        "config_action",
        nargs="?",
        choices=("edit", "check"),
        metavar=t("操作"),
        help=t("edit 编辑；check 校验；省略时查看生效配置及来源"),
    )
    config.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    config.set_defaults(func=config_command)
    report = commands.add_parser(
        "report",
        help=t("刷新 Git/软件索引并生成日报；不拉取、安装或升级"),
        group=t("辅助命令"),
        project=False,
    )
    add_environment(report)
    report.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    report.add_argument(
        "--refresh",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=t("是否刷新 Git 引用和软件索引，默认读取配置"),
    )
    report.add_argument(
        "--timeout",
        type=int,
        default=None,
        choices=range(1, 601),
        metavar=t("秒数"),
        help=t("单项收集的超时秒数，范围 1～600，默认读取配置"),
    )
    report.add_argument("--output", metavar=t("报告文件"), help=t("将报告同时保存到指定文件"))
    report.set_defaults(func=cmd_report)
    sysinfo = commands.add_parser(
        "sysinfo", help=t("只读查看当前机器与本机开发入口"), group=t("辅助命令"), project=False
    )
    add_environment(sysinfo)
    sysinfo.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    sysinfo.set_defaults(func=cmd_sysinfo)
    scan = commands.add_parser("scan", help=t("只扫描项目版本声明"), group=t("进阶命令"))
    scan.add_argument(
        "path", nargs="?", default=".", metavar=t("目录"), help=t("项目源码目录，默认当前目录")
    )
    scan.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    scan.set_defaults(func=cmd_scan)
    initialize = commands.add_parser("init", help=t("生成缺少的 dev-tools.toml 和根级 mise 配置"))
    initialize.add_argument(
        "path", nargs="?", default=".", metavar=t("目录"), help=t("项目源码目录，默认当前目录")
    )
    initialize.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    initialize.set_defaults(func=cmd_init)
    initialize.add_argument(
        "--name", metavar=t("项目名"), help=t("新项目配置中的名称；省略时按目录生成")
    )
    initialize.add_argument(
        "--runtime",
        choices=("auto", "host", "compose"),
        default="auto",
        metavar=t("运行方式"),
        help=t("auto 自动识别（默认）；host 本机进程；compose 容器编排"),
    )
    initialize.add_argument(
        "--toolchain",
        choices=("mise", "system"),
        default="mise",
        metavar=t("工具来源"),
        help=t("mise 使用项目根版本声明（默认）；system 使用已有系统工具"),
    )
    add_commands(commands)
    add_controller_commands(commands)
    add_ssh_commands(commands)
    add_hosts_commands(commands)
    add_toolbox_commands(commands)
    commands.finish()
    return result


def _dispatch(arguments: list[str], forwarded: list[str], environment: str, settings) -> None:
    from .cli_errors import TargetError
    from .runtimes.diagnostics import preflight, response_payload, target_status, unavailable_target
    from .runtimes.router import forward_remote

    command_parser = parser()
    # The catalog owns this policy for every toolbox command, including commands
    # such as which that take a positional argument instead of subcommands.
    if len(forwarded) == 1 and forwarded[0] in command_parser.toolbox_commands:
        arguments = [*arguments, "--help"]
        forwarded = [*forwarded, "--help"]
    scope = forwarded[: forwarded.index("--")] if "--" in forwarded else forwarded
    if environment != ("win" if os.name == "nt" else "wsl") and (
        any(flag in scope for flag in ("-h", "--help", "-v", "--version"))
        or not scope
        or scope[0] == "help"
    ):
        status = target_status(environment, settings)
        if status["state"] != "installed":
            raise unavailable_target(environment, status)
        if any(flag in scope for flag in ("-v", "--version")):
            print(f"dev-tools {status['version']} ({environment})")
            raise SystemExit(0)
        requested = scope or ["--help"]
        if requested[0] == "help":
            requested = [*requested[1:], "--help"]
        print(
            t(
                "Target controller: {environment} {version}",
                environment=environment,
                version=status["version"],
            )
        )
        result = forward_remote(environment, requested, settings=settings, capture=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            raise TargetError(
                "target_command_unsupported" if result.returncode == 2 else "target_unavailable",
                message("Target help query failed in {environment}", environment=environment),
                environment=environment,
                target_version=status["version"],
                returncode=result.returncode,
            )
        print(result.stdout, end="")
        raise SystemExit(0)

    command_parser.deployment_notice = lambda: deployment_notice(settings)
    args = command_parser.parse_args(arguments or ["help"])
    set_request(args)
    args.settings = settings
    if getattr(args, "list_edit", False) and args.json:
        command_parser.error(t("list --edit does not support --json"))
    if args.command == "hosts" and args.hosts_action == "edit" and args.json:
        command_parser.error(t("hosts edit does not support --json"))
    if args.command == "ssh" and args.ssh_action in {"edit", "connect"} and args.json:
        command_parser.error(
            t("ssh {operation} does not support --json", operation=args.ssh_action)
        )
    if args.command == "config" and args.config_action == "edit" and args.json:
        command_parser.error(t("config edit does not support --json"))
    if args.command == "self":
        if args.json and args.self_action != "status":
            command_parser.error(t("self --json requires status"))
        if args.source and args.self_action in {"status", "recover"}:
            command_parser.error(t("self --source requires update or upgrade"))
    if args.command == "logs" and args.service is not None and args.service_option is not None:
        command_parser.error(t("logs 的服务位置参数不能与 --service 同时使用"))
    for field in ("name", "new_name", "service", "service_option"):
        value = getattr(args, field, None)
        if value is not None and not (
            args.command == "logs"
            and field in {"service", "service_option"}
            and value in {"__sync", "__watch"}
        ):
            try:
                validate_name(value)
            except ProjectError:
                command_parser.error(t("Invalid project, task or service name"))
    if args.command in {"config", "sysinfo", "report"}:
        from .runtimes.diagnostics import forward_command

        code = forward_command(environment, forwarded, settings, as_json=args.json)
        if code is not None:
            raise SystemExit(code)
    if settings.errors and args.command not in {"config", "help"}:
        raise SettingsError(settings.errors[0], language=settings.language)
    if hasattr(args, "project_command"):
        if args.project_command == "list":
            if args.all_environments and arguments != forwarded:
                command_parser.error(t("list --all 不能与 -e/--env 同时使用"))
        else:
            if (
                args.project_command == "start"
                and args.all_projects
                and (args.name or args.service)
            ):
                command_parser.error(
                    t("start --all cannot be combined with a project name or --service")
                )
            preflight(environment, forwarded, settings)
            if os.name == "nt" and environment == "wsl" and args.project_command == "status":
                from .projects.cli import remote_wsl_status

                raise SystemExit(remote_wsl_status(forwarded, settings, args.json))
            long_running = (
                args.project_command
                in {
                    "register",
                    "prepare",
                    "start",
                    "stop",
                    "restart",
                    "build",
                    "rename",
                    "unregister",
                }
                or args.project_command == "logs"
                and args.follow
            )
            timeout = None if long_running else 30
            if getattr(args, "json", False):
                result = forward_remote(
                    environment, forwarded, settings=settings, capture=True, timeout=timeout
                )
                if result is not None:
                    value = response_payload(result, environment, args.command)
                    print(json.dumps(value, ensure_ascii=False, indent=2))
                    raise SystemExit(exit_code(result.returncode))
            else:
                code = forward_remote(environment, forwarded, settings=settings, timeout=timeout)
                if code is not None:
                    raise SystemExit(exit_code(code))
    code = args.func(args)
    # Interactive SSH sessions retain the native client's/session's exit status.
    raise SystemExit(
        code if args.command == "ssh" and args.ssh_action == "connect" else exit_code(code)
    )


def main(arguments: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    with request_scope(arguments):
        _main(arguments)


def _main(arguments: list[str]) -> None:
    from .runtimes.router import split_environment

    try:
        explicit, local = split_config(arguments)
    except SettingsError as exc:
        emit_error(exc, code="invalid_arguments")
        raise SystemExit(2) from None
    try:
        # Native supervisors must not depend on interactive user preferences.
        if "_worker" in local:
            environment, forwarded = split_environment(local)
            if forwarded[:1] == ["_worker"]:
                if len(forwarded) != 2:
                    raise ValueError(message("internal worker requires PROJECT:SERVICE"))
                from .projects.cli import run

                raise SystemExit(
                    run(
                        argparse.Namespace(
                            project_command="_worker", env=environment, instance=forwarded[1]
                        )
                    )
                )
        settings = load_settings(explicit, tolerate_invalid=True)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        with language_scope(getattr(exc, "language", "zh")):
            emit_error(exc)
        raise SystemExit(1) from None
    except KeyboardInterrupt as exc:
        emit_error(exc)
        raise SystemExit(130) from None
    # Transport carries the calling config's resolved language, never a public
    # language override or a foreign machine's settings file. Do not leak it to tasks.
    inherited = os.environ.pop("DEV_TOOLS_TRANSPORT_LANGUAGE", None)
    with language_scope(inherited if inherited in {"zh", "en"} else settings.language):
        if local == ["_controller-restore"]:
            from .controller_update import recovery_path, restore_receipt

            try:
                raise SystemExit(restore_receipt(recovery_path()))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                emit_error(exc)
                raise SystemExit(1) from None
            except KeyboardInterrupt as exc:
                emit_error(exc)
                raise SystemExit(130) from None
        try:
            environment, forwarded = split_environment(local)
        except ValueError as exc:
            emit_error(exc, code="invalid_arguments")
            raise SystemExit(2) from None
        try:
            _dispatch(local, forwarded, environment, settings)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            emit_error(exc)
            from .cli_errors import RemoteError

            raise SystemExit(
                exit_code(exc.returncode) if isinstance(exc, RemoteError) else 1
            ) from None
        except KeyboardInterrupt as exc:
            emit_error(exc)
            raise SystemExit(130) from None


if __name__ == "__main__":
    main()
