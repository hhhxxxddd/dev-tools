"""Read-only target probes and validation at the transport boundary."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from .. import __version__
from ..cli_errors import TargetError
from ..i18n import message, t
from ..schemas import CLI_SCHEMA_VERSION


def target_status(environment, settings):
    if environment == "wsl":
        from ..controller import deployment_status

        return deployment_status(settings.distro)
    from .router import forward_remote

    try:
        result = forward_remote("win", ["--version"], settings=settings, capture=True, timeout=5)
    except OSError, ValueError, subprocess.SubprocessError:
        return {"state": "unavailable", "version": None}
    version = re.fullmatch(r"dev-tools ([0-9]+\.[0-9]+\.[0-9]+)\s*", result.stdout)
    if result.returncode == 0 and version:
        return {"state": "installed", "version": version.group(1)}
    return {"state": "unavailable", "version": None}


def update_instruction(environment):
    if environment == "win":
        return "dev-tools upgrade -e win"
    return ("" if os.name == "nt" else "sudo ") + "dev-tools self update -e wsl"


def version_notice(environment, version):
    return t(
        "Controller versions differ: local {local}; target {environment} {target}. Update with: {command}",
        local=__version__,
        environment=environment,
        target=version,
        command=update_instruction(environment),
    )


def preflight(environment, arguments, settings):
    if environment == ("win" if os.name == "nt" else "wsl"):
        return None
    status = target_status(environment, settings)
    version = status.get("version")
    if status["state"] != "installed":
        raise TargetError(
            "target_unavailable",
            message(
                "Could not reach {environment}. Check its dev-tools entrypoint, Windows/WSL interop and PATH; this does not imply that an update is required.",
                environment=environment,
            ),
            environment=environment,
        )
    if version and version != __version__:
        from ..presentation import warning

        print(warning(version_notice(environment, version), stream=sys.stderr), file=sys.stderr)
    if version and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        target_version = tuple(map(int, version.split(".")))
        legacy = target_version < (0, 5, 0)
        command = arguments[0] if arguments else "help"
        unsupported = (
            command == "check"
            and legacy
            or command == "start"
            and "--all" in arguments
            and target_version < (0, 5, 2)
        )
        incompatible = legacy and command in {
            "init",
            "register",
            "prepare",
            "start",
            "restart",
            "build",
            "rename",
            "unregister",
        }
        if unsupported or incompatible:
            raise TargetError(
                "target_command_unsupported" if unsupported else "target_version_mismatch",
                message(
                    "Target {environment} dev-tools {target} cannot execute {command} with the current CLI contract (local {local}). Update with: {update}",
                    environment=environment,
                    target=version,
                    command=command
                    + (" --all" if command == "start" and "--all" in arguments else ""),
                    local=__version__,
                    update=update_instruction(environment),
                ),
                environment=environment,
                target_version=version,
            )
    return version


def response_payload(result, environment, action):
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    try:
        value = json.loads(result.stdout)
        if (
            not isinstance(value, dict)
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != CLI_SCHEMA_VERSION
            or not isinstance(value.get("action"), str)
            or action != "init"
            and value["action"] != action
            or result.returncode not in {0, 1, 2, 130}
        ):
            raise ValueError
        if "error" in value:
            error = value["error"]
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("code"), str)
                or not isinstance(error.get("message"), str)
                or not isinstance(error.get("details"), dict)
            ):
                raise ValueError
        return value
    except TypeError, ValueError:
        raise TargetError(
            "target_response_invalid",
            message(
                "Target {environment} returned an invalid CLI response (exit {returncode}); check both controller versions and target diagnostics",
                environment=environment,
                returncode=result.returncode,
            ),
            environment=environment,
            returncode=result.returncode,
        ) from None
