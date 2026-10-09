from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..i18n import message
from ..runtimes.platforms.layout import storage, workspace
from . import SCHEMA_VERSION
from .config import load_project
from .models import ProjectBinding, ProjectError, validate_name


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProjectError(message("expected a JSON object in {path}", path=path))
    return value


class Registry:
    def __init__(self, environment: str):
        self.environment = environment
        self.root, self.state_root = storage(environment)

    def path(self, name: str) -> Path:
        return self.root / (validate_name(name) + ".json")

    def names(self) -> tuple[str, ...]:
        return tuple(path.stem for path in sorted(self.root.glob("*.json")))

    def resolve_name(self, name: str | None, *, cwd: Path | None = None) -> str:
        if name is not None:
            return validate_name(name)
        current = (cwd or Path.cwd()).resolve()
        matches = []
        for alias in self.names():
            binding = self.load(alias)
            if any(
                current == root or root in current.parents
                for root in (binding.source, binding.workspace)
            ):
                matches.append(alias)
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ProjectError(
                message(
                    "当前目录匹配多个项目：{matches}；请显式指定项目名", matches=", ".join(matches)
                )
            )
        raise ProjectError(message("当前目录未匹配到已注册项目；请显式指定项目名"))

    def register(
        self, source: Path, *, name: str | None = None, run_user: str = "", force: bool = False
    ) -> ProjectBinding:
        source = source.expanduser().resolve(strict=True)
        spec = load_project(source, self.environment)
        alias = validate_name(name or spec.name)
        for existing in self.names():
            if existing != alias and self.load(existing).source == source:
                raise ProjectError(
                    message("source is already registered as: {existing}", existing=existing)
                )
        identity = alias + "-" + uuid.uuid4().hex[:8]
        native_workspace, run_user, cache_root = workspace(
            self.environment, source, identity, run_user
        )
        binding = ProjectBinding(
            alias,
            self.environment,
            source,
            native_workspace,
            self.state_root / identity,
            run_user,
            cache_root,
        )
        if self.path(alias).exists():
            current = self.load(alias)
            if not force or current.source != source or current.run_user != run_user:
                raise ProjectError(
                    message("project is already registered: {new_name}", new_name=alias)
                )
            return current
        write_json(self.path(alias), {"schema_version": SCHEMA_VERSION, **binding.as_dict()})
        return binding

    def load(self, name: str) -> ProjectBinding:
        raw = read_json(self.path(name))
        if not raw:
            raise ProjectError(message("project is not registered: {name}", name=name))
        if (
            raw.get("schema_version") != SCHEMA_VERSION
            or raw.get("environment") != self.environment
            or raw.get("name") != name
        ):
            raise ProjectError(message("invalid native binding for: {name}", name=name))
        if any(
            not isinstance(raw.get(key), str) or not Path(raw[key]).is_absolute()
            for key in ("source", "workspace", "state")
        ):
            raise ProjectError(message("native binding paths must be absolute"))
        source, workspace, state = [
            Path(raw[key]).resolve() for key in ("source", "workspace", "state")
        ]
        if self.state_root not in state.parents:
            raise ProjectError(message("binding state escapes native storage"))
        cache_root = Path(raw["cache_root"]).resolve() if raw.get("cache_root") else None
        if self.environment == "wsl":
            if cache_root is None or cache_root not in workspace.parents:
                raise ProjectError(message("binding workspace escapes native cache root"))
            if source == workspace or source in workspace.parents or workspace in source.parents:
                raise ProjectError(message("source and workspace overlap"))
        elif source != workspace:
            raise ProjectError(message("Windows workspace must be the source directory"))
        return ProjectBinding(
            name,
            self.environment,
            source,
            workspace,
            state,
            str(raw.get("run_user", "")),
            cache_root,
        )

    def rename(self, old: str, new: str) -> ProjectBinding:
        binding = self.load(old)
        if self.path(new).exists():
            raise ProjectError(message("project is already registered: {new_name}", new_name=new))
        updated = replace(binding, name=validate_name(new))
        write_json(self.path(new), {"schema_version": SCHEMA_VERSION, **updated.as_dict()})
        self.path(old).unlink()
        return updated
