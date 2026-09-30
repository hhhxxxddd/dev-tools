from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .report import collect_report, render_report
from .scanner import ScanResult, render_mise, scan_project
from .sysinfo import collect_sysinfo, print_sysinfo

PROJECT_ISOLATION_CONFIG = Path(__file__).resolve().parents[2] / "config/project-isolation.toml"


def _print_human(result: ScanResult) -> None:
    print(f"项目：{result.root}")
    if result.existing_config:
        print(f"现有配置：{result.existing_config}")
    if result.tools:
        print("识别到的工具：")
        for name, item in result.tools.items():
            print(f"  {name} = {item.version}  ({item.source}, {item.confidence})")
    else:
        print("未识别到可确定的开发工具版本。")
    for name, wrapper in result.wrappers.items():
        print(f"  {name} wrapper = {wrapper['version']}  ({wrapper['source']})")
    for warning in result.warnings:
        print(f"警告：{warning}")
    for conflict in result.conflicts:
        print(f"冲突：{conflict.tool}", file=sys.stderr)
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
    return 2 if result.conflicts else 0


def cmd_init(args: argparse.Namespace) -> int:
    result = scan_project(args.path)
    if result.existing_config:
        action = "preserved"
        content = result.existing_config.read_text(encoding="utf-8")
    elif result.conflicts:
        action = "conflict"
        content = None
    elif not result.tools:
        action = "no-tools"
        content = None
    else:
        content = render_mise(result)
        action = "preview" if args.dry_run else "created"
        if not args.dry_run:
            (result.root / "mise.toml").write_text(content, encoding="utf-8", newline="\n")
    if args.json:
        print(
            json.dumps(
                _payload(result, action=action, content=content), ensure_ascii=False, indent=2
            )
        )
    else:
        _print_human(result)
        if action == "created":
            print(f"已生成：{result.root / 'mise.toml'}")
        elif action == "preview" and content:
            print("\n将生成：\n")
            print(content, end="")
        elif action == "preserved":
            print("保留现有 mise 配置，未改写。")
        elif action == "conflict":
            print("存在同优先级版本冲突，未生成 mise.toml。", file=sys.stderr)
    return 2 if action == "conflict" else 0


def cmd_prepare(args: argparse.Namespace) -> int:
    result = scan_project(args.path)
    if result.conflicts:
        _print_human(result)
        print("存在同优先级版本冲突，未安装开发工具。", file=sys.stderr)
        return 2
    if result.existing_config is None:
        print(
            "项目根目录没有 mise.toml 或 .mise.toml；请先运行 dev-tools project init。",
            file=sys.stderr,
        )
        return 1
    if shutil.which("mise") is None:
        print("mise 不在 PATH 中，无法准备项目环境。", file=sys.stderr)
        return 1

    command = ["mise", "--yes", "-C", str(result.root), "install"]
    if args.dry_run:
        command.append("--dry-run")
    environment = os.environ.copy()
    environment["MISE_GLOBAL_CONFIG_FILE"] = str(PROJECT_ISOLATION_CONFIG)
    completed = subprocess.run(command, check=False, env=environment)
    return completed.returncode


def cmd_sysinfo(args: argparse.Namespace) -> int:
    print_sysinfo(collect_sysinfo(args.config), as_json=args.json)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    value = collect_report(args.config, refresh=not args.no_refresh, timeout=args.timeout)
    output = json.dumps(value, ensure_ascii=False, indent=2) if args.json else render_report(value)
    print(output)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dev-tools")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = result.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="刷新 Git/软件索引并生成日报；不拉取、安装或升级")
    report.add_argument("--config", help="每台机器独立的项目根目录 JSON 配置")
    report.add_argument("--json", action="store_true")
    report.add_argument(
        "--no-refresh", action="store_true", help="不 fetch/刷新软件索引；仍查询 CLI 最新版本"
    )
    report.add_argument("--timeout", type=int, default=90, choices=range(1, 601), metavar="SECONDS")
    report.add_argument("--output", help="显式保存报告路径")
    report.set_defaults(func=cmd_report)
    sysinfo = commands.add_parser("sysinfo", help="只读查看当前机器与本机开发入口")
    sysinfo.add_argument("--config", help="指定本机 JSON 配置；不自动创建或改写")
    sysinfo.add_argument("--json", action="store_true")
    sysinfo.set_defaults(func=cmd_sysinfo)
    project = commands.add_parser("project", help="扫描并规范项目开发工具版本")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    scan = project_commands.add_parser("scan", help="只扫描项目版本声明")
    scan.add_argument("path", nargs="?", default=".")
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=cmd_scan)
    initialize = project_commands.add_parser("init", help="缺少配置时生成 mise.toml")
    initialize.add_argument("path", nargs="?", default=".")
    initialize.add_argument("--dry-run", action="store_true")
    initialize.add_argument("--json", action="store_true")
    initialize.set_defaults(func=cmd_init)
    prepare = project_commands.add_parser(
        "prepare",
        help="安装项目 mise 配置声明但尚未安装的版本",
    )
    prepare.add_argument("path", nargs="?", default=".")
    prepare.add_argument("--dry-run", action="store_true")
    prepare.set_defaults(func=cmd_prepare)
    return result


def main() -> None:
    try:
        args = parser().parse_args()
        raise SystemExit(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"dev-tools: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
