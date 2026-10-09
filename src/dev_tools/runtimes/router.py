"""Transport only: native commands always enter the common project CLI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ..i18n import language, message
from ..settings import Settings, load_settings

REPOSITORY = Path(__file__).resolve().parents[3]


def split_environment(arguments: list[str]) -> tuple[str, list[str]]:
    forwarded = []
    target = "win" if os.name == "nt" else "wsl"
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            forwarded.extend(arguments[index:])
            break
        if token in {"--env", "-e"}:
            index += 1
            if index == len(arguments):
                raise ValueError(message("-e/--env requires win or wsl"))
            target = arguments[index]
        elif token.startswith("--env="):
            target = token.split("=", 1)[1]
        elif token.startswith("-e") and len(token) > 2:
            target = token[2:].removeprefix("=")
        else:
            forwarded.append(token)
        index += 1
    if target not in {"win", "wsl"}:
        raise ValueError(message("-e/--env requires win or wsl"))
    return target, forwarded


def source_argument(arguments: list[str]) -> int | None:
    index = 1
    while index < len(arguments):
        token = arguments[index]
        if token in {"--name", "--user", "--runtime", "--toolchain"}:
            index += 2
            continue
        if not token.startswith("-"):
            return index
        index += 1
    return None


def _windows_path(value: str | Path) -> str:
    value = str(value)
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
        return value
    result = subprocess.run(
        ["wslpath", "-w", str(Path(value).expanduser().resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        raise ValueError(result.stderr.strip() or f"cannot translate Windows path: {value}")
    return result.stdout.strip()


def forward_remote(
    environment: str, arguments: list[str], *, settings: Settings | None = None
) -> int | None:
    if any(token in {"--help", "-h"} for token in arguments):
        return None
    native = "win" if os.name == "nt" else "wsl"
    if environment == native:
        return None
    settings = settings or load_settings()
    if environment == "wsl":
        if shutil.which("wsl.exe") is None:
            raise ValueError(message("WSL is unavailable"))
        return subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(REPOSITORY / "scripts/dev-tools-wsl.ps1"),
                "-Distro",
                settings.distro,
                "-Language",
                language(),
                *arguments,
            ],
            check=False,
        ).returncode
    if shutil.which("pwsh.exe") is None:
        raise ValueError(message("Windows control requires PowerShell 7 and WSL interop"))
    forwarded = list(arguments)
    if (
        forwarded
        and forwarded[0] in {"scan", "init", "register"}
        and not any(token in {"--help", "-h"} for token in forwarded)
    ):
        index = source_argument(forwarded)
        if index is not None:
            forwarded[index] = _windows_path(forwarded[index])
        else:
            forwarded.insert(1, _windows_path(Path.cwd()))
    environment_variables = os.environ.copy()
    environment_variables["DEV_TOOLS_CONFIG"] = ""
    environment_variables["DEV_TOOLS_TRANSPORT_LANGUAGE"] = language()
    # WSL interop only transfers explicitly listed variables to Windows.
    transported = {"DEV_TOOLS_CONFIG", "DEV_TOOLS_TRANSPORT_LANGUAGE"}
    wslenv = [
        entry
        for entry in environment_variables.get("WSLENV", "").split(":")
        if entry and entry.split("/")[0] not in transported
    ]
    environment_variables["WSLENV"] = ":".join(
        [*wslenv, *(name + "/w" for name in sorted(transported))]
    )
    return subprocess.run(
        [
            "pwsh.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WorkingDirectory",
            _windows_path(Path.cwd()),
            "-File",
            _windows_path(REPOSITORY / "scripts/dev-tools.ps1"),
            "-e",
            "win",
            *forwarded,
        ],
        env=environment_variables,
        # The interop server may outlive this process. Its Linux cwd must not
        # hold a Windows project directory open; -WorkingDirectory preserves
        # the caller's directory in the actual Windows command.
        cwd=REPOSITORY,
        check=False,
    ).returncode
