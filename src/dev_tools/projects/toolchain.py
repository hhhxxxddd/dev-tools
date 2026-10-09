from __future__ import annotations

import tomllib
from pathlib import Path

from ..i18n import message
from .models import ProjectError


def root_runtime_versions(root: Path) -> tuple[str, ...]:
    """Only explicit root declarations authorize installation or runtime selection."""
    versions = {}
    for filename in ("mise.toml", ".mise.toml"):
        path = root / filename
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            tools = tomllib.load(stream).get("tools", {})
        if not isinstance(tools, dict):
            raise ProjectError(message("root mise tools must be a table"))
        for name, definition in tools.items():
            version = definition.get("version") if isinstance(definition, dict) else definition
            if not isinstance(version, str) or not version.strip():
                raise ProjectError(
                    message("root mise tool {name} must declare one version", name=name)
                )
            if name in versions and versions[name] != version:
                raise ProjectError(
                    message("equal-priority root mise declarations conflict for: {name}", name=name)
                )
            versions[name] = version
    return tuple(f"{name}@{version}" for name, version in versions.items())
