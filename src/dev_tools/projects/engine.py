from __future__ import annotations

import json
import socket
import time
import uuid

from ..i18n import error_detail, join_messages, message, t
from ..inspection import Inspection
from ..runtimes.platforms.locking import operation_lock
from ..schemas import CLI_SCHEMA_VERSION
from .access import access_targets
from .config import CONFIG_NAME, load_project, render_toml
from .diagnostic_evidence import failure_log, safe_message
from .diagnostics import diagnostic, enrich, error_diagnostics, recovery_progress
from .drivers import install_spring_artifact, run_compose, spring_classpath
from .execution import Executor
from .health import connection_failure, http_probe
from .lifecycle import ServiceSelection, ServiceStartup, restoration_plan, start_selection
from .logfiles import read_logs
from .maintenance import LOCKFILES, atomic_write, clean_venv_task, discovery_plan
from .models import ProjectBinding, ProjectError, ProjectSpec, dependency_order, within
from .planning import PreparationPlan, fingerprint, preparation_plan, preparation_revisions
from .registry import read_json, write_json


def _probe(service, executor) -> tuple[str, list[dict]]:
    if service.driver == "compose":
        try:
            result = run_compose(
                executor, service, "ps", "--format", "json", capture=True, check=False, timeout=5
            )
        except (ProjectError, OSError) as exc:
            return "unhealthy", [
                diagnostic(
                    "compose.query_failed",
                    stage="health",
                    evidence={"message": safe_message(str(exc))},
                )
            ]
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
        if result.returncode != 0:
            return "unhealthy", [
                diagnostic(
                    "compose.query_failed",
                    stage="health",
                    evidence={
                        "exit_code": result.returncode,
                        "message": safe_message(result.stderr or "docker compose ps"),
                    },
                )
            ]
        if not containers:
            return "unhealthy", [diagnostic("compose.empty", stage="health")]
        if not healthy:
            values = [
                diagnostic(
                    "compose.container_state",
                    stage="health",
                    evidence={
                        "container": item.get("Name") or item.get("Service") or "unknown",
                        "state": str(item.get("State", "unknown")).lower(),
                        "health": str(item.get("Health") or "unknown").lower(),
                    },
                )
                for item in containers
                if isinstance(item, dict)
                and (
                    str(item.get("State", "")).lower() != "running"
                    or str(item.get("Health", "")).lower() in {"unhealthy", "starting"}
                )
            ]
            return "unhealthy", values or [diagnostic("compose.unhealthy", stage="health")]
    checks = service.health
    if not checks.get("tcp") and not checks.get("http"):
        return ("healthy" if service.driver == "compose" else "unknown"), []
    try:
        if checks.get("tcp"):
            with socket.create_connection(("127.0.0.1", checks["tcp"]), timeout=0.5):
                pass
        if checks.get("http"):
            healthy, values = http_probe(checks)
            return ("healthy" if healthy else "unhealthy"), values
        return "healthy", []
    except OSError as exc:
        value = connection_failure(exc)
        if checks.get("tcp"):
            value["evidence"]["target"] = f"tcp://127.0.0.1:{checks['tcp']}"
        return "unhealthy", [value]


class ProjectEngine:
    def __init__(self, spec: ProjectSpec, binding: ProjectBinding, backend, *, executor=None):
        self.spec, self.binding, self.backend = spec, binding, backend
        self.executor = executor or Executor(spec, binding, backend)
        self.state_path = binding.state / "project.json"

    def state(self) -> dict:
        return read_json(self.state_path)

    def save(self, **updates) -> None:
        state = self.state()
        if updates.get("recovery"):
            previous = state.get("recovery") or {}
            recovery = dict(updates["recovery"])
            for key in (
                "attempt",
                "started_at",
                "stage",
                "current_service",
                "current_task",
                "last_failure",
            ):
                if key in previous:
                    recovery.setdefault(key, previous[key])
            recovery.setdefault("attempt", 1)
            recovery.setdefault("started_at", time.time())
            recovery["updated_at"] = time.time()
            if recovery.get("error"):
                recovery["last_failure"] = recovery.get("diagnostics") or error_diagnostics(
                    detail=recovery.get("error_detail")
                )
            updates["recovery"] = recovery
        write_json(self.state_path, {**state, **updates})

    def recovery_stage(self, stage, *, service=None, task=None):
        recovery = self.state().get("recovery")
        if recovery:
            self.save(
                recovery={
                    **recovery,
                    "stage": stage,
                    "current_service": service,
                    "current_task": task,
                }
            )

    def plan(self, *, force=False) -> PreparationPlan:
        with Inspection(self.binding.source, platform=self.binding.environment).activate():
            declared = load_project(self.binding.source, self.binding.environment)
            return self._plan(declared, force=force)

    def _plan(self, declared: ProjectSpec, *, force=False) -> PreparationPlan:
        state = self.state()
        spec, baseline = discovery_plan(
            declared, self.binding.source, state, self.binding.environment
        )
        return preparation_plan(
            spec,
            self.binding,
            self.backend,
            state=state,
            discovery_baseline=baseline,
            configuration_update=spec.raw != declared.raw,
            force=force,
        )

    def intent(self) -> dict:
        return read_json(self.binding.state / "intent.json")

    def set_intent(self, *, running: bool, selected=None) -> dict:
        intent = {"running": running, "selected": selected, "token": uuid.uuid4().hex}
        write_json(self.binding.state / "intent.json", intent)
        return intent

    def _set_spec(self, spec: ProjectSpec) -> None:
        self.spec = spec
        self.executor.spec = spec

    def _check_intent(self, token: str | None) -> None:
        if token and self.intent().get("token") != token:
            raise ProjectError(message("project stopped during maintenance"))

    def service_status(self, name: str, *, worker=None) -> dict:
        service = self.spec.services[name]
        worker = self.backend.phase(name) if worker is None else worker
        phase = worker.get("phase", "starting")
        health, diagnostics = (
            _probe(service, self.executor) if phase == "running" else ("unknown", [])
        )
        for value in diagnostics:
            value["service"] = name
        log_diagnostics = []
        if phase in {"failed", "restarting"} or phase == "running" and health == "unhealthy":
            failure = worker.get("last_failure") or {}
            offset = (
                failure.get("log_start")
                if phase in {"failed", "restarting"}
                else worker.get("log_start")
            )
            end = failure.get("log_end") if phase in {"failed", "restarting"} else None
            confirmed = type(offset) is int and type(end) is int and 0 <= offset <= end
            log_diagnostics = [
                diagnostic(
                    "log.message" if confirmed else "log.tail",
                    service=name,
                    severity="info",
                    evidence={"message": text},
                )
                for text in failure_log(
                    self.binding.state / "logs" / f"{name}.log",
                    worker,
                    start=offset if type(offset) is int else 0,
                    end=end if confirmed else None,
                )
            ]
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
            "diagnostics": diagnostics,
            "log_diagnostics": log_diagnostics,
            "updated_at": worker.get("updated_at"),
            "last_failure": worker.get("last_failure"),
            "restart_count": worker.get("restart_count"),
        }

    def status(self) -> dict:
        known = self.backend.known_workers()
        names = tuple(dict.fromkeys([*self.spec.service_order(), *known]))
        phases = self.backend.phases(names)
        services = {
            name: self.service_status(name, worker=phases[name])
            for name in self.spec.service_order()
        }
        intent = self.intent()
        required = ServiceSelection.resolve(
            self.spec,
            tuple(intent["selected"])
            if intent.get("running") and intent.get("selected") is not None
            else None,
        ).required
        for name, value in services.items():
            value["required"] = name in required
        workers = {name: phases[name] for name in known if name.startswith("__")}
        state = self.state()
        recovery = state.get("recovery")
        prepared = state.get("prepared_revision") == fingerprint(self.binding)
        expected = ({"__sync"} if self.binding.source != self.binding.workspace else set()) | {
            "__watch"
        }
        ready = (
            bool(services)
            and prepared
            and not recovery
            and all(services[name]["ready"] for name in required)
            and all(workers.get(name, {}).get("phase") == "running" for name in expected)
        )
        health = None if any(services[name]["health"] == "unknown" for name in required) else ready
        return enrich(
            {
                "schema_version": CLI_SCHEMA_VERSION,
                "action": "status",
                "name": self.binding.name,
                "environment": self.binding.environment,
                "ready": ready,
                "healthy": health,
                "services": services,
                "workers": workers,
                "recovery": recovery,
                "prepared": prepared,
                "maintenance_error": state.get("maintenance_error"),
                "desired_running": intent.get("running"),
                "expected_workers": sorted(expected) if intent.get("running") else [],
                "recovery_progress": recovery_progress(recovery),
                **(
                    {
                        "health_scope": "wsl-local",
                        "access_targets": access_targets(
                            self.spec, self.binding.workspace, source_root=self.binding.source
                        ),
                    }
                    if self.binding.environment == "wsl"
                    else {}
                ),
            }
        )

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

    def _wait(self, name: str, *, intent_token=None, required: bool | None = None) -> None:
        from ..progress import stage

        self.recovery_stage("health", service=name)
        with stage("Health check: {service}", service=name):
            self._wait_ready(name, intent_token=intent_token, required=required)

    def _wait_ready(self, name: str, *, intent_token=None, required: bool | None = None) -> None:
        required = self.spec.services[name].required if required is None else required
        deadline = time.monotonic() + float(self.spec.services[name].health.get("timeout", 15))
        status = {}
        while time.monotonic() < deadline:
            self._check_intent(intent_token)
            status = self.service_status(name)
            if status["ready"]:
                return
            if not required and status["phase"] == "stopped" and status.get("exit_code") == 0:
                return
            if status["phase"] == "failed":
                error = ProjectError(
                    message(
                        "service {name} failed: {error}; inspect project logs",
                        name=name,
                        error=status.get("error_detail") or status["error"],
                    )
                )
                error.diagnostics = enrich({"services": {name: status}})["diagnostics"]
                raise error
            time.sleep(0.2)
        error = ProjectError(
            message("service {name} did not become ready before timeout", name=name)
        )
        error.diagnostics = enrich({"services": {name: status}})["diagnostics"] or [
            diagnostic(
                "process.exited" if type(status.get("exit_code")) is int else "service.not_ready",
                service=name,
                stage="health",
                evidence={"exit_code": status.get("exit_code")},
            )
        ]
        raise error

    def _start(
        self,
        names: tuple[str, ...],
        *,
        infrastructure: bool = False,
        intent_token=None,
        monitors: tuple[str, ...] | None = None,
        report_optional_errors: bool = True,
    ) -> None:
        from ..progress import stage

        startup = ServiceStartup(
            self.spec,
            self.backend,
            ServiceSelection.resolve(self.spec, names),
            wait=self._wait,
            stop=self._stop,
            check_intent=self._check_intent,
        )
        try:
            result = startup.run(intent_token)
            if infrastructure:
                with stage("Start file and dependency monitors"):
                    self._start_monitors(startup.started, intent_token, monitors)
        except (ProjectError, OSError, KeyboardInterrupt) as exc:
            self.save(recovery=startup.rollback(exc))
            raise
        if report_optional_errors:
            result.raise_for_failures()

    def _start_monitors(self, started, intent_token, monitors):
        workers = []
        if self.binding.source != self.binding.workspace:
            workers.append("__sync")
        workers.append("__watch")
        if monitors is not None:
            workers = [worker for worker in workers if worker in monitors]
        for name in workers:
            self._check_intent(intent_token)
            if not self.backend.active(name):
                started.append(name)
                self.backend.start(name)
            # Initial snapshots on mounted filesystems can take tens of seconds.
            deadline = time.monotonic() + 120
            while True:
                phase = self.backend.phase(name).get("phase")
                if phase == "running":
                    break
                self._check_intent(intent_token)
                if phase == "failed" or time.monotonic() >= deadline:
                    raise ProjectError(
                        message(
                            "monitor {name} failed to start; inspect project logs",
                            name=name,
                        )
                    )
                time.sleep(0.2)

    def _validate_plan(self, plan: PreparationPlan) -> None:
        if plan.unresolved:
            raise ProjectError(
                message(
                    "project preparation is unresolved: {unresolved}",
                    unresolved=join_messages("; ", plan.unresolved),
                )
            )
        if plan.revision != fingerprint(self.binding, spec=plan.spec):
            raise ProjectError(
                message("project declarations changed after planning; create a new prepare plan")
            )

    def _resolve_dependencies(self, plan, revision: str, intent_token) -> str:
        declarations = fingerprint(self.binding, spec=self.spec, include_locks=False)
        native_declarations = fingerprint(
            self.binding, spec=self.spec, root=self.binding.workspace, include_locks=False
        )
        for task in plan.resolution_tasks:
            self._check_intent(intent_token)
            self.executor.task(task)
        if native_declarations != fingerprint(
            self.binding, spec=self.spec, root=self.binding.workspace, include_locks=False
        ):
            raise ProjectError(message("dependency resolution changed project declarations"))
        if self.binding.source == self.binding.workspace:
            if declarations != fingerprint(self.binding, spec=self.spec, include_locks=False):
                raise ProjectError(message("project declarations changed during prepare"))
        elif plan.resolution_tasks:
            if revision != fingerprint(self.binding, spec=self.spec):
                raise ProjectError(message("project declarations changed during prepare"))
            # The only files copied back are known lockfiles from reviewed resolver steps.
            updates = {}
            for task in plan.resolution_tasks:
                for filename in LOCKFILES[task.manager]:
                    native = within(self.binding.workspace, task.workdir) / filename
                    within(
                        self.binding.workspace,
                        native.relative_to(self.binding.workspace).as_posix(),
                    )
                    if native.is_symlink():
                        raise ProjectError(
                            message("refusing symbolic-link update: {path}", path=native)
                        )
                    if native.is_file():
                        source = within(self.binding.source, task.workdir) / filename
                        within(
                            self.binding.source, source.relative_to(self.binding.source).as_posix()
                        )
                        updates[source] = native.read_bytes()
            for source, content in updates.items():
                self._check_intent(intent_token)
                if revision != fingerprint(self.binding, spec=self.spec):
                    raise ProjectError(message("project declarations changed during prepare"))
                atomic_write(source, content)
                revision = fingerprint(self.binding, spec=self.spec)
        return fingerprint(self.binding, spec=self.spec)

    def _prepare_locked(
        self,
        plan: PreparationPlan,
        *,
        preserve_watch=False,
        restore=True,
        intent_token=None,
        structural=False,
    ) -> None:
        from ..progress import stage

        self._validate_plan(plan)
        recovery = self.state().get("recovery") or {}
        active = tuple(dict.fromkeys([*self._active(), *recovery.get("restore_workers", [])]))
        completed = []
        revision = plan.revision
        self.save(
            recovery={
                "operation": "prepare",
                "attempt": recovery.get("attempt", 0) + 1,
                "stage": "prepare",
                "current_service": None,
                "current_task": None,
                "next_retry_at": None,
                "last_failure": recovery.get("last_failure")
                or recovery.get("diagnostics")
                or (
                    error_diagnostics(detail=recovery.get("error_detail"))
                    if recovery.get("error")
                    else []
                ),
                "restore_workers": list(active),
                "completed_tasks": [],
            }
        )
        try:
            self._check_intent(intent_token)
            self._stop(tuple(name for name in active if not (preserve_watch and name == "__watch")))
            self._set_spec(plan.spec)
            if revision != fingerprint(self.binding, spec=self.spec):
                raise ProjectError(message("project declarations changed during prepare"))
            if plan.configuration_update:
                atomic_write(self.binding.source / CONFIG_NAME, render_toml(self.spec.raw).encode())
                revision = fingerprint(self.binding, spec=self.spec)
            self.save(last_project=self.spec.raw)
            with stage("Check platform prerequisites"):
                self.backend.install_requirements(plan.requirements)
            self._check_intent(intent_token)
            if plan.runtime_install:
                with stage(
                    "Install declared runtimes: {versions}",
                    versions=", ".join(plan.runtime_versions),
                ):
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
            self._check_intent(intent_token)
            with stage("Synchronize native workspace"):
                self.backend.sync(self.spec)
            if revision != fingerprint(self.binding, spec=self.spec):
                raise ProjectError(message("project declarations changed during prepare"))
            if plan.resolution_tasks:
                with stage("Resolve dependency and lockfile changes"):
                    revision = self._resolve_dependencies(plan, revision, intent_token)
            with stage("Prepare service artifacts"):
                for service in self.spec.services.values():
                    install_spring_artifact(self.executor, service)
            selected = plan.tasks if structural else plan.pending_tasks
            if structural:
                selected = self.spec.task_order(
                    tuple(
                        dict.fromkeys(
                            [*selected, *[item.structural_task for item in self.spec.builds]]
                        )
                    )
                )
            for name in selected:
                self._check_intent(intent_token)
                self.recovery_stage("prepare", task=name)
                with stage("Run dependency/build task: {task}", task=name):
                    self.executor.task(
                        clean_venv_task(self.spec.tasks[name], self.binding.workspace)
                    )
                completed.append(name)
                self.save(
                    recovery={
                        "operation": "prepare",
                        "restore_workers": list(active),
                        "completed_tasks": completed,
                    }
                )
            for service in self.spec.services.values():
                self._check_intent(intent_token)
                if service.driver == "compose":
                    options = service.options.get("compose", {})
                    if options.get("pull", False):
                        run_compose(self.executor, service, "pull")
                    if options.get("build", True):
                        run_compose(self.executor, service, "build")
                spring_classpath(self.binding, self.backend, service, reload=structural)
            if revision != fingerprint(self.binding, spec=self.spec):
                raise ProjectError(message("project declarations changed during preparation tasks"))
            declarations, installations = preparation_revisions(self.spec, self.binding, plan.tasks)
            self.save(
                prepared_revision=revision,
                dependency_revisions=declarations,
                task_revisions=installations,
                runtime_versions=list(plan.runtime_versions),
                discovery_baseline=plan.discovery_baseline,
                maintenance_error=None,
            )
            if restore:
                restoration = restoration_plan(self.spec, self.intent(), active)
                if restoration is not None:
                    self._start(
                        restoration.names,
                        infrastructure=restoration.infrastructure,
                        intent_token=restoration.intent_token,
                        report_optional_errors=False,
                    )
            self.save(recovery=None)
        except (ProjectError, OSError, KeyboardInterrupt) as exc:
            self.save(
                recovery={
                    "operation": "prepare",
                    "restore_workers": list(active),
                    "completed_tasks": completed,
                    "error": str(exc),
                    "error_detail": error_detail(exc),
                    "diagnostics": error_diagnostics(exc),
                }
            )
            raise

    def prepare(self, plan: PreparationPlan | None = None) -> None:
        plan = plan or self.plan(force=True)
        self._validate_plan(plan)
        self.backend.require_control()
        with operation_lock(self.binding.state, timeout=15):
            intent = self.intent()
            self._prepare_locked(
                plan, intent_token=intent.get("token") if intent.get("running") else None
            )

    def maintain(self, *, structural=False) -> None:
        self.backend.require_control()
        with operation_lock(self.binding.state):
            intent = self.intent()
            if intent and not intent.get("running"):
                return
            try:
                self._prepare_locked(
                    self.plan(),
                    preserve_watch=True,
                    intent_token=intent.get("token"),
                    structural=structural,
                )
            except (ValueError, OSError) as exc:
                recovery = self.state().get("recovery")
                self.save(
                    maintenance_error=error_detail(exc),
                    **(
                        {"recovery": {**recovery, "next_retry_at": time.time() + 30}}
                        if recovery
                        else {}
                    ),
                )
                raise

    def _prepare_for_start(self, plan, token):
        if (
            self.state().get("recovery")
            or self.state().get("prepared_revision") != plan.revision
            or plan.pending_tasks
            or plan.runtime_install
            or plan.configuration_update
        ):
            self._prepare_locked(plan, restore=False, intent_token=token)
        else:
            self._set_spec(plan.spec)

    def start(self, selected: tuple[str, ...] | None = None) -> None:
        from ..progress import stage
        from ..runtimes.platforms.layout import storage

        update = read_json(
            storage(self.binding.environment)[1] / ".controller-update/recovery.json"
        )
        if update.get("phase") in {"stopping", "stopped"}:
            raise ProjectError(
                message("Controller update is in progress; retry start after it finishes")
            )
        self.backend.require_control()
        with operation_lock(self.binding.state, timeout=15):
            previous = self.intent()
            active = tuple(name for name in self._active() if name in self.spec.services)
            with stage("Analyze declarations and dependency changes"):
                plan = self.plan()
                self._validate_plan(plan)
            graph = {name: service.depends_on for name, service in plan.spec.services.items()}
            names = dependency_order(graph, selected)
            if not names:
                raise ProjectError(message("no runtime services are declared"))
            if self.intent() != previous:
                raise ProjectError(message("project stopped during maintenance"))
            desired = (
                None
                if selected is None or previous.get("running") and previous.get("selected") is None
                else list(
                    dict.fromkeys(
                        [
                            *[name for name in previous.get("selected") or [] if name in graph],
                            *names,
                        ]
                    )
                )
            )
            intent = self.set_intent(running=True, selected=desired)
            token = intent["token"]
            with stage("Prepare project environment"):
                self._prepare_for_start(plan, token)
            names = start_selection(self.spec, selected, active).names
            self._check_intent(token)
            with stage("Synchronize native workspace and service artifacts"):
                self.backend.sync(self.spec)
                for name in names:
                    spring_classpath(self.binding, self.backend, self.spec.services[name])
            from .monitoring import git_snapshot

            self.save(git=git_snapshot(self.executor), last_project=self.spec.raw)
            self._start(names, infrastructure=True, intent_token=token)

    def stop(self) -> None:
        self.backend.require_control()
        # Publish cancellation before waiting for a long package/build operation.
        self.set_intent(running=False)
        with operation_lock(self.binding.state, timeout=15):
            self.set_intent(running=False)
            self._stop(tuple(dict.fromkeys([*self.backend.known_workers(), *self.spec.services])))
            recovery = self.state().get("recovery")
            if recovery:
                self.save(recovery={**recovery, "restore_workers": []})

    def restart(self) -> None:
        self.stop()
        self.start()

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
            if (
                (plan.runtime_install and self.spec.toolchain == "mise")
                or plan.configuration_update
                or plan.resolution_tasks
                or plan.requirements.packages
                or plan.requirements.enable_docker
                or plan.requirements.docker_group
            ):
                raise ProjectError(
                    message("build requires preparation; run dev-tools start or prepare")
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
                "attempt": recovery.get("attempt", 0) + 1,
                "stage": "build",
                "current_service": service,
                "current_task": None,
                "next_retry_at": None,
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
                    task = self.spec.tasks[name]
                    self.recovery_stage("build", task=name)
                    self.executor.task(
                        clean_venv_task(task, self.binding.workspace) if kind == "branch" else task
                    )
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
                if kind == "branch":
                    declarations, installations = preparation_revisions(
                        self.spec, self.binding, plan.tasks
                    )
                    self.save(
                        prepared_revision=plan.revision,
                        dependency_revisions=declarations,
                        task_revisions=installations,
                        runtime_versions=list(plan.runtime_versions),
                        recovery=None,
                    )
                else:
                    self.save(recovery=None)
                intent = self.intent()
                if not intent or intent.get("running"):
                    self._start(stop, intent_token=intent.get("token"))
            except (ProjectError, OSError) as exc:
                self.save(
                    recovery={
                        **record,
                        "error": str(exc),
                        "error_detail": error_detail(exc),
                        "diagnostics": error_diagnostics(exc),
                        "next_retry_at": time.time() + 30 if lock_timeout == 0 else None,
                    }
                )
                raise

    def logs(
        self,
        service: str | None = None,
        *,
        lines: int = 100,
        follow: bool = False,
        task: bool = False,
    ) -> None:
        allowed = self.spec.tasks if task else {*self.spec.services, "__sync", "__watch"}
        if service is not None and service not in allowed:
            raise ProjectError(message("unknown service: {service}", service=service))
        directory = "logs/tasks" if task else "logs"
        names = [service] if service else sorted(allowed)
        paths = {name: self.binding.state / directory / f"{name}.log" for name in names}
        if not paths or not any(path.is_file() for path in paths.values()) and not follow:
            print(t("暂无日志。"))
            return
        if follow and not any(path.is_file() for path in paths.values()):
            print(t("暂无日志，等待新增内容；按 Ctrl+C 停止。"), flush=True)
        previous, start = None, True
        for name, content in read_logs(paths, lines=lines, follow=follow):
            if service is not None:
                print(content, end="", flush=follow)
                continue
            fragments = []
            if previous != name and not start:
                fragments.append("\n")
                start = True
            for part in content.splitlines(keepends=True):
                if start:
                    fragments.append(f"[{name}] ")
                fragments.append(part)
                start = part.endswith("\n")
            print("".join(fragments), end="", flush=follow)
            previous = name
        if service is None and not start:
            print()
