from __future__ import annotations

import argparse
import json
import sys

from .scanner import ScanResult, render_mise, scan_project


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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dev-tools")
    commands = result.add_subparsers(dest="command", required=True)
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
