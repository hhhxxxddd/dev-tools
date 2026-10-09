from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project_models import ScanResult
from .scanner import render_mise, scan_project

PROJECT_ISOLATION_CONFIG = Path(__file__).resolve().parents[2] / "config/project-isolation.toml"


def mise_environment(*roots: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "MISE_GLOBAL_CONFIG_FILE": str(PROJECT_ISOLATION_CONFIG),
            "MISE_AUTO_INSTALL": "false",
            "MISE_EXEC_AUTO_INSTALL": "false",
            "MISE_NOT_FOUND_AUTO_INSTALL": "false",
            "MISE_NOT_FOUND_SYSTEM_FALLBACK": "false",
            "MISE_TRUSTED_CONFIG_PATHS": os.pathsep.join(map(str, roots)),
        }
    )
    return environment


@dataclass(frozen=True)
class InitializationPlan:
    scan: ScanResult
    action: str
    content: str | None

    @property
    def blocked(self) -> bool:
        return self.scan.blocked and self.action != "preserved"

    def as_dict(self) -> dict[str, Any]:
        value = self.scan.as_dict()
        value["action"] = self.action
        if self.content is not None:
            value["content"] = self.content
        return value


def initialization_plan(path: str | Path) -> InitializationPlan:
    result = scan_project(path)
    if result.existing_config:
        return InitializationPlan(
            result, "preserved", result.existing_config.read_text(encoding="utf-8")
        )
    if result.conflicts:
        return InitializationPlan(result, "conflict", None)
    if result.unresolved or result.diagnostics:
        return InitializationPlan(result, "unresolved", None)
    if not result.tools:
        return InitializationPlan(result, "no-tools", None)
    return InitializationPlan(result, "preview", render_mise(result))


def write_initialization(plan: InitializationPlan) -> InitializationPlan:
    if plan.action != "preview":
        return plan
    # Recheck both root names before writing, and never overwrite a racing writer.
    existing = next(
        (
            plan.scan.root / name
            for name in ("mise.toml", ".mise.toml")
            if (plan.scan.root / name).is_file()
        ),
        None,
    )
    if existing:
        return initialization_plan(plan.scan.root)
    try:
        with (plan.scan.root / "mise.toml").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(plan.content or "")
    except FileExistsError:
        return initialization_plan(plan.scan.root)
    return InitializationPlan(plan.scan, "created", plan.content)
