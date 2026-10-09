from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path

from .i18n import language, t
from .presentation import label
from .settings import Settings, load_settings, native_path


def _description(value: str | dict) -> str:
    return (
        value.get(language(), value.get("zh", value.get("en", "")))
        if isinstance(value, dict)
        else value
    )


def collect_sysinfo(
    config_path: str | Path | None = None, *, settings: Settings | None = None
) -> dict:
    """Read platform metadata, PATH availability and configured directories only.

    Never execute tools, profiles or project code, or read arbitrary personal fields.
    Disabled sections perform no probes. Configured lists replace native defaults.
    """
    settings = settings or load_settings(config_path)
    cfg = settings.data["sysinfo"]
    sections = cfg["sections"]
    system = (
        {
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        }
        if "system" in sections
        else {}
    )
    directories = []
    if "directories" in sections:
        for item in cfg["directories"]:
            path = native_path(item["path"], settings.path.parent)
            try:
                exists = path.is_dir()
            except OSError:
                exists = False
            if exists or cfg["show_missing"]:
                directories.append(
                    {
                        "command": item["command"],
                        "description": _description(item["description"]),
                        "path": str(path),
                        "exists": exists,
                    }
                )
    tools, commands = [], []
    if "tools" in sections:
        for item in cfg["tools"]:
            available = shutil.which(item["command"]) is not None
            if available or cfg["show_missing"]:
                tools.append(
                    {"command": item["command"], "description": _description(item["description"])}
                )
                commands.append({"command": item["command"], "available_on_path": available})
    return {
        "system": system,
        "config_state": settings.state,
        "sections": sections,
        "directories": directories,
        "tools": tools,
        "commands": commands,
    }


def print_sysinfo(result: dict, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    system = result["system"]
    if system:
        print(t("系统：{os} {release} ({architecture})", **system))
        print(t("CPU 逻辑核心：{cpu_count}；当前 Python：{python}", **system))
    print(t("本机配置：{state}", state=label(result["config_state"])))
    if "directories" in result["sections"]:
        print(t("目录检查："))
        for directory in result["directories"]:
            marker = t("存在") if directory["exists"] else t("不存在")
            print(
                f"  [{marker}] {directory['command']} {directory['path']} ({directory['description']})"
            )
    if "tools" in result["sections"]:
        print(t("PATH 命令可用性（不执行命令或检查更新）："))
        descriptions = {item["command"]: item["description"] for item in result["tools"]}
        for command in result["commands"]:
            marker = t("可用") if command["available_on_path"] else t("未找到")
            description = descriptions[command["command"]]
            suffix = f" ({description})" if description else ""
            print(f"  [{marker}] {command['command']}{suffix}")
