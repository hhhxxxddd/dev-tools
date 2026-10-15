"""Socket inventory and conservative project attribution from live process identity."""

from __future__ import annotations

import math
import os

from ..projects.registry import Registry, read_json
from .platforms import native


def project_processes(processes: dict, environment: str) -> tuple[dict, list]:
    roots, warnings = {}, []
    registry = Registry(environment)
    try:
        names = registry.names()
    except OSError, ValueError:
        return roots, ["project_details_unavailable"]
    for name in names:
        try:
            binding = registry.load(name)
            for path in sorted((binding.state / "workers").glob("*.json")):
                record = read_json(path)
                if environment == "wsl":
                    # WSL wall clocks can step independently of /proc start
                    # ticks. Use the live systemd cgroup identity, not a widened
                    # timestamp tolerance, to match the entire service subtree.
                    if record.get("phase") == "running":
                        prefix = (
                            "dev-tools-test-worker"
                            if os.environ.get("DEV_TOOLS_TEST_TRANSIENT") == "1"
                            else "dev-tools-worker"
                        )
                        roots[f"{prefix}@{name}:{path.stem}.service"] = [
                            {"project": name, "service": path.stem}
                        ]
                    continue
                pid = record.get("pid")
                process = processes.get(str(pid), {})
                started, updated = process.get("started"), record.get("updated_at")
                if (
                    record.get("phase") == "running"
                    and type(pid) is int
                    and all(
                        type(item) in (int, float) and math.isfinite(item)
                        for item in (started, updated)
                    )
                    and 0 <= updated - started <= 10
                ):
                    roots.setdefault(pid, []).append({"project": name, "service": path.stem})
        except OSError, ValueError:
            warnings.append("project_details_unavailable")
    return roots, warnings


def process_projects(pid: int | None, processes: dict, roots: dict) -> list:
    seen = set()
    while pid and pid not in seen:
        seen.add(pid)
        child = processes.get(str(pid), {})
        for unit in child.get("units", ()):
            if unit in roots:
                return roots[unit]
        if pid in roots:
            return roots[pid]
        parent_pid = child.get("parent_pid")
        parent = processes.get(str(parent_pid), {})
        # A reused parent PID must not attribute an unrelated process to a project.
        if (
            not parent.get("started")
            or not child.get("started")
            or parent["started"] > child["started"]
        ):
            break
        pid = parent_pid
    return []


def collect_ports(environment: str, *, port: int | None = None, protocol: str = "all") -> dict:
    snapshot = native().port_snapshot(port=port, protocol=protocol)
    processes = snapshot["processes"]
    roots, project_warnings = (
        project_processes(processes, environment) if snapshot["entries"] else ({}, [])
    )
    warnings = [*project_warnings, *snapshot["warnings"]]
    entries = []
    for row in snapshot["entries"]:
        if (port is not None and row["port"] != port) or (
            protocol != "all" and row["protocol"] != protocol
        ):
            continue
        process = processes.get(str(row["pid"]), {})
        entries.append(
            {
                **row,
                "family": "ipv6" if ":" in row["address"] else "ipv4",
                "process": process.get("name"),
                "executable": process.get("executable"),
                "projects": process_projects(row["pid"], processes, roots),
            }
        )
    if any(row["pid"] is None for row in entries):
        warnings.append("socket_owner_unavailable")
    warnings = sorted(set(warnings))
    entries.sort(key=lambda row: (row["port"], row["protocol"], row["address"], row["pid"] or -1))
    return {"entries": entries, "warnings": warnings, "complete": not warnings}
