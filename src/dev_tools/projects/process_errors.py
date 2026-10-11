"""Safe subprocess diagnostics at the project/native adapter boundary."""

from __future__ import annotations

import subprocess

from ..i18n import message
from .models import ProjectError


def process_error(error: subprocess.SubprocessError, command=None) -> ProjectError:
    """Preserve a useful operation name without exposing argv, output or credentials."""
    command = command if command is not None else getattr(error, "cmd", ())
    if isinstance(command, (list, tuple)):
        executable = str(command[0]) if command else "process"
    else:
        executable = str(command).split(maxsplit=1)[0] if command else "process"
    executable = executable.replace("\\", "/").rsplit("/", 1)[-1]
    if isinstance(error, subprocess.TimeoutExpired):
        return ProjectError(
            message(
                "command timed out after {seconds}s: {command}",
                seconds=error.timeout,
                command=executable,
            )
        )
    if isinstance(error, subprocess.CalledProcessError):
        return ProjectError(
            message(
                "command failed ({returncode}): {value}",
                returncode=error.returncode,
                value=executable,
            )
        )
    return ProjectError(
        message(
            "could not run command {command}: {detail}",
            command=executable,
            detail=type(error).__name__,
        )
    )


def run_process(argv, *, check=False, **options):
    try:
        return subprocess.run(argv, check=check, **options)
    except subprocess.SubprocessError as exc:
        raise process_error(exc, argv) from None
