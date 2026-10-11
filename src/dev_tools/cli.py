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
from .i18n import language_scope, message, render, t
from .presentation import label
from .project_models import ScanResult
from .projects.cli import add_commands
from .projects.initialization import initialize
from .projects.models import ProjectError, validate_name
from .report import collect_report, render_report
from .scanner import scan_project
from .schemas import CLI_SCHEMA_VERSION
from .settings import SettingsError, config_command, load_settings, split_config
from .sysinfo import collect_sysinfo, print_sysinfo


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
    for warning in result.warnings:
        print(t("警告：{warning}", warning=render(warning)))
    for item in result.unresolved:
        print(
            t(
                "待确定：{tool} ({value}): {raw} {reason}",
                tool=item.tool,
                value=item.source,
                raw=item.raw,
                reason=render(item.reason),
            )
        )
    for diagnostic in result.diagnostics:
        print(
            t(
                "元数据错误：{value}: {message}",
                value=diagnostic.source,
                message=render(diagnostic.message),
            ),
            file=sys.stderr,
        )
    for conflict in result.conflicts:
        print(t("冲突：{tool}", tool=conflict.tool), file=sys.stderr)
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
    with language_scope(settings.language):
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
    commands.finish()
    return result


def _dispatch(arguments: list[str], forwarded: list[str], environment: str, settings) -> None:
    from .runtimes.router import forward_remote

    command_parser = parser()
    command_parser.deployment_notice = lambda: deployment_notice(settings)
    args = command_parser.parse_args(arguments or ["help"])
    set_request(args)
    args.settings = settings
    if args.command == "config" and args.config_action == "edit" and args.json:
        command_parser.error(t("config edit does not support --json"))
    if args.command == "self":
        if args.json and args.self_action != "status":
            command_parser.error(t("self --json requires status"))
        if args.source and args.self_action == "status":
            command_parser.error(t("self --source requires update or upgrade"))
    for field in ("name", "new_name", "service"):
        value = getattr(args, field, None)
        if value and not (args.command == "logs" and field == "service" and value.startswith("__")):
            try:
                validate_name(value)
            except ProjectError:
                command_parser.error(t("Invalid project, task or service name"))
    if args.command in {"config", "sysinfo", "report"} and arguments != forwarded:
        command_parser.error(t("-e/--env 只用于项目命令；辅助命令使用当前平台"))
    if settings.errors and args.command not in {"config", "help"}:
        raise SettingsError(settings.errors[0], language=settings.language)
    if hasattr(args, "project_command"):
        if args.project_command == "list":
            if args.all_environments and arguments != forwarded:
                command_parser.error(t("list --all 不能与 -e/--env 同时使用"))
        else:
            if getattr(args, "json", False):
                result = forward_remote(
                    environment, forwarded, settings=settings, capture=True, timeout=None
                )
                if result is not None:
                    try:
                        value = json.loads(result.stdout)
                        if (
                            not isinstance(value, dict)
                            or type(value.get("schema_version")) is not int
                            or value.get("schema_version") != CLI_SCHEMA_VERSION
                            or not isinstance(value.get("action"), str)
                            or args.command != "init"
                            and value.get("action") != args.command
                            or result.returncode not in {0, 1, 2, 130}
                        ):
                            raise ValueError
                    except TypeError, ValueError:
                        raise ProjectError(
                            message("Invalid JSON response from target environment")
                        ) from None
                    if result.stderr:
                        print(result.stderr, end="", file=sys.stderr)
                    print(json.dumps(value, ensure_ascii=False, indent=2))
                    raise SystemExit(exit_code(result.returncode))
            else:
                code = forward_remote(environment, forwarded, settings=settings)
                if code is not None:
                    raise SystemExit(exit_code(code))
    raise SystemExit(exit_code(args.func(args)))


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
        try:
            environment, forwarded = split_environment(local)
        except ValueError as exc:
            emit_error(exc, code="invalid_arguments")
            raise SystemExit(2) from None
        try:
            _dispatch(local, forwarded, environment, settings)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            emit_error(exc)
            raise SystemExit(1) from None
        except KeyboardInterrupt as exc:
            emit_error(exc)
            raise SystemExit(130) from None


if __name__ == "__main__":
    main()
