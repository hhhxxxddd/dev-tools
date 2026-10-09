"""The same service and monitor worker runs behind both native supervisors."""

from __future__ import annotations

import signal
import subprocess
import time
from threading import Event

from ..i18n import error_detail, message
from .drivers import compose_command, spring_classpath
from .models import ProjectError
from .monitoring import sync_loop, watch_loop
from .registry import write_json


def run_worker(engine, name: str) -> int:
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    phase_path = engine.binding.state / "workers" / f"{name}.json"
    log_path = engine.binding.state / "logs" / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def phase(value: str, **details) -> None:
        write_json(phase_path, {"phase": value, "updated_at": time.time(), **details})

    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        if name in {"__sync", "__watch"}:
            import contextlib

            try:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    (sync_loop if name == "__sync" else watch_loop)(
                        engine, stop, lambda: phase("running")
                    )
                phase("stopped")
                return 0
            except (ProjectError, OSError) as exc:
                log.write(f"{exc}\n")
                phase("failed", error=str(exc), error_detail=error_detail(exc), exit_code=1)
                return 1
        if name not in engine.spec.services:
            raise ProjectError(message("unknown service: {service}", service=name))
        service = engine.spec.services[name]
        while not stop.is_set():
            child = None
            try:
                command = (
                    compose_command(engine.binding, service, "up", "--no-build", "--remove-orphans")
                    if service.driver == "compose"
                    else service.command
                )
                classpath = spring_classpath(engine.binding, engine.backend, service)
                argv, cwd, environment = engine.executor.resolve(
                    command,
                    workdir=service.workdir,
                    options=service.options,
                    additions=service.env,
                    spring_classpath=classpath,
                )
                child = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    **engine.backend.child_options(),
                )
                phase("running", pid=child.pid)
                while child.poll() is None and not stop.wait(0.2):
                    pass
                if stop.is_set():
                    engine.backend.terminate_child(child)
                    phase("stopped")
                    return 0
                code = child.wait()
                engine.backend.terminate_child(child)
                restart = (
                    service.restart == "always" or service.restart == "on-failure" and code != 0
                )
                phase(
                    "restarting" if restart else "failed" if code else "stopped",
                    exit_code=code,
                    error=f"command exited with code {code}" if code else "",
                    error_detail=message("command exited with code {code}", code=code).as_dict()
                    if code
                    else None,
                )
                if not restart:
                    return code
                stop.wait(1)
            except (ProjectError, OSError) as exc:
                if child:
                    engine.backend.terminate_child(child)
                log.write(f"{exc}\n")
                phase("failed", error=str(exc), error_detail=error_detail(exc), exit_code=1)
                return 1
        phase("stopped")
    return 0
