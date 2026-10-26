"""Bounded legacy-controller log fallback through the existing read-only logs command."""

from __future__ import annotations

import json
import subprocess
import time

from .config import parse_project
from .diagnostic_evidence import failure_messages
from .diagnostics import diagnostic
from .models import validate_name

MAX_REQUESTS = 3
TIME_BUDGET = 4


def _failed_process(project, name, service):
    if service.get("phase") in {"failed", "restarting"}:
        return True
    recovery = project.get("recovery") or {}
    return (
        service.get("phase") == "running"
        and service.get("health") == "unhealthy"
        and name in recovery.get("restore_workers", [])
    )


def remote_log_evidence(projects, target, settings):
    from ..runtimes.router import forward_remote

    deadline = time.monotonic() + TIME_BUDGET
    attempted = 0
    result = [{**original, "services": dict(original.get("services", {}))} for original in projects]
    prioritized = sorted(
        result,
        key=lambda project: (
            not any(
                _failed_process(project, name, service)
                for name, service in project["services"].items()
            )
        ),
    )
    for project in prioritized:
        if project.get("desired_running") is False:
            continue
        missing = [
            name
            for name, service in project["services"].items()
            if service.get("phase") == "running"
            and service.get("health") == "unhealthy"
            and "diagnostics" not in service
        ]
        remaining = deadline - time.monotonic()
        if missing and attempted < MAX_REQUESTS and remaining > 0:
            try:
                validate_name(project["name"])
                attempted += 1
                reply = forward_remote(
                    target,
                    ["show", project["name"], "--json"],
                    settings=settings,
                    capture=True,
                    timeout=min(2, remaining),
                    stdin=subprocess.DEVNULL,
                )
                if reply.returncode == 0:
                    spec = parse_project(
                        json.loads(reply.stdout)["project"], "windows" if target == "win" else "wsl"
                    )
                    for name in missing:
                        if name not in spec.services:
                            continue
                        health = spec.services[name].health
                        address = health.get("http") or (
                            f"tcp://127.0.0.1:{health['tcp']}" if health.get("tcp") else None
                        )
                        project["services"][name] = {
                            **project["services"][name],
                            "diagnostics": [
                                diagnostic(
                                    "health.details_missing",
                                    service=name,
                                    stage="health",
                                    evidence={"target": address},
                                )
                            ],
                        }
            except OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError:
                pass
        for name, original_service in project["services"].items():
            if (
                not _failed_process(project, name, original_service)
                or "log_diagnostics" in original_service
            ):
                continue
            remaining = deadline - time.monotonic()
            if attempted >= MAX_REQUESTS or remaining <= 0:
                break
            try:
                validate_name(project["name"])
                validate_name(name)
            except ValueError:
                continue
            attempted += 1
            service = dict(original_service)
            project["services"][name] = service
            service["log_diagnostics"] = []
            try:
                reply = forward_remote(
                    target,
                    ["logs", project["name"], name, "--lines", "60"],
                    settings=settings,
                    capture=True,
                    timeout=min(2, remaining),
                    stdin=subprocess.DEVNULL,
                )
                if reply.returncode == 0 and isinstance(reply.stdout, str):
                    service["log_diagnostics"] = [
                        diagnostic(
                            "log.tail", service=name, severity="info", evidence={"message": value}
                        )
                        for value in failure_messages(reply.stdout)
                    ]
            except OSError, ValueError, subprocess.SubprocessError:
                # Supplementary evidence must not fail a successful status/list query.
                pass
    return result
