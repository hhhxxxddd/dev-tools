"""Application readiness uses direct HTTP and an optional JSON subset contract."""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request

from .diagnostic_evidence import safe_target
from .diagnostics import diagnostic

MAX_READINESS_BYTES = 65536
READINESS_TIMEOUT = 1


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def _matches(expected, actual) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _matches(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(_matches(left, right) for left, right in zip(expected, actual, strict=True))
        )
    return type(expected) is type(actual) and expected == actual


def _mismatch_paths(expected, actual, path="", depth=0):
    if _matches(expected, actual):
        return []
    if depth >= 8:
        return [path or "/"]
    if isinstance(expected, dict) and isinstance(actual, dict):
        values = []
        for key, value in expected.items():
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in actual:
                values.append(child)
            else:
                values.extend(_mismatch_paths(value, actual[key], child, depth + 1))
            if len(values) >= 4:
                break
        return values[:4]
    return [path or "/"]


def _read_json(response, deadline):
    content = bytearray()
    while len(content) <= MAX_READINESS_BYTES and time.monotonic() < deadline:
        block = response.read1(min(8192, MAX_READINESS_BYTES + 1 - len(content)))
        if not block:
            return json.loads(content), None
        content.extend(block)
    return None, "health.response_limit"


def _failed_checks(payload, paths):
    values = []
    for path in paths:
        value = payload
        for key in path[1:].split("/"):
            key = key.replace("~1", "/").replace("~0", "~")
            value = value.get(key) if isinstance(value, dict) else None
        if (
            value is False
            or isinstance(value, str)
            and value in {"failed", "error", "unhealthy", "unavailable", "down"}
        ):
            values.append(
                diagnostic("health.check_failed", stage="health", evidence={"check": path})
            )
    return values


def connection_failure(error):
    error = getattr(error, "reason", error)
    code = (
        "health.timeout"
        if isinstance(error, (TimeoutError, socket.timeout))
        else "health.connection_refused"
        if isinstance(error, ConnectionRefusedError)
        else "health.certificate_invalid"
        if isinstance(error, ssl.SSLCertVerificationError)
        else "health.probe_failed"
    )
    return diagnostic(
        code,
        stage="health",
        evidence={"errno": getattr(error, "errno", None), "exception": type(error).__name__},
    )


def http_probe(checks: dict) -> tuple[bool, list[dict]]:
    healthy, values = _http_probe(checks)
    target = safe_target(checks["http"])
    if target:
        for value in values:
            value["evidence"]["target"] = target
    return healthy, values


def _http_probe(checks: dict) -> tuple[bool, list[dict]]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    deadline = time.monotonic() + READINESS_TIMEOUT
    try:
        try:
            response = opener.open(checks["http"], timeout=READINESS_TIMEOUT)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            values = []
            healthy = 200 <= response.status < 300
            if not 200 <= response.status < 300:
                values.append(
                    diagnostic(
                        "health.http_status",
                        stage="health",
                        evidence={"http_status": response.status},
                    )
                )
            if ("json" in checks and not values) or checks.get("details"):
                try:
                    payload, failure = _read_json(response, deadline)
                    if failure:
                        values.append(diagnostic(failure, stage="health"))
                        if "json" in checks:
                            healthy = False
                    else:
                        values.extend(_failed_checks(payload, checks.get("details", [])))
                        if "json" in checks and not _matches(checks["json"], payload):
                            values.extend(
                                diagnostic(
                                    "health.json_mismatch", stage="health", evidence={"check": path}
                                )
                                for path in _mismatch_paths(checks["json"], payload)
                            )
                            healthy = False
                except ValueError, RecursionError:
                    values.append(diagnostic("health.invalid_json", stage="health"))
                    if "json" in checks:
                        healthy = False
            # Detail extraction is advisory; it does not change readiness semantics.
            if healthy:
                for value in values:
                    value["severity"] = "warning"
            return healthy, values
    except OSError as exc:
        return False, [connection_failure(exc)]
    except ValueError, RecursionError:
        return False, [diagnostic("health.probe_failed", stage="health")]


def http_ready(checks: dict) -> bool:
    return http_probe(checks)[0]
