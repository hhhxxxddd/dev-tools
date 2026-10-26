"""Bounded, portable diagnostic facts; presentation never interprets application prose."""

from __future__ import annotations

import math
import re
import time

from ..i18n import t
from .diagnostic_evidence import safe_message, safe_target

MESSAGES = {
    "health.http_status": "HTTP readiness returned {http_status}",
    "health.connection_refused": "Health connection was refused",
    "health.timeout": "Health check timed out",
    "health.certificate_invalid": "Health TLS certificate validation failed",
    "health.probe_failed": "Health check could not be completed",
    "health.details_missing": "Remote health check failed; the controller did not report the failure details",
    "health.invalid_json": "Health response is not valid JSON",
    "health.json_mismatch": "Health response does not match the declared JSON",
    "health.response_limit": "Health response exceeded its size or time limit",
    "health.check_failed": "Application health check failed: {check}",
    "compose.unhealthy": "Compose containers are missing, stopped or unhealthy",
    "compose.query_failed": "Compose status query failed: {message}",
    "compose.empty": "Compose did not report any containers",
    "compose.container_state": "Container {container}: state={state}, health={health}",
    "process.exited": "Process exited with code {exit_code}",
    "process.failed": "Worker failed; inspect its log",
    "log.hint": "Recent failure log contains {code} (clue)",
    "log.message": "Failure log clue: {message}",
    "log.tail": "Log tail clue (event time unverified): {message}",
    "service.not_ready": "Service did not become ready before timeout",
    "service.stopped": "Required service is stopped",
    "service.starting": "Service is starting",
    "monitor.stopped": "Required monitor is not running",
    "monitor.reported": "Monitor reported: {message}",
    "preparation.stale": "Project declarations require preparation",
    "task.failed": "Task {task} failed with code {exit_code}",
    "command.failed": "Command failed with code {exit_code}",
    "command.timeout": "Command timed out",
    "operation.failed": "Operation failed; inspect project logs",
    "controller.reported": "Controller reported {code}: {message}",
    "operation.pending": "Recovery is in progress",
    "access.unavailable": "Windows localhost access failed ({stage}/{code})",
    "access.unknown": "Windows localhost access could not be checked",
    "access.observed": "Windows access received HTTP {http_status}",
    "service.port_in_use": "Declared port {port} is already listening",
    "operation.busy": "Another project operation is still in progress",
}
NAME = re.compile(r"[A-Za-z0-9_.:/~@+-]{1,128}\Z")
FIELDS = {
    "http_status",
    "exit_code",
    "port",
    "task",
    "check",
    "stage",
    "code",
    "errno",
    "command",
    "exception",
    "container",
    "state",
    "health",
}


def diagnostic(code, *, service=None, stage=None, severity="error", evidence=None):
    value = {"code": code, "stage": stage or "runtime", "severity": severity, "evidence": {}}
    if isinstance(service, str) and NAME.fullmatch(service):
        value["service"] = service
    for key, item in (evidence or {}).items():
        if key in {"target", "message"}:
            cleaned = safe_target(item) if key == "target" else safe_message(item)
            if cleaned:
                value["evidence"][key] = cleaned
            continue
        if key in FIELDS and (type(item) is int or isinstance(item, str) and NAME.fullmatch(item)):
            value["evidence"][key] = item
    return value


def normalize(values):
    result, seen = [], set()
    for value in values if isinstance(values, list) else []:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("code"), str)
            or value["code"] not in MESSAGES
        ):
            continue
        item = diagnostic(
            value["code"],
            service=value.get("service"),
            stage=value.get("stage")
            if isinstance(value.get("stage"), str)
            and value["stage"]
            in {"runtime", "health", "prepare", "build", "start", "monitor", "access"}
            else "runtime",
            severity=value.get("severity")
            if isinstance(value.get("severity"), str)
            and value["severity"] in {"error", "warning", "info"}
            else "error",
            evidence=value.get("evidence") if isinstance(value.get("evidence"), dict) else {},
        )
        if item["code"] in {"log.message", "log.tail"} and not item["evidence"].get("message"):
            continue
        key = (item["code"], item.get("service"), tuple(sorted(item["evidence"].items())))
        if key not in seen:
            seen.add(key)
            result.append(item)
        if len(result) >= 32:
            break
    return result


def describe(value):
    evidence = {key: "?" for key in FIELDS}
    evidence["message"] = "?"
    evidence.update(value.get("evidence", {}))
    text = t(MESSAGES.get(value["code"], MESSAGES["operation.failed"]), **evidence)
    if (
        value["code"] == "access.unavailable"
        and type(value.get("evidence", {}).get("http_status")) is int
    ):
        text = t("Windows access received HTTP {http_status}", **evidence)
    if value.get("evidence", {}).get("target"):
        text += f" ({value['evidence']['target']})"
    if value.get("evidence", {}).get("errno") is not None:
        text += f" [errno={value['evidence']['errno']}]"
    if value.get("evidence", {}).get("exception"):
        text += f" [{value['evidence']['exception']}]"
    if value.get("evidence", {}).get("command"):
        text += f" [{value['evidence']['command']}]"
    if value["code"] == "health.json_mismatch" and value.get("evidence", {}).get("check"):
        text += f" [{value['evidence']['check']}]"
    return f"{value['service']}: {text}" if value.get("service") else text


def summary(values):
    values = [
        value
        for value in normalize(values)
        if not value["code"].startswith("log.") and value["code"] != "access.observed"
    ]
    return "; ".join(describe(value) for value in values[:2]) or None


def error_diagnostics(error=None, *, detail=None):
    supplied = normalize(getattr(error, "diagnostics", None))
    if supplied:
        return supplied
    value = error.args[0] if error is not None and error.args else None
    source = getattr(value, "source", "")
    parameters = getattr(value, "parameters", {})
    if isinstance(detail, dict):
        source, parameters = detail.get("source", ""), detail.get("parameters", {})
    parameters = parameters if isinstance(parameters, dict) else {}
    name = parameters.get("name")
    if source == "service {name}: TCP port {tcp} is already listening":
        return [
            diagnostic(
                "service.port_in_use",
                service=name,
                stage="start",
                evidence={"port": parameters.get("tcp")},
            )
        ]
    if source == "another project operation is in progress":
        return [diagnostic("operation.busy")]
    if source == "service {name} did not become ready before timeout":
        return [diagnostic("service.not_ready", service=name, stage="health")]
    if source == "task {name} failed ({returncode}); log: {log_path}":
        code = parameters.get("returncode")
        return [
            diagnostic(
                "task.failed",
                stage="build",
                evidence={
                    "task": name,
                    "exit_code": int(code) if str(code).lstrip("-").isdigit() else -1,
                },
            )
        ]
    if source in {"command failed ({returncode}): {value}", "command exited with code {code}"}:
        code = parameters.get("returncode", parameters.get("code"))
        return [
            diagnostic(
                "command.failed",
                evidence={
                    "exit_code": int(code) if str(code).lstrip("-").isdigit() else -1,
                    "command": str(parameters.get("value", ""))
                    .replace("\\", "/")
                    .rsplit("/", 1)[-1],
                },
            )
        ]
    if source == "command timed out after {seconds}s: {command}":
        return [diagnostic("command.timeout")]
    return [diagnostic("operation.failed")]


def project_diagnostics(project):
    values = []
    services = project.get("services", {})
    stopped = project.get("desired_running") is False
    recovery = project.get("recovery") or {}
    for name, service in services.items():
        required = service.get("required", True)
        phase = service.get("phase")
        if stopped and phase == "stopped":
            continue
        facts = normalize(service.get("diagnostics"))
        if phase in {"failed", "restarting"}:
            code = service.get("exit_code")
            facts.insert(
                0,
                diagnostic(
                    "process.exited" if type(code) is int else "process.failed",
                    service=name,
                    evidence={"exit_code": code},
                ),
            )
        elif phase == "running" and service.get("health") == "unhealthy" and not facts:
            facts.append(diagnostic("health.details_missing", service=name, stage="health"))
        elif phase == "stopped" and required and not recovery and project.get("desired_running"):
            facts.append(diagnostic("service.stopped", service=name))
        for fact in facts:
            fact["service"] = name
            if not required:
                fact["severity"] = "warning"
        values.extend(facts)
        if facts:
            values.extend(
                {**fact, "service": name} for fact in normalize(service.get("log_diagnostics"))
            )
        failure = service.get("last_failure") or {}
        failed_at = failure.get("at")
        recent = type(failed_at) in {int, float} and 0 <= time.time() - failed_at <= 5
        if facts and failure and (phase in {"failed", "restarting"} or recent):
            values.extend(
                diagnostic("log.hint", service=name, severity="info", evidence={"code": code})
                for code in failure.get("log_codes", [])[:3]
                if isinstance(code, str)
                and not any(
                    code in fact.get("evidence", {}).get("message", "")
                    for fact in values
                    if fact["code"] in {"log.message", "log.tail"}
                )
            )
    if not stopped:
        previous = normalize(recovery.get("diagnostics")) or normalize(recovery.get("last_failure"))
        if not previous and (recovery.get("error") or project.get("maintenance_error")):
            previous = error_diagnostics(
                detail=recovery.get("error_detail") or project.get("maintenance_error")
            )
        # The specific current probe/exit fact takes precedence over its timeout wrapper.
        current_services = {value.get("service") for value in values}
        values.extend(value for value in previous if value.get("service") not in current_services)
        for name in project.get("expected_workers", []):
            worker = project.get("workers", {}).get(name, {})
            if worker.get("phase") != "running" and not recovery:
                values.append(diagnostic("monitor.stopped", service=name, stage="monitor"))
                if worker.get("error"):
                    values.append(
                        diagnostic(
                            "monitor.reported",
                            service=name,
                            stage="monitor",
                            severity="info",
                            evidence={"message": worker["error"]},
                        )
                    )
        if project.get("prepared") is False and not recovery:
            values.append(diagnostic("preparation.stale", stage="prepare"))
        if recovery and not values:
            values.append(diagnostic("operation.pending", stage="prepare", severity="info"))
    access = project.get("windows_access", {})
    generic = {"health.details_missing", "health.probe_failed"}
    affected = {
        value.get("service")
        for value in values
        if value["severity"] == "error" and value["code"] not in generic
    }
    for check in access.get("checks", []):
        name = check.get("service")
        if not check.get("reachable") and name not in affected:
            values = [
                value
                for value in values
                if not (value.get("service") == name and value["code"] in generic)
            ]
            values.append(
                diagnostic(
                    "access.unavailable",
                    service=name,
                    stage="access",
                    severity="error" if services.get(name, {}).get("required", True) else "warning",
                    evidence={
                        "stage": check.get("stage"),
                        "code": check.get("code"),
                        "target": check.get("target"),
                        "http_status": check.get("http_status"),
                    },
                )
            )
        elif (
            name not in affected
            and type(check.get("http_status")) is int
            and any(value.get("service") == name and value["code"] in generic for value in values)
        ):
            values.append(
                diagnostic(
                    "access.observed",
                    service=name,
                    stage="access",
                    severity="info",
                    evidence={"target": check.get("target"), "http_status": check["http_status"]},
                )
            )
    if access.get("state") == "unknown":
        values.append(diagnostic("access.unknown", stage="access"))
    if project.get("error") or project.get("error_detail"):
        values.insert(0, error_diagnostics(detail=project.get("error_detail"))[0])
    return sorted(
        normalize(values),
        key=lambda value: {"error": 0, "warning": 1, "info": 2}[value["severity"]],
    )


def enrich(project):
    values = project_diagnostics(project)
    return {**project, "diagnostics": values, "summary": summary(values)}


def recovery_progress(recovery, *, now=None):
    if not recovery:
        return None
    now = time.time() if now is None else now

    def number(value):
        return value if type(value) in {int, float} and math.isfinite(value) else None

    deadline = number(recovery.get("next_retry_at"))
    waiting = isinstance(deadline, int | float) and deadline > now
    return {
        "state": "waiting"
        if waiting
        else "failed"
        if recovery.get("error")
        else "running"
        if recovery.get("updated_at")
        else "unknown",
        "stage": recovery.get("stage", recovery.get("operation")),
        "service": recovery.get("current_service"),
        "task": recovery.get("current_task"),
        "attempt": recovery.get("attempt"),
        "last_progress_at": number(recovery.get("updated_at")),
        "retry_in_seconds": max(0, round(deadline - now)) if waiting else None,
    }
