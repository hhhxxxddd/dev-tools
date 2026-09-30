from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config/sysinfo.local.json"
PROBE_COMMANDS = (
    "git",
    "mise",
    "node",
    "python",
    "python3",
    "uv",
    "pnpm",
    "winget",
    "scoop",
    "wsl",
)


def _label(value: object) -> str:
    # Local labels are plain text, never commands or terminal escape sequences.
    return (
        "".join(char for char in value if char.isprintable())[:200]
        if isinstance(value, str)
        else ""
    )


def collect_sysinfo(config_path: str | Path | None = None) -> dict:
    """Inspect only platform metadata, PATH availability, and explicitly configured directories.

    Never execute discovered tools, load shell profiles, scan projects, read SSH files,
    or include arbitrary config/environment fields in the result.
    """
    path = Path(config_path or os.environ.get("DEV_TOOLS_SYSINFO_CONFIG") or DEFAULT_CONFIG)
    config: dict = {}
    state = "missing"
    warnings: list[str] = []
    try:
        content = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(content, dict):
            raise TypeError("config must be an object")
        config = content
        state = "loaded"
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        state = "invalid"
        warnings.append("本机配置无法读取或格式无效；已回退到通用只读探测。")

    directories = []
    native_key = "windows" if os.name == "nt" else "wsl"
    entries = config.get("directories", [])
    if not isinstance(entries, list):
        entries = []
        warnings.append("directories 应为列表；已忽略。")
    for item in entries:
        if not isinstance(item, dict):
            continue
        raw_path = item.get(native_key)
        if not isinstance(raw_path, str) or not raw_path:
            continue
        directory = Path(raw_path).expanduser()
        try:
            exists = directory.is_dir()
        except OSError:
            exists = False
        directories.append(
            {
                "command": _label(item.get("command")),
                "description": _label(item.get("description")),
                "path": str(directory),
                "exists": exists,
            }
        )

    tools = []
    entries = config.get("tools", [])
    if not isinstance(entries, list):
        entries = []
        warnings.append("tools 应为列表；已忽略。")
    for item in entries:
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        if not isinstance(command, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", command):
            continue
        tools.append({"command": command, "description": _label(item.get("description"))})

    # Command availability refers only to PATH, not shell functions or service health.
    return {
        "system": {
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "config_state": state,
        "directories": directories,
        "tools": tools,
        "commands": [
            {"command": command, "available_on_path": shutil.which(command) is not None}
            for command in PROBE_COMMANDS
        ],
        "warnings": warnings,
    }


def print_sysinfo(result: dict, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    system = result["system"]
    print(f"系统：{system['os']} {system['release']} ({system['architecture']})")
    print(f"CPU 逻辑核心：{system['cpu_count']}；当前 Python：{system['python']}")
    print(f"本机配置：{result['config_state']}")
    for directory in result["directories"]:
        marker = "OK" if directory["exists"] else "不存在"
        print(
            f"  [{marker}] {directory['command']} {directory['path']} ({directory['description']})"
        )
    if result["tools"]:
        print("配置的工具入口（不验证 shell 函数或服务）：")
        for tool in result["tools"]:
            print(f"  {tool['command']}：{tool['description']}")
    print("PATH 命令可用性（不执行命令或检查更新）：")
    for command in result["commands"]:
        marker = "OK" if command["available_on_path"] else "--"
        print(f"  [{marker}] {command['command']}")
    for warning in result["warnings"]:
        print(f"警告：{warning}", file=sys.stderr)
