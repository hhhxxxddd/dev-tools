"""Transport only: native commands always enter the common project CLI."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..i18n import language, message
from ..presentation import color_enabled
from ..settings import Settings, load_settings

REPOSITORY = Path(__file__).resolve().parents[3]


def _run_transport(command, environment, **options):
    try:
        return subprocess.run(command, check=False, **options)
    except subprocess.TimeoutExpired:
        # Preserve timeout classification and duration at the presentation boundary.
        raise
    except OSError, subprocess.SubprocessError:
        from ..cli_errors import TargetError

        raise TargetError(
            "target_unavailable",
            message(
                "WSL could not launch Windows PowerShell; check Windows interop and open a new WSL session"
            )
            if environment == "win"
            else message(
                "Target {environment} could not be reached; check deployment, connection and target diagnostics",
                environment=environment,
            ),
            environment=environment,
            reason="interop_unavailable" if environment == "win" else None,
        ) from None


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
        stdin=subprocess.DEVNULL,
    )
    if result.returncode or not result.stdout.strip():
        raise ValueError(result.stderr.strip() or f"cannot translate Windows path: {value}")
    return result.stdout.strip()


def _windows_entry(arguments: list[str]) -> list[str]:
    source = REPOSITORY / ".windows-source"
    if source.is_file():
        root = source.read_text(encoding="utf-8").strip()
        if not re.match(r"^[A-Za-z]:[\\/]", root):
            raise ValueError(message("invalid Windows controller source: {path}", path=source))
        entry = root.rstrip("/\\") + "/scripts/dev-tools.ps1"
    else:
        entry = _windows_path(REPOSITORY / "scripts/dev-tools.ps1")
    scoop_source = re.search(
        r"[\\/]apps[\\/]dev-tools[\\/](?:current|[0-9]+\.[0-9]+\.[0-9]+)[\\/]scripts[\\/]dev-tools\.ps1$",
        entry,
        re.IGNORECASE,
    )
    if not entry.startswith("\\\\") and not scoop_source:
        return ["-File", entry, "-e", "win", *arguments]

    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    missing = str(message("Windows dev-tools entrypoint is missing; install dev-tools on Windows"))
    invocation = " ".join(quote(value) for value in ["-e", "win", *arguments])
    # Linux-native deployments use the Windows installation rather than executing
    # an unsigned PowerShell script through a WSL network share.
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
        "$entry = Get-Command dev-tools -CommandType Application -ErrorAction SilentlyContinue; "
        f"if (-not $entry) {{ throw {quote(missing)} }}; "
        f"& $entry.Source {invocation}; exit $LASTEXITCODE"
    )
    return ["-Command", script]


def _transport_environment(capture: bool) -> dict:
    environment = os.environ.copy()
    environment["DEV_TOOLS_TERMINAL"] = (
        "1"
        if not capture
        and (
            color_enabled()
            or os.environ.get("DEV_TOOLS_TERMINAL") == "1"
            or sys.stdin.isatty()
            and sys.stdout.isatty()
        )
        else "0"
    )
    # Terminal capability and color preferences are independent. Normalize empty
    # NO_COLOR too, since Windows interop cannot reliably carry empty variables.
    if "NO_COLOR" in environment or environment.get("TERM") == "dumb":
        environment["NO_COLOR"] = "1"
    return environment


def forward_remote(
    environment: str,
    arguments: list[str],
    *,
    settings: Settings | None = None,
    capture: bool = False,
    timeout: float | None = 30,
    stdin=None,
) -> int | subprocess.CompletedProcess[str] | None:
    native = "win" if os.name == "nt" else "wsl"
    if environment == native:
        return None
    settings = settings or load_settings()
    options = (
        {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout,
        }
        if capture
        else {"timeout": timeout}
    )
    if stdin is not None:
        options["stdin"] = stdin
    if environment == "wsl":
        if shutil.which("wsl.exe") is None:
            from ..cli_errors import TargetError

            raise TargetError(
                "target_unavailable", message("WSL is unavailable"), environment="wsl"
            )
        transport_environment = _transport_environment(capture)
        result = _run_transport(
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
                "-ArgumentsPayload",
                base64.b64encode(json.dumps(arguments, ensure_ascii=False).encode("utf-8")).decode(
                    "ascii"
                ),
            ],
            environment,
            env=transport_environment,
            **options,
        )
        return result if capture else result.returncode
    from .platforms.windows_interop import powershell_entry

    powershell = powershell_entry()
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
    # Source directories are caller paths; output files may not exist yet. Keep
    # already-native Windows paths intact and respect the downstream -- boundary.
    if forwarded and forwarded[0] in {"self", "upgrade", "report"}:
        path_options = {"--output"} if forwarded[0] == "report" else {"--source"}
        index = 0
        while index < len(forwarded):
            token = forwarded[index]
            if token == "--":
                break
            if token in path_options and index + 1 < len(forwarded):
                forwarded[index + 1] = _windows_path(forwarded[index + 1])
                index += 1
            elif any(token.startswith(option + "=") for option in path_options):
                option, value = token.split("=", 1)
                # PowerShell -File splits --option=C:\path at the colon. Use
                # separate argv tokens so both public spellings stay equivalent.
                forwarded[index : index + 1] = [option, _windows_path(value)]
                index += 1
            index += 1
    environment_variables = _transport_environment(capture)
    environment_variables["DEV_TOOLS_CONFIG"] = ""
    environment_variables["DEV_TOOLS_TRANSPORT_LANGUAGE"] = language()
    # WSL interop only transfers explicitly listed variables to Windows.
    transported = {"DEV_TOOLS_CONFIG", "DEV_TOOLS_TRANSPORT_LANGUAGE", "DEV_TOOLS_TERMINAL"}
    if "NO_COLOR" in environment_variables:
        transported.add("NO_COLOR")
    wslenv = [
        entry
        for entry in environment_variables.get("WSLENV", "").split(":")
        if entry and entry.split("/")[0] not in transported
    ]
    environment_variables["WSLENV"] = ":".join(
        [*wslenv, *(name + "/w" for name in sorted(transported))]
    )
    result = _run_transport(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-WorkingDirectory",
            _windows_path(Path.cwd()),
            *_windows_entry(forwarded),
        ],
        environment,
        env=environment_variables,
        # The interop server may outlive this process. Its Linux cwd must not
        # hold a Windows project directory open; -WorkingDirectory preserves
        # the caller's directory in the actual Windows command.
        cwd=REPOSITORY,
        **options,
    )
    return result if capture else result.returncode
