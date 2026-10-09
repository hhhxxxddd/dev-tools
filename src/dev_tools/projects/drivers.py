from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from ..i18n import message
from .models import CommandSpec, ProjectBinding, ProjectError, ServiceSpec, within


def maven_wrapper(root: Path, workdir: str, filename: str) -> Path | None:
    root = root.resolve()
    current = within(root, workdir)
    while current == root or root in current.parents:
        wrapper = current / filename
        if wrapper.is_file():
            within(root, wrapper.relative_to(root).as_posix())
            return wrapper
        if current == root:
            break
        current = current.parent
    return None


def maven_runtime(
    binding: ProjectBinding, backend, workdir: str, options: dict[str, Any], *, source: bool = False
) -> tuple[str, dict[str, str]]:
    java = options.get("java", {})
    if not java:
        return "mvn", {}
    config = java.get("maven", {})
    mode = config.get("repository", "user")
    if mode not in {"user", "project"}:
        raise ProjectError(
            message(
                "java.maven.repository must be user or project; native paths belong in local bindings"
            )
        )
    home = backend.home()
    repository = home / (
        ".m2/repository"
        if mode == "user"
        else f".cache/dev-tools/maven/{binding.state.name}/repository"
    )
    wrapper = maven_wrapper(binding.source, workdir, backend.wrapper_filename)
    root = (binding.source if source else binding.workspace).resolve()
    executable = str(root / wrapper.relative_to(binding.source.resolve())) if wrapper else "mvn"
    option = f'-Dmaven.repo.local="{repository}"'
    return executable, {
        "MAVEN_OPTS": (os.environ.get("MAVEN_OPTS", "") + " " + option).strip(),
        "DEV_TOOLS_MAVEN_REPOSITORY": str(repository),
    }


def artifact_jar(repository: Path, coordinate: str) -> Path:
    parts = coordinate.split(":")
    if len(parts) != 3 or any(
        not re.fullmatch(r"[A-Za-z0-9_.+\-]+", item) or item in {".", ".."} for item in parts
    ):
        raise ProjectError(message("Spring DevTools requires group:artifact:version"))
    group, artifact, version = parts
    if any(not re.fullmatch(r"[A-Za-z0-9_+\-]+", item) for item in group.split(".")) or any(
        item.endswith(".") for item in (artifact, version)
    ):
        raise ProjectError(message("invalid Maven coordinate path components"))
    return (
        repository
        / Path(group.replace(".", "/"))
        / artifact
        / version
        / f"{artifact}-{version}.jar"
    )


def spring_artifact(
    binding: ProjectBinding, backend, service: ServiceSpec
) -> tuple[str, Path] | None:
    spring = service.options.get("java", {}).get("spring", {})
    coordinate = spring.get("devtools", "")
    if spring.get("unresolved"):
        raise ProjectError(message(str(spring["unresolved"])))
    if not coordinate:
        return None
    _, env = maven_runtime(binding, backend, service.workdir, service.options)
    return coordinate, artifact_jar(Path(env["DEV_TOOLS_MAVEN_REPOSITORY"]), coordinate)


def install_spring_artifact(executor, service: ServiceSpec) -> None:
    artifact = spring_artifact(executor.binding, executor.backend, service)
    if not artifact or artifact[1].is_file():
        return
    coordinate, path = artifact
    command = CommandSpec(argv=("{maven}", "-q", f"-Dartifact={coordinate}", "dependency:get"))
    argv, cwd, environment = executor.resolve(
        command, workdir=service.workdir, options=service.options
    )
    result = executor.run(argv, cwd=cwd, env=environment, project=False, check=False)
    if result.returncode or not path.is_file():
        raise ProjectError(
            message("cannot resolve Spring DevTools artifact: {coordinate}", coordinate=coordinate)
        )


def remove_owned_tree(path: Path, boundary: Path) -> None:
    target, root = path.resolve(), boundary.resolve()
    if target == root or root not in target.parents or path.is_symlink():
        raise ProjectError(message("refusing unsafe recursive deletion: {path}", path=path))
    shutil.rmtree(target)


def spring_classpath(
    binding: ProjectBinding, backend, service: ServiceSpec, *, reload: bool = False
) -> str:
    spring = service.options.get("java", {}).get("spring", {})
    artifact = spring_artifact(binding, backend, service)
    parts = []
    if artifact:
        if not artifact[1].is_file():
            raise ProjectError(
                message(
                    "Spring DevTools is missing; run dev-tools prepare {name}", name=binding.name
                )
            )
        parts.append(str(artifact[1]))
    state = binding.state / "classpath" / service.name
    if spring:
        trigger = state / "trigger"
        trigger.mkdir(parents=True, exist_ok=True)
        parts.append(str(trigger))
    for index, relative in enumerate(spring.get("classpath_modules", [])):
        source = within(binding.workspace, str(relative))
        if not source.is_dir():
            raise ProjectError(
                message("compiled module is missing; run prepare: {value}", value=source)
            )
        destination = state / str(index)
        destination.mkdir(parents=True, exist_ok=True)
        entries = spring.get("classpath_entries", [])
        wanted = {}
        for path in source.rglob("*.class"):
            relative_file = path.relative_to(source)
            if (entries and relative_file.parts[0] not in entries) or path.is_symlink():
                continue
            if source not in path.resolve().parents:
                raise ProjectError(message("compiled classpath escapes its module"))
            wanted[relative_file] = path
        for relative_file, path in wanted.items():
            output = within(destination, relative_file.as_posix())
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.is_file() and output.read_bytes() == path.read_bytes():
                continue
            temporary = output.with_name(output.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                shutil.copy2(path, temporary)
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)
        for existing in destination.rglob("*"):
            if (
                existing.is_file()
                and existing.name != "dev-tools-reload.trigger"
                and existing.relative_to(destination) not in wanted
            ):
                existing.unlink()
        parts.append(str(destination))
    if spring:
        marker = state / "trigger/dev-tools-reload.trigger"
        if reload or not marker.exists():
            marker.write_text(str(time.time_ns()), encoding="utf-8")
    return ",".join(parts)


def compose_command(binding: ProjectBinding, service: ServiceSpec, *arguments: str) -> CommandSpec:
    options = service.options.get("compose", {})
    identity = f"dev-tools-{binding.state.name}-{service.name}"
    project = (
        identity
        if len(identity) <= 63
        else identity[:54] + "-" + hashlib.sha256(identity.encode()).hexdigest()[:8]
    )
    command = ["docker", "compose", "--project-name", project]
    for filename in options.get("files", ["compose.yaml"]):
        command.extend(
            ["--file", str(within(within(binding.workspace, service.workdir), filename))]
        )
    for profile in options.get("profiles", []):
        command.extend(["--profile", str(profile)])
    command.extend(arguments)
    return CommandSpec(argv=tuple(command))


def run_compose(
    executor, service: ServiceSpec, *arguments: str, capture: bool = False, check: bool = True
):
    argv, cwd, environment = executor.resolve(
        compose_command(executor.binding, service, *arguments),
        workdir=service.workdir,
        additions=service.env,
    )
    return executor.run(argv, cwd=cwd, env=environment, project=False, capture=capture, check=check)
