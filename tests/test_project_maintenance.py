from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.projects.config import parse_project, render_toml
from dev_tools.projects.discovery import discover_project
from dev_tools.projects.maintenance import discovery_plan, resolution_task
from dev_tools.projects.models import CommandSpec, ProjectError, TaskSpec
from tests import test_project_engine as engine_fixture


class MaintenanceTests(unittest.TestCase):
    def test_generated_modules_are_added_removed_and_custom_commands_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = {"name": "fixture", "scripts": {"dev": "fixture"}}
            (root / "package.json").write_text(json.dumps(package))
            baseline = discover_project(root, name="demo", toolchain="system")
            current = copy.deepcopy(baseline)
            service = next(iter(current["services"]))
            current["services"][service]["command"] = ["custom", "dev"]
            (root / "module").mkdir()
            (root / "module/package.json").write_text(json.dumps(package))
            spec, incoming = discovery_plan(
                parse_project(current, "wsl"), root, {"discovery_baseline": baseline}, "wsl"
            )
            self.assertEqual(spec.services[service].command.argv, ("custom", "dev"))
            self.assertEqual(len(spec.services), 2)
            (root / "module/package.json").unlink()
            updated, _ = discovery_plan(spec, root, {"discovery_baseline": incoming}, "wsl")
            self.assertEqual(tuple(updated.services), (service,))
            self.assertEqual(updated.services[service].command.argv, ("custom", "dev"))

    def test_dry_run_rediscovery_never_writes_or_executes_and_keeps_platform_override(self):
        fixture = engine_fixture.EngineContractTests()
        with tempfile.TemporaryDirectory() as temporary:
            engine = fixture.fixture(Path(temporary), "windows")
            raw = copy.deepcopy(engine.spec.raw)
            raw["discovery"] = "auto"
            raw["services"]["api"]["platforms"] = {"windows": {"command": ["windows-only"]}}
            path = engine.binding.source / "dev-tools.toml"
            path.write_text(render_toml(raw))
            before = path.read_bytes()
            with patch("subprocess.run") as execute:
                plan = engine.plan()
            execute.assert_not_called()
            self.assertEqual(plan.spec.services["api"].command.argv, ("windows-only",))
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(engine.binding.state.exists())

    def test_custom_install_arguments_are_not_replaced_by_a_resolver(self):
        task = TaskSpec(
            "install", CommandSpec(argv=("npm", "install", "specific-package")), manager="npm"
        )
        self.assertIsNone(resolution_task(task))

    def test_locked_mode_requires_a_lock_without_resolving_it(self):
        fixture = engine_fixture.EngineContractTests()
        with tempfile.TemporaryDirectory() as temporary:
            engine = fixture.fixture(Path(temporary), "windows")
            raw = copy.deepcopy(engine.spec.raw)
            raw["dependency_mode"] = "locked"
            raw["tasks"]["install"] = {"command": ["npm", "ci"], "manager": "npm"}
            (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw))
            plan = engine.plan()
            self.assertFalse(plan.resolution_tasks)
            self.assertTrue(any("lockfile" in str(item) for item in plan.unresolved))

    def test_invalid_modes_are_configuration_errors(self):
        for field, value in (
            ("dependency_mode", "latest"),
            ("dependency_mode", []),
            ("discovery", {}),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ProjectError):
                parse_project({"schema": 1, "name": "demo", field: value}, "windows")
