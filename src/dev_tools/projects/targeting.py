"""Resolve named lifecycle actions from registration facts alone."""

from __future__ import annotations

import json
import subprocess

from ..i18n import message
from .models import ProjectError
from .registry import Registry

REGISTRY_LOOKUP = "_registry-lookup"


def registration_payload(environment: str, name: str) -> dict:
    registry = Registry("windows" if environment == "win" else environment)
    try:
        # Count occupied slots conservatively, even if the binding itself is invalid.
        registry.path(name).lstat()
    except FileNotFoundError:
        registered = False
    else:
        registered = True
    return {"schema_version": 1, "name": name, "environment": environment, "registered": registered}


def _registered(environment, name, *, native, settings):
    from ..runtimes.router import forward_remote

    try:
        if environment == native:
            return registration_payload(environment, name)["registered"]
        result = forward_remote(
            environment,
            [REGISTRY_LOOKUP, name],
            settings=settings,
            capture=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else None
        if (
            isinstance(payload, dict)
            and type(payload.get("schema_version")) is int
            and payload["schema_version"] == 1
            and payload.get("environment") == environment
            and payload.get("name") == name
            and type(payload.get("registered")) is bool
        ):
            return payload["registered"]
    except OSError, ValueError, subprocess.SubprocessError:
        pass
    raise ProjectError(
        message(
            "Could not check whether {name} is registered in {environment}; specify -e win or -e wsl",
            name=name,
            environment=environment,
        )
    ) from None


def resolve_project_environment(name: str, native: str, settings) -> str:
    candidates = [
        environment
        for environment in ("win", "wsl")
        if _registered(environment, name, native=native, settings=settings)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise ProjectError(
            message(
                "Project {name} is registered in both environments; specify -e win or -e wsl",
                name=name,
            )
        )
    raise ProjectError(message("Project {name} is not registered in either environment", name=name))
