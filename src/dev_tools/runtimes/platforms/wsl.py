from __future__ import annotations

import os
import pwd
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from ...i18n import message
from ...projects.models import ProjectError, ProjectSpec
from ...projects.registry import write_json
from ...scanner import IGNORED_DIRECTORIES
from .base import Backend, PlatformRequirements

EXCLUDES = (*[name + "/" for name in sorted(IGNORED_DIRECTORIES)], ".cache*/", ".turbo/")


class WslBackend(Backend):
    python_command = "python3"
    wrapper_filename = "mvnw"
    native_shells = frozenset({"bash", "pwsh"})

    def command(self, argv: list[str]) -> list[str]:
        path = Path(argv[0])
        if path.name == "mvnw" and path.is_file() and not os.access(path, os.X_OK):
            return ["bash", *argv]
        return argv

    def child_options(self) -> dict:
        return {"start_new_session": True}

    def terminate_child(self, child: subprocess.Popen) -> None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
            if child.poll() is None:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)

    def home(self) -> Path:
        return Path(pwd.getpwnam(self.binding.run_user).pw_dir)

    def venv_python(self, root: Path) -> Path:
        return root / ".venv/bin/python"

    def environment(self) -> dict[str, str]:
        value = os.environ.copy()
        for key in (
            "MISE_DATA_DIR",
            "MISE_CACHE_DIR",
            "MISE_CONFIG_DIR",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
        ):
            value.pop(key, None)
        value.update(
            HOME=str(self.home()), USER=self.binding.run_user, LOGNAME=self.binding.run_user
        )
        return value

    def identity(
        self, argv: list[str], environment: dict[str, str]
    ) -> tuple[list[str], dict[str, str]]:
        user = pwd.getpwuid(os.geteuid()).pw_name
        if user == self.binding.run_user:
            return argv, environment
        if os.geteuid() != 0:
            raise ProjectError(
                message(
                    "run as root or configured user: {run_user}", run_user=self.binding.run_user
                )
            )
        return (
            [
                "runuser",
                "-u",
                self.binding.run_user,
                "--",
                "env",
                *[f"{key}={value}" for key, value in environment.items()],
                *argv,
            ],
            environment,
        )

    def _run(
        self,
        argv: list[str],
        *,
        project: bool = False,
        check: bool = True,
        timeout: float | None = None,
    ):
        environment = self.environment() if project else os.environ.copy()
        environment["DEBIAN_FRONTEND"] = "noninteractive"
        if project:
            argv, environment = self.identity(argv, environment)
        result = subprocess.run(
            argv,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
        if check and result.returncode:
            raise ProjectError(result.stderr.strip() or f"native command failed: {argv[0]}")
        return result

    def require_control(self) -> None:
        if os.geteuid() != 0:
            raise ProjectError(message("WSL control requires root; run this command with sudo"))

    def requirements(self, spec: ProjectSpec) -> PlatformRequirements:
        packages = [
            package
            for command, package in (
                ("git", "git"),
                ("rsync", "rsync"),
                ("runuser", "util-linux"),
            )
            if shutil.which(command) is None
        ]
        unresolved = []
        docker = any(service.driver == "compose" for service in spec.services.values())
        enable = group = False
        if docker and shutil.which("docker") is None:
            packages.extend(["docker.io", "docker-compose-v2"])
            enable, group = True, self.binding.run_user != "root"
        elif docker:
            if self._run(["docker", "compose", "version"], project=True, check=False).returncode:
                packages.append("docker-compose-v2")
            info = self._run(["docker", "info"], project=True, check=False, timeout=15)
            if info.returncode:
                group = (
                    "permission denied" in info.stderr.lower() and self.binding.run_user != "root"
                )
                if not group:
                    if (
                        self._run(["systemctl", "cat", "docker.service"], check=False).returncode
                        == 0
                    ):
                        enable = True
                    else:
                        unresolved.append(
                            message(
                                "Docker engine is unreachable; enable native WSL Docker or Docker Desktop integration"
                            )
                        )
        return PlatformRequirements(
            tuple(dict.fromkeys(packages)), enable, group, tuple(unresolved)
        )

    def install_requirements(self, requirements: PlatformRequirements) -> None:
        self.require_control()
        if requirements.packages:
            self._run(["apt-get", "update"])
            self._run(["apt-get", "install", "-y", *requirements.packages])
        if requirements.enable_docker:
            self._run(["systemctl", "enable", "--now", "docker"])
        if requirements.docker_group:
            self._run(["usermod", "-aG", "docker", self.binding.run_user])

    def ensure_workspace(self) -> None:
        workspace = self.binding.workspace
        if (
            self.binding.cache_root is None
            or self.binding.cache_root.resolve() not in workspace.resolve().parents
        ):
            raise ProjectError(message("workspace escapes native cache root"))
        if workspace.is_symlink():
            raise ProjectError(message("workspace must not be a symbolic link"))
        workspace.mkdir(parents=True, exist_ok=True)
        account = pwd.getpwnam(self.binding.run_user)
        if os.geteuid() == 0:
            os.chown(workspace, account.pw_uid, account.pw_gid)

    def sync(self, spec: ProjectSpec) -> None:
        self.ensure_workspace()
        argv = [
            "rsync",
            "-a",
            "--delete",
            "--delay-updates",
            *[f"--include={pattern}" for pattern in spec.sync_include],
            *[f"--exclude={pattern}" for pattern in (*EXCLUDES, *spec.sync_exclude)],
            str(self.binding.source) + "/",
            str(self.binding.workspace) + "/",
        ]
        self._run(argv, project=True)

    def unit(self, worker: str) -> str:
        prefix = (
            "dev-tools-test-worker"
            if os.environ.get("DEV_TOOLS_TEST_TRANSIENT") == "1"
            else "dev-tools-worker"
        )
        return f"{prefix}@{self.binding.name}:{worker}.service"

    def start(self, worker: str) -> None:
        if self.active(worker):
            return
        write_json(self.binding.state / "workers" / (worker + ".json"), {"phase": "starting"})
        if os.environ.get("DEV_TOOLS_TEST_TRANSIENT") == "1":
            if not os.environ.get("DEV_TOOLS_REGISTRY_ROOT") or not os.environ.get(
                "DEV_TOOLS_STATE_ROOT"
            ):
                raise ProjectError(
                    message("transient tests require isolated registry and state roots")
                )
            argv = [
                "systemd-run",
                f"--unit={self.unit(worker)}",
                "--collect",
                "--property=Type=simple",
                "--property=KillMode=control-group",
                "--property=Restart=no",
                f"--setenv=PYTHONPATH={Path(__file__).resolve().parents[3]}",
            ]
            for key in (
                "DEV_TOOLS_REGISTRY_ROOT",
                "DEV_TOOLS_STATE_ROOT",
                "DEV_TOOLS_CACHE_ROOT",
                "DEV_TOOLS_TEST_TRANSIENT",
            ):
                if key in os.environ:
                    argv.append(f"--setenv={key}={os.environ[key]}")
            argv.extend(
                [
                    sys.executable,
                    "-P",
                    "-m",
                    "dev_tools",
                    "--env",
                    "wsl",
                    "_worker",
                    f"{self.binding.name}:{worker}",
                ]
            )
            self._run(argv)
        else:
            self._run(["systemctl", "start", self.unit(worker)])

    def active(self, worker: str) -> bool:
        return (
            self._run(
                ["systemctl", "is-active", "--quiet", self.unit(worker)], check=False
            ).returncode
            == 0
        )

    def stop(self, worker: str) -> None:
        self._run(["systemctl", "stop", self.unit(worker)], check=False)
        if self.active(worker):
            raise ProjectError(message("could not stop native worker: {worker}", worker=worker))
        write_json(self.binding.state / "workers" / (worker + ".json"), {"phase": "stopped"})
