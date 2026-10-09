from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.projects.config import parse_project, render_toml
from dev_tools.projects.engine import ProjectEngine
from dev_tools.projects.models import ProjectBinding, ProjectError
from dev_tools.projects.planning import fingerprint
from dev_tools.runtimes.platforms.base import Backend, PlatformRequirements


class FakeBackend(Backend):
    def __init__(self, binding):
        super().__init__(binding)
        self.running = set()
        self.events = []
        self.unresolved = ()
        self.fail_start = ""

    def known_workers(self):
        return tuple(sorted(self.running))

    def phase(self, worker):
        return {"phase": "running" if worker in self.running else "stopped"}

    def active(self, worker):
        return worker in self.running

    def start(self, worker):
        self.events.append("start:" + worker)
        if worker == self.fail_start:
            raise ProjectError("start failure")
        self.running.add(worker)

    def stop(self, worker):
        self.events.append("stop:" + worker)
        self.running.discard(worker)

    def requirements(self, spec):
        return PlatformRequirements(unresolved=self.unresolved)

    def install_requirements(self, requirements):
        self.events.append("requirements")

    def sync(self, spec):
        self.events.append("sync")


class FakeExecutor:
    def __init__(self, backend):
        self.backend = backend
        self.binding = backend.binding
        self.fail = ""
        self.mutate = None

    def task(self, task):
        self.backend.events.append("task:" + task.name)
        if self.mutate:
            self.mutate()
        if task.name == self.fail:
            raise ProjectError("task failure")


class EngineContractTests(unittest.TestCase):
    def fixture(self, root, environment):
        raw = {
            "schema": 1,
            "name": "demo",
            "toolchain": "system",
            "rebuild_on_branch": False,
            "tasks": {
                "install": {"command": ["fixture"]},
                "compile": {"command": ["fixture"], "role": "build"},
            },
            "services": {
                "web": {"command": ["fixture"], "depends_on": ["api"]},
                "api": {"command": ["fixture"]},
            },
            "builds": {
                "api": {
                    "watch": ["."],
                    "source_task": "compile",
                    "resource_task": "compile",
                    "structural_task": "compile",
                }
            },
        }
        source = root / "source"
        source.mkdir()
        (source / "dev-tools.toml").write_text(render_toml(raw), encoding="utf-8")
        workspace = source if environment == "windows" else root / "native"
        binding = ProjectBinding("demo", environment, source, workspace, root / "state")
        backend = FakeBackend(binding)
        executor = FakeExecutor(backend)
        return ProjectEngine(parse_project(raw, environment), binding, backend, executor=executor)

    def both(self):
        for environment in ("windows", "wsl"):
            temporary = tempfile.TemporaryDirectory(prefix=environment + "-")
            self.addCleanup(temporary.cleanup)
            yield self.fixture(Path(temporary.name), environment)

    def test_prepare_start_stop_and_status_have_one_contract(self):
        for engine in self.both():
            engine.prepare()
            self.assertEqual(engine.backend.events, ["requirements", "sync", "task:install"])
            engine.backend.events.clear()
            with patch("dev_tools.projects.monitoring.git_snapshot", return_value={}):
                engine.start(("web",))
            starts = [item for item in engine.backend.events if item.startswith("start:")]
            self.assertEqual(starts[:2], ["start:api", "start:web"])
            self.assertTrue(engine.status()["ready"])
            self.assertIsNone(engine.status()["healthy"])
            engine.backend.events.clear()
            engine.stop()
            stops = [item for item in engine.backend.events if item.startswith("stop:")]
            self.assertLess(stops.index("stop:web"), stops.index("stop:api"))
            self.assertFalse(engine.status()["ready"])

    def test_failed_start_rolls_back_and_requires_recovery(self):
        for engine in self.both():
            engine.prepare()
            engine.backend.fail_start = "web"
            with (
                patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
                self.assertRaises(ProjectError),
            ):
                engine.start()
            self.assertFalse(engine.backend.running)
            self.assertEqual(engine.state()["recovery"]["operation"], "start")

    def test_unresolved_plan_does_not_stop_or_install(self):
        for engine in self.both():
            engine.backend.running.add("api")
            engine.backend.unresolved = ("missing lockfile",)
            with self.assertRaises(ProjectError):
                engine.prepare()
            self.assertEqual(engine.backend.running, {"api"})
            self.assertEqual(engine.backend.events, [])

    def test_failed_prepare_preserves_restore_set_and_retry_restores_it(self):
        for engine in self.both():
            engine.backend.running.update({"api", "web", "__watch"})
            engine.executor.fail = "install"
            with self.assertRaises(ProjectError):
                engine.prepare()
            self.assertFalse(engine.backend.running)
            self.assertEqual(
                set(engine.state()["recovery"]["restore_workers"]), {"api", "web", "__watch"}
            )
            engine.executor.fail = ""
            engine.prepare()
            self.assertTrue({"api", "web", "__watch"} <= engine.backend.running)
            self.assertIsNone(engine.state()["recovery"])

    def test_explicit_stop_cancels_pending_restore_intent(self):
        for engine in self.both():
            engine.backend.running.update({"api", "web"})
            engine.executor.fail = "install"
            with self.assertRaises(ProjectError):
                engine.prepare()
            engine.stop()
            engine.executor.fail = ""
            engine.prepare()
            self.assertFalse(engine.backend.running)

    def test_resource_build_quiesces_dependents_and_retry_restores_them(self):
        for engine in self.both():
            engine.backend.running.update({"api", "web", "__watch"})
            engine.executor.fail = "compile"
            with self.assertRaises(ProjectError):
                engine.rebuild(service="api", kind="resource")
            self.assertEqual(engine.backend.running, {"__watch"})
            self.assertEqual(engine.state()["recovery"]["restore_workers"], ["api", "web"])
            engine.executor.fail = ""
            engine.rebuild(service="api", kind="resource")
            self.assertEqual(engine.backend.running, {"api", "web", "__watch"})

    def test_source_build_keeps_services_running_and_does_not_install_runtimes(self):
        for engine in self.both():
            engine.backend.running.update({"api", "web"})
            engine.rebuild(service="api", kind="source")
            self.assertEqual(engine.backend.running, {"api", "web"})
            self.assertEqual(engine.backend.events, ["sync", "task:compile"])

    def test_changed_manifest_blocks_start_and_planned_prepare(self):
        for engine in self.both():
            engine.prepare()
            plan = engine.plan()
            (engine.binding.source / "package-lock.json").write_text("{}")
            with self.assertRaises(ProjectError):
                engine.start()
            with self.assertRaises(ProjectError):
                engine.prepare(plan)
            self.assertFalse(engine.backend.running)

    def test_task_cannot_commit_a_changed_declaration_as_prepared(self):
        for engine in self.both():
            engine.executor.mutate = lambda engine=engine: (
                engine.binding.source / "package-lock.json"
            ).write_text("{}")
            with self.assertRaises(ProjectError):
                engine.prepare()
            self.assertNotEqual(
                engine.state().get("prepared_revision"), fingerprint(engine.binding)
            )
