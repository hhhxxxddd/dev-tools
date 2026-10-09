from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...projects.models import ProjectBinding, ProjectSpec
from ...projects.registry import read_json


@dataclass(frozen=True)
class PlatformRequirements:
    packages: tuple[str, ...] = ()
    enable_docker: bool = False
    docker_group: bool = False
    unresolved: tuple[str, ...] = ()


class Backend:
    python_command = "python"
    wrapper_filename = "mvnw.cmd"
    native_shells = frozenset({"pwsh"})

    def __init__(self, binding: ProjectBinding):
        self.binding = binding

    def home(self) -> Path:
        return Path.home()

    def environment(self) -> dict[str, str]:
        return os.environ.copy()

    def identity(
        self, argv: list[str], environment: dict[str, str]
    ) -> tuple[list[str], dict[str, str]]:
        return argv, environment

    def venv_python(self, root: Path) -> Path:
        return root / ".venv/Scripts/python.exe"

    def command(self, argv: list[str]) -> list[str]:
        return argv

    def child_options(self) -> dict:
        return {}

    def terminate_child(self, child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)

    def requirements(self, spec: ProjectSpec) -> PlatformRequirements:
        return PlatformRequirements()

    def install_requirements(self, requirements: PlatformRequirements) -> None:
        raise NotImplementedError

    def ensure_workspace(self) -> None:
        self.binding.workspace.mkdir(parents=True, exist_ok=True)

    def sync(self, spec: ProjectSpec) -> None:
        self.ensure_workspace()

    def require_control(self) -> None:
        pass

    def start(self, worker: str) -> None:
        raise NotImplementedError

    def stop(self, worker: str) -> None:
        raise NotImplementedError

    def active(self, worker: str) -> bool:
        raise NotImplementedError

    def known_workers(self) -> tuple[str, ...]:
        return tuple(path.stem for path in sorted((self.binding.state / "workers").glob("*.json")))

    def phase(self, worker: str) -> dict:
        return self._phase(worker, active=self.active(worker))

    def phases(self, workers: tuple[str, ...]) -> dict[str, dict]:
        return {worker: self.phase(worker) for worker in workers}

    def _phase(self, worker: str, *, active: bool) -> dict:
        record = read_json(self.binding.state / "workers" / f"{worker}.json")
        if not active:
            return {
                **record,
                "phase": "failed"
                if record.get("phase") in {"failed", "starting", "running", "restarting"}
                else "stopped",
                "error": record.get("error", "worker exited unexpectedly" if record else ""),
            }
        return record or {"phase": "starting"}
