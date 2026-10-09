from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ...i18n import language, message
from ...projects.models import ProjectError, ProjectSpec
from ...projects.registry import read_json, write_json
from .base import Backend, PlatformRequirements

REPOSITORY = Path(__file__).resolve().parents[4]


class WindowsBackend(Backend):
    def _native(self, action: str, path: Path) -> dict:
        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(REPOSITORY / "scripts/dev-tools-native.ps1"),
                "-Action",
                action,
                "-RequestPath",
                str(path),
                "-Language",
                language(),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode:
            raise ProjectError(result.stderr.strip() or f"Windows native {action} failed")
        return json.loads(result.stdout)

    def _identity_path(self, worker: str) -> Path:
        return self.binding.state / "native" / (worker + ".json")

    def requirements(self, spec: ProjectSpec) -> PlatformRequirements:
        unresolved = []
        if not shutil.which("pwsh"):
            unresolved.append(message("Windows process control requires PowerShell 7"))
        if any(
            service.driver == "compose" for service in spec.services.values()
        ) and not shutil.which("docker"):
            unresolved.append(
                message("Docker is unavailable; configure a native Windows Docker engine")
            )
        elif any(service.driver == "compose" for service in spec.services.values()):
            for arguments in (["docker", "compose", "version"], ["docker", "info"]):
                result = subprocess.run(
                    arguments,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=15,
                )
                if result.returncode:
                    unresolved.append(
                        message(
                            "native Windows Docker/Compose engine is unavailable: {value}",
                            value=result.stderr.strip(),
                        )
                    )
                    break
        if spec.rebuild_on_branch and not shutil.which("git"):
            unresolved.append(message("Git is unavailable for branch monitoring"))
        return PlatformRequirements(unresolved=tuple(unresolved))

    def install_requirements(self, requirements: PlatformRequirements) -> None:
        if requirements.unresolved:
            raise ProjectError("; ".join(requirements.unresolved))

    def command(self, argv: list[str]) -> list[str]:
        executable = shutil.which(argv[0]) or argv[0]
        if Path(executable).suffix.lower() not in {".cmd", ".bat"} and argv[0] not in {
            "npm",
            "npx",
            "mvn",
            "pnpm",
            "yarn",
        }:
            return argv
        payload = base64.b64encode(json.dumps(argv).encode()).decode()
        script = f"$items = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json; & $items[0] @($items | Select-Object -Skip 1); exit $LASTEXITCODE"
        encoded = base64.b64encode(script.encode("utf-16-le")).decode()
        return ["pwsh", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]

    def terminate_child(self, child: subprocess.Popen) -> None:
        if child.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True, check=False
            )
            child.wait(timeout=10)

    def start(self, worker: str) -> None:
        if self.active(worker):
            return
        request = self.binding.state / "native" / (worker + ".launch.json")
        environment = os.environ.copy()
        environment.update(PYTHONPATH=str(REPOSITORY / "src"), PYTHONUTF8="1")
        command = [
            sys.executable,
            "-P",
            "-m",
            "dev_tools",
            "--env",
            "win",
            "_worker",
            f"{self.binding.name}:{worker}",
        ]
        write_json(self.binding.state / "workers" / (worker + ".json"), {"phase": "starting"})
        write_json(
            request,
            {
                "command_line": subprocess.list2cmdline(command),
                "cwd": str(REPOSITORY),
                "environment": environment,
            },
        )
        try:
            identity = self._native("launch", request)
            write_json(self._identity_path(worker), identity)
        finally:
            request.unlink(missing_ok=True)

    def active(self, worker: str) -> bool:
        path = self._identity_path(worker)
        return bool(read_json(path)) and bool(self._native("query", path)["active"])

    def stop(self, worker: str) -> None:
        path = self._identity_path(worker)
        if read_json(path):
            self._native("stop", path)
        write_json(self.binding.state / "workers" / (worker + ".json"), {"phase": "stopped"})
