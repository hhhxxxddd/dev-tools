from __future__ import annotations

import json
import socket
import time
import urllib.request
from collections import deque

from ..i18n import error_detail, join_messages, message, t
from ..runtimes.platforms.locking import operation_lock
from . import SCHEMA_VERSION
from .drivers import install_spring_artifact, run_compose, spring_classpath
from .execution import Executor
from .models import ProjectBinding, ProjectError, ProjectSpec, dependency_order
from .planning import PreparationPlan, fingerprint, preparation_plan
from .registry import read_json, write_json


def _probe(service, executor) -> str:
    if service.driver == "compose":
        result = run_compose(executor, service, "ps", "--format", "json", capture=True, check=False)
        try:
            value = json.loads(result.stdout)
            containers = value if isinstance(value, list) else [value]
        except json.JSONDecodeError:
            try:
                containers = [
                    json.loads(line) for line in result.stdout.splitlines() if line.strip()
                ]
            except json.JSONDecodeError:
                containers = []
        healthy = bool(containers) and all(
            isinstance(item, dict)
            and str(item.get("State", "")).lower() == "running"
            and str(item.get("Health", "")).lower() not in {"unhealthy", "starting"}
            for item in containers
        )
        if result.returncode != 0 or not healthy:
            return "unhealthy"
    checks = service.health
    if not checks.get("tcp") and not checks.get("http"):
        return "healthy" if service.driver == "compose" else "unknown"
    try:
        if checks.get("tcp"):
            with socket.create_connection(("127.0.0.1", checks["tcp"]), timeout=0.5):
                pass
        if checks.get("http"):
            with urllib.request.urlopen(checks["http"], timeout=1) as response:
                if not 200 <= response.status < 400:
                    return "unhealthy"
        return "healthy"
    except OSError:
        return "unhealthy"


class ProjectEngine:
    def __init__(self, spec: ProjectSpec, binding: ProjectBinding, backend, *, executor=None):
        self.spec, self.binding, self.backend = spec, binding, backend
        self.executor = executor or Executor(spec, binding, backend)
        self.state_path = binding.state / "project.json"

    def state(self) -> dict:
        return read_json(self.state_path)

    def save(self, **updates) -> None:
        write_json(self.state_path, {**self.state(), **updates})

    def plan(self) -> PreparationPlan:
        return preparation_plan(self.spec, self.binding, self.backend)

    def service_status(self, name: str) -> dict:
        service = self.spec.services[name]
        worker = self.backend.phase(name)
        phase = worker.get("phase", "starting")
        health = _probe(service, self.executor) if phase == "running" else "unknown"
        return {
            "name": name,
            "driver": service.driver,
            "phase": phase,
            "health": health,
            "ready": phase == "running" and health != "unhealthy",
            "pid": worker.get("pid"),
            "exit_code": worker.get("exit_code"),
            "error": worker.get("error", ""),
            "error_detail": worker.get("error_detail"),
        }

    def status(self) -> dict:
        services = {name: self.service_status(name) for name in self.spec.service_order()}
        workers = {
            name: self.backend.phase(name)
            for name in self.backend.known_workers()
            if name.startswith("__")
        }
        state = self.state()
        recovery = state.get("recovery")
        prepared = state.get("prepared_revision") == fingerprint(self.binding)
        expected = ({"__sync"} if self.binding.source != self.binding.workspace else set()) | (
            {"__watch"} if self.spec.builds or self.spec.rebuild_on_branch else set()
        )
        ready = (
            bool(services)
            and prepared
            and not recovery
            and all(value["ready"] for value in services.values())
            and all(workers.get(name, {}).get("phase") == "running" for name in expected)
        )
        health = None if any(value["health"] == "unknown" for value in services.values()) else ready
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "status",
            "name": self.binding.name,
            "environment": self.binding.environment,
            "ready": ready,
            "healthy": health,
            "services": services,
            "workers": workers,
            "recovery": recovery,
            "prepared": prepared,
        }

    def _active(self) -> tuple[str, ...]:
        return tuple(name for name in self.backend.known_workers() if self.backend.active(name))

    def _stop(self, names: tuple[str, ...]) -> None:
        from .config import parse_project

        previous = self.state().get("last_project")
        spec = parse_project(previous, self.binding.environment) if previous else self.spec
        order = tuple(reversed(spec.service_order()))
        failures = []
        for name in (
            *[item for item in names if item.startswith("__")],
            *order,
            *[item for item in names if item not in order and not item.startswith("__")],
        ):
            if name not in names:
                continue
            try:
                self.backend.stop(name)
                service = spec.services.get(name)
                if service and service.driver == "compose":
                    run_compose(
                        self.executor, service, "down", "--remove-orphans", "--timeout", "20"
                    )
            except (ProjectError, OSError) as exc:
                failures.append(
                    message(
                        "{name}: {detail}", name=name, detail=exc.args[0] if exc.args else str(exc)
                    )
                )
        if failures:
            raise ProjectError(
                message(
                    "could not stop workers: {failures}", failures=join_messages("; ", failures)
                )
            )

    def _wait(self, name: str) -> None:
        deadline = time.monotonic() + float(self.spec.services[name].health.get("timeout", 15))
        while time.monotonic() < deadline:
            status = self.service_status(name)
            if status["ready"]:
                return
            if status["phase"] == "failed":
                raise ProjectError(
                    message(
                        "service {name} failed: {error}; inspect project logs",
                        name=name,
                        error=status.get("error_detail") or status["error"],
                    )
                )
            time.sleep(0.2)
        raise ProjectError(message("service {name} did not become ready before timeout", name=name))

    def _start(self, names: tuple[str, ...], *, infrastructure: bool = False) -> None:
        names = dependency_order(
            {name: service.depends_on for name, service in self.spec.services.items()}, names
        )
        started = []
        try:
            for name in names:
                if not self.backend.active(name):
                    service = self.spec.services[name]
                    if service.driver == "process" and service.health.get("tcp"):
                        with socket.socket() as probe:
                            probe.settimeout(0.3)
                            if probe.connect_ex(("127.0.0.1", service.health["tcp"])) == 0:
                                raise ProjectError(
                                    message(
                                        "service {name}: TCP port {tcp} is already listening",
                                        name=name,
                                        tcp=service.health["tcp"],
                                    )
                                )
                    started.append(name)
                    self.backend.start(name)
                self._wait(name)
            if infrastructure:
                workers = []
                if self.binding.source != self.binding.workspace:
                    workers.append("__sync")
                if self.spec.builds or self.spec.rebuild_on_branch:
                    workers.append("__watch")
                for name in workers:
                    if not self.backend.active(name):
                        started.append(name)
                        self.backend.start(name)
                    deadline = time.monotonic() + 30
                    while self.backend.phase(name).get("phase") != "running":
                        if (
                            self.backend.phase(name).get("phase") == "failed"
                            or time.monotonic() >= deadline
                        ):
                            raise ProjectError(
                                message(
                                    "monitor {name} failed to start; inspect project logs",
                                    name=name,
                                )
                            )
                        time.sleep(0.2)
        except (ProjectError, OSError) as exc:
            cleanup_error = ""
            try:
                self._stop(tuple(started))
            except (ProjectError, OSError) as cleanup:
                cleanup_error = str(cleanup)
            self.save(
                recovery={
                    "operation": "start",
                    "error": str(exc),
                    "error_detail": error_detail(exc),
                    "cleanup_error": cleanup_error,
                    "restore_workers": list(names),
                }
            )
            raise

    def _prepared(self) -> None:
        state = self.state()
        if state.get("recovery"):
            raise ProjectError(message("recovery is pending; run dev-tools prepare"))
        if state.get("prepared_revision") != fingerprint(self.binding):
            raise ProjectError(
                message("project declarations changed or are unprepared; run dev-tools prepare")
            )

    def prepare(self, plan: PreparationPlan | None = None) -> None:
        plan = plan or self.plan()
        if plan.unresolved:
            raise ProjectError(
                message(
                    "project preparation is unresolved: {unresolved}",
                    unresolved=join_messages("; ", plan.unresolved),
                )
            )
        self.backend.require_control()
        with operation_lock(self.binding.state, timeout=15):
            if plan.revision != fingerprint(self.binding):
                raise ProjectError(
                    message(
                        "project declarations changed after planning; create a new prepare plan"
                    )
                )
            recovery = self.state().get("recovery") or {}
            active = tuple(dict.fromkeys([*self._active(), *recovery.get("restore_workers", [])]))
            self.save(
                recovery={
                    "operation": "prepare",
                    "restore_workers": list(active),
                    "completed_tasks": [],
                }
            )
            completed = []
            try:
                self._stop(active)
                self.save(last_project=self.spec.raw)
                self.backend.install_requirements(plan.requirements)
                if self.spec.toolchain == "mise" and plan.runtime_versions:
                    self.executor.run(
                        [
                            "mise",
                            "--yes",
                            "-C",
                            str(self.binding.source),
                            "install",
                            *plan.runtime_versions,
                        ],
                        cwd=self.binding.source,
                    )
                self.backend.sync(self.spec)
                if plan.revision != fingerprint(self.binding):
                    raise ProjectError(message("project declarations changed during prepare"))
                for service in self.spec.services.values():
                    install_spring_artifact(self.executor, service)
                for name in plan.tasks:
                    self.executor.task(self.spec.tasks[name])
                    completed.append(name)
                    self.save(
                        recovery={
                            "operation": "prepare",
                            "restore_workers": list(active),
                            "completed_tasks": completed,
                        }
                    )
                for service in self.spec.services.values():
                    if service.driver == "compose":
                        options = service.options.get("compose", {})
                        if options.get("pull", False):
                            run_compose(self.executor, service, "pull")
                        if options.get("build", True):
                            run_compose(self.executor, service, "build")
                    spring_classpath(self.binding, self.backend, service)
                if plan.revision != fingerprint(self.binding):
                    raise ProjectError(
                        message("project declarations changed during preparation tasks")
                    )
                self.save(prepared_revision=plan.revision)
                self._start(
                    tuple(name for name in active if name in self.spec.services),
                    infrastructure=any(name.startswith("__") for name in active),
                )
                self.save(recovery=None)
            except (ProjectError, OSError) as exc:
                self.save(
                    recovery={
                        "operation": "prepare",
                        "restore_workers": list(active),
                        "completed_tasks": completed,
                        "error": str(exc),
                        "error_detail": error_detail(exc),
                    }
                )
                raise

    def start(self, selected: tuple[str, ...] | None = None) -> None:
        self.backend.require_control()
        with operation_lock(self.binding.state, timeout=15):
            self._prepared()
            graph = {name: service.depends_on for name, service in self.spec.services.items()}
            names = dependency_order(graph, selected)
            if not names:
                raise ProjectError(message("no runtime services are declared"))
            self.backend.sync(self.spec)
            for name in names:
                spring_classpath(self.binding, self.backend, self.spec.services[name])
            from .monitoring import git_snapshot

            self.save(git=git_snapshot(self.executor))
            self._start(names, infrastructure=True)

    def stop(self) -> None:
        self.backend.require_control()
        with operation_lock(self.binding.state, timeout=15):
            self._stop(tuple(dict.fromkeys([*self.backend.known_workers(), *self.spec.services])))
            recovery = self.state().get("recovery")
            if recovery:
                self.save(recovery={**recovery, "restore_workers": []})

    def restart(self) -> None:
        self.backend.require_control()
        with operation_lock(self.binding.state, timeout=15):
            self._prepared()
            self._stop(self._active())
            self.backend.sync(self.spec)
            self._start(self.spec.service_order(), infrastructure=True)

    def rebuild(
        self, *, service: str | None = None, kind: str = "branch", lock_timeout: float = 15
    ) -> None:
        if kind not in {"branch", "source", "resource", "structural"}:
            raise ProjectError(message("unknown build kind: {kind}", kind=kind))
        if service is not None and not any(item.service == service for item in self.spec.builds):
            raise ProjectError(message("no build policy for service: {service}", service=service))
        if service is not None and kind == "branch":
            kind = "structural"
        self.backend.require_control()
        with operation_lock(self.binding.state, timeout=lock_timeout):
            from .config import load_project

            if load_project(self.binding.source, self.binding.environment).raw != self.spec.raw:
                raise ProjectError(message("project declaration changed; run dev-tools prepare"))
            plan = self.plan()
            if plan.unresolved:
                raise ProjectError(
                    message(
                        "build cannot proceed; run dev-tools prepare: {unresolved}",
                        unresolved=join_messages("; ", plan.unresolved),
                    )
                )
            recovery = self.state().get("recovery") or {}
            if recovery and recovery.get("operation") != "build":
                raise ProjectError(message("recovery is pending; run dev-tools prepare"))
            active = tuple(
                dict.fromkeys(
                    [
                        *[name for name in self._active() if not name.startswith("__")],
                        *recovery.get("restore_workers", []),
                    ]
                )
            )
            affected = set(active) if service is None else {service}
            while True:
                dependents = {
                    name
                    for name, item in self.spec.services.items()
                    if affected.intersection(item.depends_on)
                }
                if dependents <= affected:
                    break
                affected.update(dependents)
            stop = tuple(name for name in active if name in affected) if kind != "source" else ()
            record = {
                "operation": "build",
                "service": service,
                "kind": kind,
                "restore_workers": list(stop),
            }
            self.save(recovery=record)
            try:
                self._stop(stop)
                self.backend.sync(self.spec)
                if kind == "branch":
                    selected = tuple(
                        dict.fromkeys(
                            [*plan.tasks, *[item.structural_task for item in self.spec.builds]]
                        )
                    )
                else:
                    selected = tuple(
                        dict.fromkeys(
                            getattr(item, f"{kind}_task")
                            for item in self.spec.builds
                            if service is None or item.service == service
                        )
                    )
                names = self.spec.task_order(selected)
                for name in names:
                    self.executor.task(self.spec.tasks[name])
                for item in self.spec.services.values():
                    if service is not None and item.name != service:
                        continue
                    if (
                        item.driver == "compose"
                        and service is None
                        and kind in {"branch", "structural"}
                    ):
                        run_compose(self.executor, item, "build")
                    spring_classpath(self.binding, self.backend, item, reload=True)
                if plan.revision != fingerprint(self.binding):
                    raise ProjectError(
                        message("project declarations changed during build; run prepare")
                    )
                self.save(prepared_revision=plan.revision, recovery=None)
                self._start(stop)
            except (ProjectError, OSError) as exc:
                self.save(recovery={**record, "error": str(exc), "error_detail": error_detail(exc)})
                raise

    def logs(
        self, service: str, *, lines: int = 100, follow: bool = False, task: bool = False
    ) -> None:
        allowed = self.spec.tasks if task else {*self.spec.services, "__sync", "__watch"}
        if service not in allowed:
            raise ProjectError(message("unknown service: {service}", service=service))
        directory = "logs/tasks" if task else "logs"
        path = self.binding.state / directory / f"{service}.log"
        if not path.is_file():
            print(t("暂无日志。"))
            return
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in deque(stream, maxlen=lines):
                print(line, end="")
            while follow:
                line = stream.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.25)
