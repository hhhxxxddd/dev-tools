"""Command definitions supply both native entrypoints' grouped help."""

from __future__ import annotations

import argparse
import os
import re
import sys

from .i18n import t


class LocalizedHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, prefix=t("用法：") if prefix is None else prefix)

    def start_section(self, heading):
        super().start_section(
            {"positional arguments": t("位置参数"), "options": t("选项")}.get(heading, heading)
        )


class LocalizedArgumentParser(argparse.ArgumentParser):
    deployment_notice = None

    def __init__(self, *args, **kwargs):
        add_help = kwargs.pop("add_help", True)
        kwargs.setdefault("formatter_class", LocalizedHelpFormatter)
        super().__init__(*args, add_help=False, **kwargs)
        if add_help:
            self.add_argument("-h", "--help", action="help", help=t("显示帮助并退出"))

    def print_help(self, file=None):
        super().print_help(file)
        if self.deployment_notice:
            notice = self.deployment_notice()
            if notice:
                self._print_message("\n" + notice + "\n", file or sys.stdout)

    def error(self, message):
        # argparse exposes only the formatted diagnostic to error(). Match its own
        # documented parser messages, never project text or external tool output.
        patterns = (
            (r"argument (.+?): (.*)", "参数 {argument}：{detail}", ("argument", "detail")),
            (
                r"invalid choice: (.+) \(choose from (.+)\)",
                "无效选项：{value}（可选：{choices}）",
                ("value", "choices"),
            ),
            (r"unrecognized arguments: (.*)", "无法识别的参数：{detail}", ("detail",)),
            (r"the following arguments are required: (.*)", "缺少必需参数：{detail}", ("detail",)),
            (r"invalid (.+) value: (.*)", "无效的 {kind} 值：{value}", ("kind", "value")),
        )

        def localize(value):
            from .i18n import language

            if language() == "en":
                return value
            for pattern, template, names in patterns:
                match = re.fullmatch(pattern, value)
                if match:
                    parameters = dict(zip(names, match.groups(), strict=True))
                    if "detail" in parameters:
                        parameters["detail"] = localize(parameters["detail"])
                    return template.format(**parameters)
            return t(value)

        self.print_usage(sys.stderr)
        self.exit(2, t("{prog}：错误：{detail}\n", prog=self.prog, detail=localize(message)))


def add_config(parser: argparse.ArgumentParser, *, root: bool = False) -> None:
    parser.add_argument(
        "--config",
        metavar=t("配置文件"),
        default=None if root else argparse.SUPPRESS,
        help=t("指定 dev-tools 本机配置（TOML 格式）"),
    )


def add_environment(parser: argparse.ArgumentParser, *, root: bool = False) -> None:
    parser.add_argument(
        "-e",
        "--env",
        choices=("win", "wsl"),
        metavar=t("环境"),
        default=("win" if os.name == "nt" else "wsl") if root else argparse.SUPPRESS,
        help=t("执行环境：win 为 Windows，wsl 为 WSL；省略时使用当前平台"),
    )


class CommandCatalog:
    def __init__(self, parser: argparse.ArgumentParser):
        self.root = parser
        self.commands = parser.add_subparsers(
            dest="command", required=True, metavar=t("命令"), help=t("要执行的命令，参见下方列表")
        )
        self.parsers: dict[str, argparse.ArgumentParser] = {}
        self.groups: dict[str, list[tuple[str, str]]] = {}

    def add_parser(self, name: str, *, help: str, group: str | None = None, project: bool = True):
        parser = self.commands.add_parser(name, description=help, allow_abbrev=False)
        self.parsers[name] = parser
        self.groups.setdefault(group or t("常用命令"), []).append((name, help))
        add_config(parser)
        if project:
            add_environment(parser)
            parser.set_defaults(project_command=name)
        return parser

    def finish(self) -> None:
        helper = self.add_parser(
            "help", help=t("查看全部命令或某个命令的参数"), group=t("辅助命令"), project=False
        )
        helper.add_argument(
            "topic",
            nargs="?",
            choices=tuple(self.parsers),
            metavar=t("命令"),
            help=t("要查看的命令；省略时列出全部命令"),
        )
        helper.set_defaults(func=self.show_help)
        lines = []
        for group in (t("常用命令"), t("进阶命令"), t("辅助命令")):
            lines.append(t("{group}：", group=group))
            lines.extend(f"  {name:<12} {help}" for name, help in self.groups.get(group, []))
            lines.append("")
        lines.extend(
            (
                t("用法：dev-tools help <命令> 或 dev-tools <命令> --help"),
                t("示例：dev-tools -e wsl list；dev-tools prepare demo --dry-run"),
                t("项目名可省略：当前目录必须唯一匹配已注册项目的源码或工作目录（含子目录）。"),
                t("start 自动准备环境，运行期间跟进依赖变化；mise 的诊断命令为 mise doctor。"),
            )
        )
        self.root.epilog = "\n".join(lines)

    def show_help(self, args: argparse.Namespace) -> int:
        (self.parsers[args.topic] if args.topic else self.root).print_help()
        return 0
