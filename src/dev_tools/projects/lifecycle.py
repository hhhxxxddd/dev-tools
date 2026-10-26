"""Shared service selection, startup outcomes and recovery policy."""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..i18n import error_detail, join_messages, message
from .diagnostics import error_diagnostics
from .models import ProjectError, ProjectSpec, dependency_order
from .registry import write_json

if TYPE_CHECKING:
    from ..runtimes.platforms.base import Backend


@dataclass(frozen=True)
class ServiceSelection:
    names: tuple[str, ...]
    required: frozenset[str]

    @classmethod
    def resolve(
        cls, spec: ProjectSpec, selected: tuple[str, ...] | None = None
    ) -> ServiceSelection:
        names = dependency_order(
            {name: item.depends_on for name, item in spec.services.items()}, selected
        )
        return cls(names, frozenset(spec.required_services(names)))


def start_selection(
    spec: ProjectSpec, selected: tuple[str, ...] | None, active: tuple[str, ...]
) -> ServiceSelection:
    """Restore active services during prepare without reopening unrelated optional ones."""
    names = (
        None
        if selected is None
        else tuple(dict.fromkeys([*selected, *[name for name in active if name in spec.services]]))
    )
    return ServiceSelection.resolve(spec, names)


@dataclass(frozen=True)
class RestorePlan:
    names: tuple[str, ...]
    infrastructure: bool
    intent_token: str | None


def restoration_plan(
    spec: ProjectSpec, intent: dict, active: tuple[str, ...]
) -> RestorePlan | None:
    """An explicit stop cancels recovery; closed optional services remain closed."""
    if intent and not intent.get("running"):
        return None
    names = tuple(name for name in active if name in spec.services)
    if intent.get("running"):
        selected = intent.get("selected")
        selection = ServiceSelection.resolve(
            spec,
            None if selected is None else tuple(name for name in selected if name in spec.services),
        )
        names = tuple(
            name for name in selection.names if name in selection.required or name in active
        )
    return RestorePlan(
        names,
        bool(intent.get("running")) or any(name.startswith("__") for name in active),
        intent.get("token"),
    )


@dataclass(frozen=True)
class ServiceFailure:
    name: str
    error: ProjectError | OSError

    def worker_state(self) -> dict:
        return {
            "phase": "failed",
            "error": str(self.error),
            "error_detail": error_detail(self.error),
            "exit_code": 1,
        }

    def description(self) -> str:
        return message(
            "{name}: {detail}",
            name=self.name,
            detail=self.error.args[0] if self.error.args else str(self.error),
        )


@dataclass(frozen=True)
class StartResult:
    optional_failures: tuple[ServiceFailure, ...] = ()

    def raise_for_failures(self) -> None:
        if self.optional_failures:
            raise ProjectError(
                message(
                    "optional services failed; shared services remain running: {errors}",
                    errors=join_messages(
                        "; ", [item.description() for item in self.optional_failures]
                    ),
                )
            )


class ServiceStartup:
    """Execute one selection and track only workers owned by this startup attempt."""

    def __init__(
        self,
        spec: ProjectSpec,
        backend: Backend,
        selection: ServiceSelection,
        *,
        wait: Callable[..., None],
        stop: Callable[[tuple[str, ...]], None],
        check_intent: Callable[[str | None], None],
    ):
        self.spec, self.backend, self.selection = spec, backend, selection
        self.wait, self.stop, self.check_intent = wait, stop, check_intent
        self.started: list[str] = []

    def run(self, intent_token: str | None = None) -> StartResult:
        from ..progress import stage

        failures = []
        for name in self.selection.names:
            self.check_intent(intent_token)
            try:
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
                    self.started.append(name)
                    with stage("Start service: {service}", service=name):
                        self.backend.start(name)
                self.wait(name, intent_token=intent_token, required=name in self.selection.required)
            except (ProjectError, OSError) as exc:
                self.check_intent(intent_token)
                if name in self.selection.required:
                    raise
                if name in self.started:
                    self.stop((name,))
                failure = ServiceFailure(name, exc)
                write_json(
                    self.backend.binding.state / "workers" / f"{name}.json", failure.worker_state()
                )
                failures.append(failure)
        return StartResult(tuple(failures))

    def rollback(self, error: BaseException) -> dict:
        cleanup_error = ""
        try:
            self.stop(tuple(self.started))
        except (ProjectError, OSError) as exc:
            cleanup_error = str(exc)
        return {
            "operation": "start",
            "error": str(error),
            "error_detail": error_detail(error),
            "diagnostics": error_diagnostics(error),
            "cleanup_error": cleanup_error,
            "restore_workers": list(self.selection.names),
        }
