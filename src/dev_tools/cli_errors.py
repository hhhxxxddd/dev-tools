"""Safe public error documents and per-invocation output intent."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tomllib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .i18n import message, render, t
from .projects.models import ProjectError
from .schemas import CLI_SCHEMA_VERSION
from .settings import SettingsError

JSON_COMMANDS = frozenset(
    {
        "init",
        "scan",
        "register",
        "list",
        "check",
        "prepare",
        "start",
        "stop",
        "restart",
        "status",
        "build",
        "unregister",
        "show",
        "rename",
        "config",
        "sysinfo",
        "report",
        "self",
        "ssh",
        "hosts",
        "ports",
        "which",
        "net",
    }
)
ERROR_MESSAGES = {
    "invalid_arguments": "Invalid command arguments; see dev-tools help",
    "invalid_config": "Configuration is invalid; check the configuration and retry",
    "project_not_registered": "Project is not registered; run dev-tools register",
    "timeout": "Operation timed out; check the environment and retry",
    "preparation_failed": "Project preparation failed; inspect project logs and retry",
    "operation_failed": "Operation failed; check the environment or project logs and retry",
    "interrupted": "Operation interrupted",
    "target_unavailable": "Target controller is unavailable; check its deployment and connection",
    "target_version_mismatch": "Target controller version is incompatible; update the target controller",
    "target_command_unsupported": "Target controller does not support this command; update the target controller",
    "target_response_invalid": "Target controller returned an invalid response; check both controller versions",
}


class TargetError(ProjectError):
    """A transport failure with explicitly selected public facts, never raw output."""

    def __init__(
        self, code, explanation, *, environment, target_version=None, returncode=None, reason=None
    ):
        from . import __version__

        super().__init__(explanation)
        self.code = code
        self.details = {"environment": environment, "controller_version": __version__}
        if target_version is not None:
            self.details["target_version"] = target_version
        if type(returncode) is int:
            self.details["returncode"] = returncode
        if reason is not None:
            self.details["reason"] = reason


class RemoteError(ProjectError):
    """Preserve a validated target error document and its exit code."""

    def __init__(self, payload, returncode):
        super().__init__(payload["error"]["message"])
        self.payload = payload
        self.returncode = returncode


@dataclass(frozen=True)
class ErrorRequest:
    as_json: bool = False
    action: str | None = None


_request = ContextVar("dev_tools_error_request", default=None)


def _initial_request(arguments: list[str]) -> ErrorRequest:
    # Only inspect our option scope. Log streams and downstream Compose options
    # must never become controller JSON requests, even before parsing succeeds.
    tokens = arguments[: arguments.index("--")] if "--" in arguments else arguments
    command = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"--config", "--env", "-e"}:
            index += 2
            continue
        if not token.startswith("-"):
            command = token
            break
        index += 1
    return ErrorRequest(
        "--json" in tokens and command not in {"logs", "compose", "help", "upgrade", "_worker"},
        command if command in JSON_COMMANDS else None,
    )


@contextmanager
def request_scope(arguments: list[str]):
    token = _request.set(_initial_request(arguments))
    try:
        yield
    finally:
        _request.reset(token)


def set_request(args) -> None:
    _request.set(ErrorRequest(bool(getattr(args, "json", False)), args.command))


def json_requested() -> bool:
    request = _request.get()
    return bool(request and request.as_json)


def _number(value) -> int | float | None:
    try:
        number = float(value)
    except ValueError, TypeError, OverflowError:
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def error_payload(error: BaseException, *, code: str | None = None, action: str | None = None):
    """Classify errors without serializing arbitrary exception text, argv or output."""
    request = _request.get()
    action = action or (request.action if request else None)
    if isinstance(error, RemoteError):
        return error.payload
    if isinstance(error, TargetError):
        return {
            "schema_version": CLI_SCHEMA_VERSION,
            "action": action,
            "error": {
                "code": error.code,
                "message": t(ERROR_MESSAGES[error.code]),
                "details": error.details,
            },
        }
    value = error.args[0] if error.args else None
    source = getattr(value, "source", "")
    parameters = getattr(value, "parameters", {})
    details = {}
    if code is None:
        if isinstance(error, (SettingsError, tomllib.TOMLDecodeError)):
            code = "invalid_config"
        elif isinstance(error, KeyboardInterrupt):
            code = "interrupted"
        elif isinstance(error, subprocess.TimeoutExpired) or source in {
            "command timed out after {seconds}s: {command}",
            "service {name} did not become ready before timeout",
            "project list query timed out in {environment}",
        }:
            code = "timeout"
        elif source in {
            "project is not registered: {name}",
            "当前目录未匹配到已注册项目；请显式指定项目名",
        }:
            code = "project_not_registered"
        elif source == "Invalid JSON response from target environment":
            code = "operation_failed"
        elif action in {"prepare", "start"} and isinstance(
            error, (ProjectError, subprocess.SubprocessError)
        ):
            code = "preparation_failed"
        else:
            code = "operation_failed"
    if code == "timeout":
        seconds = _number(getattr(error, "timeout", parameters.get("seconds")))
        if seconds is not None:
            details["timeout_seconds"] = seconds
    if isinstance(error, subprocess.CalledProcessError) or "returncode" in parameters:
        returncode = _number(getattr(error, "returncode", parameters.get("returncode")))
        if isinstance(returncode, int):
            details["returncode"] = returncode
    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "action": action,
        "error": {"code": code, "message": t(ERROR_MESSAGES[code]), "details": details},
    }


def emit_error(error: BaseException, *, code: str | None = None) -> None:
    if json_requested():
        print(json.dumps(error_payload(error, code=code), ensure_ascii=False, indent=2))
        return
    if isinstance(error, subprocess.TimeoutExpired):
        error = ValueError(message("Controller command timed out; check the environment and retry"))
    elif isinstance(error, subprocess.SubprocessError):
        error = ValueError(message("Controller command failed; check the environment and retry"))
    if not isinstance(error, KeyboardInterrupt):
        from .presentation import error as error_text

        print(error_text(f"dev-tools: {render(error)}", stream=sys.stderr), file=sys.stderr)


def exit_code(value: int | None) -> int:
    """Keep public exit categories independent of installer/process return codes."""
    return value if value in {0, 1, 2, 130} else 1
