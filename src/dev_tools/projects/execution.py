from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..i18n import message
from ..workflows import mise_environment
from .models import CommandSpec, ProjectBinding, ProjectError, ProjectSpec, within
from .toolchain import root_runtime_versions


class Executor:
    def __init__(self, spec: ProjectSpec, binding: ProjectBinding, backend):
        self.spec, self.binding, self.backend = spec, binding, backend

    def user_environment(self, additions: dict[str, str] | None = None) -> dict[str, str]:
        environment = self.backend.environment()
        if additions:
            environment.update(additions)
        if self.spec.toolchain == "mise":
            protected = mise_environment(self.binding.source, self.binding.workspace)
            for key in (
                "MISE_GLOBAL_CONFIG_FILE",
                "MISE_AUTO_INSTALL",
                "MISE_EXEC_AUTO_INSTALL",
                "MISE_NOT_FOUND_AUTO_INSTALL",
                "MISE_NOT_FOUND_SYSTEM_FALLBACK",
                "MISE_TRUSTED_CONFIG_PATHS",
            ):
                environment[key] = protected[key]
        return environment

    def identity(
        self, argv: list[str], environment: dict[str, str]
    ) -> tuple[list[str], dict[str, str]]:
        return self.backend.identity(argv, environment)

    def resolve(
        self,
        command: CommandSpec,
        *,
        workdir: str = ".",
        options: dict[str, Any] | None = None,
        additions: dict[str, str] | None = None,
        source: bool = False,
        spring_classpath: str = "",
    ) -> tuple[list[str], Path, dict[str, str]]:
        root = self.binding.source if source else self.binding.workspace
        cwd = within(root, workdir)
        options = options or {}
        replacements = {"{python}": self.backend.python_command}
        python_root = within(root, str(options.get("python", {}).get("root", workdir)))
        replacements["{venv_python}"] = str(self.backend.venv_python(python_root))
        from .drivers import maven_runtime

        maven, maven_env = maven_runtime(
            self.binding, self.backend, workdir, options, source=source
        )
        replacements["{maven}"] = maven
        replacements["{spring_classpath}"] = spring_classpath
        if command.argv:
            argv = []
            for item in command.argv:
                for key, value in replacements.items():
                    item = item.replace(key, value)
                argv.append(item)
        else:
            argv = (
                ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command.script]
                if command.shell == "pwsh"
                else ["bash", "-c", command.script]
            )
        environment = self.user_environment({**maven_env, **(additions or {})})
        argv = self.backend.command(argv)
        if self.spec.toolchain == "mise":
            if shutil.which("mise") is None:
                raise ProjectError(message("mise is unavailable; run the dev-tools installer"))
            argv = ["mise", "exec", *root_runtime_versions(self.binding.source), "--", *argv]
        argv, environment = self.identity(argv, environment)
        return argv, cwd, environment

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        project: bool = True,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = self.user_environment(env)
        if project:
            argv, environment = self.identity(argv, environment)
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            capture_output=capture,
            **({} if capture else {"stdout": sys.stderr, "stderr": sys.stderr}),
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and result.returncode:
            raise ProjectError(
                message(
                    "command failed ({returncode}): {value}",
                    returncode=result.returncode,
                    value=argv[0],
                )
            )
        return result

    def task(self, task) -> None:
        argv, cwd, environment = self.resolve(
            task.command, workdir=task.workdir, options=task.options, additions=task.env
        )
        if not cwd.is_dir():
            raise ProjectError(message("task workdir does not exist: {cwd}", cwd=cwd))
        log_path = self.binding.state / "logs/tasks" / f"{task.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            result = subprocess.run(
                argv, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        if result.returncode:
            raise ProjectError(
                message(
                    "task {name} failed ({returncode}); log: {log_path}",
                    name=task.name,
                    returncode=result.returncode,
                    log_path=log_path,
                )
            )
