from __future__ import annotations

from pathlib import Path

from ..workflows import InitializationPlan, initialization_plan, write_initialization
from . import SCHEMA_VERSION
from .config import CONFIG_NAME, load_project, render_toml
from .discovery import discover_project


def initialize(
    path: str | Path,
    *,
    dry_run: bool = False,
    name: str | None = None,
    runtime: str = "auto",
    toolchain: str = "mise",
) -> tuple[dict, bool]:
    plan = initialization_plan(path)
    if plan.action == "no-tools" and toolchain == "mise":
        plan = InitializationPlan(plan.scan, "preview", "[tools]\n")
    payload = {**plan.as_dict(), "schema_version": SCHEMA_VERSION}
    root = plan.scan.root
    project = root / CONFIG_NAME
    blocked = bool(
        plan.scan.diagnostics
        or plan.scan.conflicts
        or (toolchain == "mise" and plan.scan.unresolved)
    )
    if blocked:
        payload["project_config"] = {"action": "unresolved", "path": str(project)}
        return payload, True
    if project.exists():
        load_project(root, "windows")
        load_project(root, "wsl")
        content = project.read_text(encoding="utf-8")
        project_action = "preserved"
    else:
        content = render_toml(
            discover_project(root, name=name, runtime=runtime, toolchain=toolchain)
        )
        project_action = "preview"
    if not dry_run:
        if project_action == "preview":
            try:
                with project.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(content)
                project_action = "created"
            except FileExistsError:
                load_project(root, "windows")
                load_project(root, "wsl")
                project_action = "preserved"
        if toolchain == "mise":
            plan = write_initialization(plan)
            payload.update(plan.as_dict())
    payload.update(
        schema_version=SCHEMA_VERSION,
        project_config={"action": project_action, "path": str(project), "content": content},
    )
    return payload, False
