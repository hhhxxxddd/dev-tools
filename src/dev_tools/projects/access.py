"""Read-only Windows-to-WSL checks, separate from native service lifecycle health."""

from __future__ import annotations

import json
import os
import re
import socket
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlsplit

from .models import within

WEB_COMMAND = re.compile(
    r"(?:^|[\s/\\])(?:vite|uvicorn|gunicorn|hypercorn|http-server)(?:\s|$)"
    r"|spring-boot:run|(?:next|nuxt|astro)\s+dev|react-scripts\s+start"
    r"|vue-cli-service\s+serve|manage\.py\s+runserver|flask\s+run"
)
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
MAX_TARGETS = 128
MAX_METADATA_BYTES = 1024 * 1024


def _web_service(service, root: Path, source_root: Path | None = None) -> bool:
    command = " ".join(service.command.argv) or service.command.script
    if service.driver == "spring" or WEB_COMMAND.search(command):
        return True
    argv = service.command.argv
    if not argv or Path(argv[0].strip("{}")).stem not in {"npm", "pnpm", "yarn", "bun"}:
        return False
    arguments = list(argv[1:])
    if arguments[:1] in (["run"], ["run-script"]):
        arguments = arguments[1:]
    if not arguments or arguments[0].startswith("-"):
        return False
    for directory in dict.fromkeys(path for path in (root, source_root) if path is not None):
        try:
            path = within(directory, service.workdir) / "package.json"
            if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                continue
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    continue
                content = stream.read(MAX_METADATA_BYTES + 1)
            if len(content) > MAX_METADATA_BYTES:
                return False
            scripts = json.loads(content).get("scripts", {})
            script = scripts.get(arguments[0]) if isinstance(scripts, dict) else None
            return isinstance(script, str) and bool(WEB_COMMAND.search(script))
        except OSError:
            # An unprivileged query may inspect a project owned by root. Read
            # source metadata without elevating or exposing the native cache.
            continue
        except ValueError, AttributeError:
            return False
    return False


def access_targets(spec, root: Path, *, source_root: Path | None = None) -> list[dict]:
    targets = []
    for name, service in spec.services.items():
        http = service.health.get("http")
        if http:
            url = urlsplit(http)
            if url.hostname in LOOPBACK and url.scheme in {"http", "https"}:
                # Reachability needs no credentials, query tokens or fragments.
                targets.append(
                    {
                        "service": name,
                        "protocol": url.scheme,
                        "port": url.port or (443 if url.scheme == "https" else 80),
                        "path": quote(url.path or "/", safe="/%:@-._~!$&'()*+,;="),
                    }
                )
        elif port := service.health.get("tcp"):
            targets.append(
                {
                    "service": name,
                    "protocol": "http" if _web_service(service, root, source_root) else "tcp",
                    "port": port,
                    "path": "/",
                }
            )
    return targets


def _validated_targets(project: dict) -> list[dict] | None:
    targets = project.get("access_targets")
    services = project.get("services")
    if (
        not isinstance(targets, list)
        or len(targets) > MAX_TARGETS
        or not isinstance(services, dict)
    ):
        return None
    valid = []
    for target in targets:
        if (
            not isinstance(target, dict)
            or not isinstance(target.get("service"), str)
            or target["service"] not in services
            or not isinstance(target.get("protocol"), str)
            or target["protocol"] not in {"tcp", "http", "https"}
            or type(target.get("port")) is not int
            or not 1 <= target["port"] <= 65535
            or not isinstance(target.get("path"), str)
            or not target["path"].startswith("/")
            or target["path"].startswith("//")
            or any(char.isspace() or ord(char) < 32 or ord(char) >= 127 for char in target["path"])
            or "?" in target["path"]
            or "#" in target["path"]
        ):
            return None
        service = services[target["service"]]
        if not isinstance(service, dict):
            return None
        if service.get("phase") == "running":
            valid.append(target)
    return valid


def _probe_address(target: tuple[str, int, str], host: str) -> dict:
    from ..toolbox.network import check_target, parse_target

    protocol, port, path = target
    address = f"{host}:{port}" if protocol == "tcp" else f"{protocol}://{host}:{port}{path}"
    logical = f"localhost:{port}" if protocol == "tcp" else f"{protocol}://localhost:{port}{path}"
    addresses = (
        [(socket.AF_INET, ("127.0.0.1", port))]
        if host == "127.0.0.1"
        else [(socket.AF_INET6, ("::1", port, 0, 0))]
    )
    # Preserve the localhost Host header and TLS SNI while selecting each
    # loopback explicitly. Literal loopbacks need no resolver child process.
    result = check_target(parse_target(logical), timeout=2, addresses=addresses)
    stages = result["stages"]
    failed = next((stage for stage in stages if stage["status"] == "failed"), None)
    http_status = next((stage["http_status"] for stage in stages if "http_status" in stage), None)
    # Authentication and missing root pages still prove HTTP forwarding works.
    reachable = result["healthy"] or (
        failed is not None
        and failed.get("stage") == "http"
        and failed.get("code") == "http_status"
        and http_status is not None
        and 400 <= http_status < 500
    )
    value = {"target": address, "reachable": bool(reachable)}
    peer = next((stage["peer"] for stage in stages if "peer" in stage), None)
    if peer:
        value["peer"] = peer
    if http_status is not None:
        value["http_status"] = http_status
    if not reachable:
        value.update(
            stage=failed["stage"] if failed else "unknown",
            code=failed.get("code", "probe_failed") if failed else "probe_failed",
        )
    return value


def probe(target: tuple[str, int, str]) -> dict:
    # Windows can delay refusing ::1 until the entire connect timeout expires.
    # Probe the two loopbacks independently so it cannot hide a working IPv4
    # listener, and an accepting-but-silent IPv4 relay cannot hide working IPv6.
    with ThreadPoolExecutor(max_workers=2) as workers:
        attempts = list(
            workers.map(lambda host: _probe_address(target, host), ("127.0.0.1", "[::1]"))
        )
    selected = next((attempt for attempt in attempts if attempt["reachable"]), None)
    if selected is None:
        selected = next(
            (attempt for attempt in attempts if attempt.get("stage") == "http"), attempts[0]
        )
    protocol, port, path = target
    address = f"localhost:{port}" if protocol == "tcp" else f"{protocol}://localhost:{port}{path}"
    return {**selected, "target": address, "attempts": attempts}


def windows_access(projects: list[dict]) -> list[dict]:
    """Apply caller-side diagnostics without changing native state or processes."""
    declarations = [_validated_targets(project) for project in projects]
    unique = tuple(
        dict.fromkeys(
            (target["protocol"], target["port"], target["path"])
            for targets in declarations
            for target in targets or []
        )
    )
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = dict(zip(unique, workers.map(probe, unique), strict=True))
    values = []
    for project, targets in zip(projects, declarations, strict=True):
        checks = [
            {
                "service": target["service"],
                **results[target["protocol"], target["port"], target["path"]],
            }
            for target in targets or []
        ]
        services = project.get("services")
        required_checks = [
            check
            for check in checks
            if isinstance(services, dict)
            and services.get(check["service"], {}).get("required", True)
        ]
        running = isinstance(services, dict) and any(
            isinstance(service, dict)
            and service.get("phase") == "running"
            and service.get("required", True)
            for service in services.values()
        )
        state = (
            "unknown"
            if targets is None and running
            else "unreachable"
            if any(not check["reachable"] for check in required_checks)
            else "reachable"
            if required_checks
            else "not-applicable"
        )
        value = {
            **project,
            "runtime_ready": project.get("ready", False),
            "runtime_healthy": project.get("healthy"),
            "health_scope": "windows-to-wsl",
            "windows_access": {"state": state, "checks": checks},
        }
        if state in {"unreachable", "unknown"}:
            value.update(ready=False, healthy=False if state == "unreachable" else None)
        values.append(value)
    return values
