from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.projects.cli import _list_state
from dev_tools.projects.config import parse_project, render_toml
from dev_tools.projects.lifecycle import restoration_plan
from dev_tools.projects.models import ProjectError
from dev_tools.projects.registry import read_json
from tests import test_project_engine as engine_fixture


class ServiceLifecycleTests(unittest.TestCase):
    def test_optional_only_projects_remain_listed_as_running_until_maintenance_stops(self):
        for environment in ("windows", "wsl"):
            engine = self.fixture(environment)
            raw = copy.deepcopy(engine.spec.raw)
            raw["services"] = {"client": {"command": ["fixture"], "required": False}}
            raw.pop("builds")
            self.configure(engine, raw)
            engine.start()
            self.assertEqual(_list_state(engine.status()), "running")
            engine.backend.running.discard("client")
            self.assertTrue(engine.status()["ready"])
            self.assertEqual(_list_state(engine.status()), "running")
            engine.stop()
            self.assertFalse(engine.status()["ready"])
            self.assertEqual(_list_state(engine.status()), "stopped")

    def fixture(self, environment):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        engine = engine_fixture.EngineContractTests().fixture(Path(temporary.name), environment)
        raw = copy.deepcopy(engine.spec.raw)
        raw["services"]["client"] = {
            "command": ["fixture"],
            "depends_on": ["web"],
            "required": False,
            "restart": "never",
        }
        self.configure(engine, raw)
        return engine

    def configure(self, engine, raw):
        (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw), encoding="utf-8")
        engine._set_spec(parse_project(raw, engine.binding.environment))

    def test_multiple_optional_failures_are_reported_without_project_recovery(self):
        for environment in ("windows", "wsl"):
            engine = self.fixture(environment)
            raw = copy.deepcopy(engine.spec.raw)
            raw["services"]["inspector"] = {
                "command": ["fixture"],
                "depends_on": ["api"],
                "required": False,
            }
            self.configure(engine, raw)
            original = engine.backend.start

            def start(name, original=original):
                if name in {"client", "inspector"}:
                    raise ProjectError(f"{name} failed")
                original(name)

            with (
                patch.object(engine.backend, "start", side_effect=start),
                self.assertRaisesRegex(ProjectError, "client.*inspector"),
            ):
                engine.start()
            self.assertTrue(engine.status()["ready"])
            self.assertIsNone(engine.state().get("recovery"))
            for name in ("client", "inspector"):
                worker = read_json(engine.binding.state / "workers" / f"{name}.json")
                self.assertEqual(worker["phase"], "failed")
                self.assertEqual(worker["error"], f"{name} failed")

    def test_required_failure_rolls_back_only_new_workers_and_preserves_cleanup_error(self):
        for environment in ("windows", "wsl"):
            engine = self.fixture(environment)
            engine.start(("api",))
            engine.backend.fail_start = "web"
            original = engine.backend.stop

            def stop(name, original=original):
                original(name)
                if name == "web":
                    raise ProjectError("cleanup failure")

            with (
                patch.object(engine.backend, "stop", side_effect=stop),
                self.assertRaisesRegex(ProjectError, "start failure"),
            ):
                engine.start(("web",))
            self.assertIn("api", engine.backend.running)
            self.assertIn("__watch", engine.backend.running)
            recovery = engine.state()["recovery"]
            self.assertEqual(recovery["error"], "start failure")
            self.assertIn("cleanup failure", recovery["cleanup_error"])
            self.assertEqual(recovery["restore_workers"], ["api", "web"])

    def test_monitor_failure_rolls_back_after_optional_failure(self):
        for environment in ("windows", "wsl"):
            engine = self.fixture(environment)
            original = engine.backend.start

            def start(name, original=original):
                if name in {"client", "__watch"}:
                    raise ProjectError(f"{name} failed")
                original(name)

            with (
                patch.object(engine.backend, "start", side_effect=start),
                self.assertRaisesRegex(ProjectError, "__watch failed"),
            ):
                engine.start()
            self.assertFalse(engine.backend.running)
            self.assertEqual(engine.state()["recovery"]["operation"], "start")

    def test_clean_exit_of_optional_dependency_cannot_satisfy_required_service(self):
        for environment in ("windows", "wsl"):
            engine = self.fixture(environment)
            raw = copy.deepcopy(engine.spec.raw)
            raw["services"]["client"]["depends_on"] = ["api"]
            raw["services"]["client"]["health"] = {"timeout": 0.001}
            raw["services"]["web"]["depends_on"] = ["client"]
            self.configure(engine, raw)
            original = engine.service_status

            def status(name, original=original):
                if name == "client":
                    return {"phase": "stopped", "exit_code": 0, "ready": False}
                return original(name)

            with (
                patch.object(engine, "service_status", side_effect=status),
                self.assertRaisesRegex(ProjectError, "client did not become ready"),
            ):
                engine.start(("web",))
            self.assertNotIn("start:web", engine.backend.events)
            self.assertFalse(engine.backend.running)

    def test_optional_failure_during_maintenance_keeps_shared_services_ready(self):
        for environment in ("windows", "wsl"):
            engine = self.fixture(environment)
            engine.start()
            raw = copy.deepcopy(engine.spec.raw)
            raw["services"]["api"]["env"] = {"MODE": "updated"}
            self.configure(engine, raw)
            engine.backend.fail_start = "client"
            engine.maintain()
            self.assertTrue(engine.status()["ready"])
            self.assertIsNone(engine.state().get("recovery"))
            self.assertIsNone(engine.state().get("maintenance_error"))
            self.assertEqual(
                read_json(engine.binding.state / "workers/client.json")["phase"], "failed"
            )

    def test_restore_policy_cancels_stop_and_filters_removed_or_closed_services(self):
        engine = self.fixture("windows")
        self.assertIsNone(
            restoration_plan(engine.spec, {"running": False}, ("api", "web", "client", "__watch"))
        )
        for intent, active, names, infrastructure in (
            ({}, ("web", "removed"), ("web",), False),
            ({}, ("web", "__watch"), ("web",), True),
            ({"running": True, "selected": None}, ("api", "web"), ("api", "web"), True),
            ({"running": True, "selected": ["client"]}, ("api", "web"), ("api", "web"), True),
            (
                {"running": True, "selected": ["client"]},
                ("client",),
                ("api", "web", "client"),
                True,
            ),
            ({"running": True, "selected": ["removed"]}, ("api",), (), True),
        ):
            with self.subTest(intent=intent, active=active):
                result = restoration_plan(
                    engine.spec, intent | {"token": "fixture"} if intent else intent, active
                )
                self.assertEqual(result.names, names)
                self.assertEqual(result.infrastructure, infrastructure)
                self.assertEqual(result.intent_token, "fixture" if intent else None)
