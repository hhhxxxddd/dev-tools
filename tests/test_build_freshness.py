from __future__ import annotations

import contextlib
import copy
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.i18n import language_scope
from dev_tools.projects.cli import emit
from dev_tools.projects.config import parse_project, render_toml
from dev_tools.projects.models import ProjectError
from tests import test_project_engine as engine_fixture


class BuildFreshnessTests(unittest.TestCase):
    def fixture(self, environment):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return engine_fixture.EngineContractTests().fixture(Path(temporary.name), environment)

    def test_stopped_source_edit_rebuilds_before_start_even_with_same_size_and_time(self):
        for environment in ("windows", "wsl"):
            with self.subTest(environment=environment):
                engine = self.fixture(environment)
                source = engine.binding.source / "A.java"
                source.write_text("old!")
                with patch("dev_tools.projects.monitoring.git_snapshot", return_value={}):
                    engine.start()
                    engine.stop()
                    timestamp = source.stat().st_mtime_ns
                    source.write_text("new!")
                    os.utime(source, ns=(timestamp, timestamp))
                    before = engine.state_path.read_bytes()
                    plan = engine.plan()
                    self.assertEqual(plan.stale_builds, ("api",))
                    self.assertIn("compile", plan.pending_tasks)
                    self.assertEqual(plan.as_dict()["stale_builds"], ["api"])
                    self.assertEqual(engine.state_path.read_bytes(), before)
                    engine.backend.events.clear()
                    engine.start()
                self.assertLess(
                    engine.backend.events.index("task:compile"),
                    engine.backend.events.index("start:api"),
                )
                self.assertEqual(engine.plan().stale_builds, ())

    def test_check_reports_stale_builds_in_both_languages(self):
        engine = self.fixture("windows")
        plan = engine.plan().as_dict()
        for language, expected in (
            ("zh", "构建需要更新：api"),
            ("en", "Builds requiring updates: api"),
        ):
            with self.subTest(language=language), language_scope(language):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    emit(plan, False)
                self.assertIn(expected, output.getvalue())

    def test_check_reads_git_without_invoking_tools_or_writing_state(self):
        engine = self.fixture("windows")
        raw = copy.deepcopy(engine.spec.raw)
        raw["rebuild_on_branch"] = True
        (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw))
        git = engine.binding.source / ".git"
        (git / "refs" / "heads").mkdir(parents=True)
        (git / "HEAD").write_text("ref: refs/heads/main\n")
        (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n")
        with patch("subprocess.run") as execute:
            self.assertEqual(engine.plan(force=True).stale_builds, ("api",))
        execute.assert_not_called()
        self.assertFalse(engine.binding.state.exists())

    def test_legacy_state_rebuilds_once_before_start(self):
        for environment in ("windows", "wsl"):
            with self.subTest(environment=environment):
                engine = self.fixture(environment)
                engine.prepare()
                engine.save(built_inputs={})
                engine.backend.events.clear()
                with patch("dev_tools.projects.monitoring.git_snapshot", return_value={}):
                    engine.start()
                    self.assertEqual(engine.backend.events.count("task:compile"), 1)
                    engine.stop()
                    engine.backend.events.clear()
                    engine.start()
                self.assertNotIn("task:compile", engine.backend.events)

    def test_branch_change_while_stopped_triggers_structural_build(self):
        for environment in ("windows", "wsl"):
            with self.subTest(environment=environment):
                engine = self.fixture(environment)
                raw = copy.deepcopy(engine.spec.raw)
                raw["rebuild_on_branch"] = True
                (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw))
                engine.spec = parse_project(raw, environment)
                engine.executor.spec = engine.spec
                git = engine.binding.source / ".git"
                (git / "refs" / "heads").mkdir(parents=True)
                (git / "HEAD").write_text("ref: refs/heads/main\n")
                ref = git / "refs" / "heads" / "main"
                ref.write_text("a" * 40 + "\n")
                engine.start()
                engine.stop()
                ref.write_text("b" * 40 + "\n")
                engine.backend.events.clear()
                self.assertEqual(engine.plan().stale_builds, ("api",))
                engine.start()
                self.assertIn("task:compile", engine.backend.events)

    def test_failed_or_concurrently_changed_build_does_not_certify_inputs(self):
        for environment in ("windows", "wsl"):
            with self.subTest(environment=environment):
                engine = self.fixture(environment)
                source = engine.binding.source / "A.java"
                source.write_text("old")
                engine.prepare()
                baseline = copy.deepcopy(engine.state()["built_inputs"])
                source.write_text("first change")

                def edit_during_build(task, source=source):
                    if task.name == "compile":
                        source.write_text("another change")

                engine.executor.on_task = edit_during_build
                with self.assertRaisesRegex(ProjectError, "sources changed during build"):
                    engine.start()
                self.assertEqual(engine.state()["built_inputs"], baseline)
                self.assertFalse(engine.backend.running)

    def test_partial_build_does_not_certify_other_changed_files(self):
        for environment in ("windows", "wsl"):
            with self.subTest(environment=environment):
                engine = self.fixture(environment)
                source = engine.binding.source / "A.java"
                resource = engine.binding.source / "application.yml"
                source.write_text("old")
                resource.write_text("old")
                engine.prepare()
                source.write_text("new")
                resource.write_text("new")
                engine.rebuild(service="api", kind="source")
                self.assertEqual(engine.plan().stale_builds, ("api",))
                engine.rebuild(service="api", kind="structural")
                self.assertEqual(engine.plan().stale_builds, ())

    def test_partial_build_records_only_changes_it_covers(self):
        engine = self.fixture("windows")
        source = engine.binding.source / "A.java"
        source.write_text("old")
        engine.prepare()
        source.write_text("new")
        engine.rebuild(service="api", kind="source")
        self.assertEqual(engine.plan().stale_builds, ())
        source.unlink()
        engine.rebuild(service="api", kind="source")
        self.assertEqual(engine.plan().stale_builds, ("api",))

    def test_source_change_after_preparation_is_not_started_as_fresh(self):
        for environment in ("windows", "wsl"):
            with self.subTest(environment=environment):
                engine = self.fixture(environment)
                source = engine.binding.source / "A.java"
                source.write_text("old")
                engine.prepare()
                sync = engine.backend.sync

                def edit_after_sync(spec, source=source, sync=sync):
                    sync(spec)
                    source.write_text("new")

                engine.backend.events.clear()
                with (
                    patch.object(engine.backend, "sync", side_effect=edit_after_sync),
                    self.assertRaisesRegex(ProjectError, "sources changed during build"),
                ):
                    engine.start()
                self.assertNotIn("start:api", engine.backend.events)

    def test_failed_build_keeps_the_last_successful_baseline(self):
        engine = self.fixture("windows")
        source = engine.binding.source / "A.java"
        source.write_text("old")
        engine.prepare()
        baseline = copy.deepcopy(engine.state()["built_inputs"])
        source.write_text("new")
        engine.executor.fail = "compile"
        with self.assertRaisesRegex(ProjectError, "task failure"):
            engine.start()
        self.assertEqual(engine.state()["built_inputs"], baseline)
        self.assertEqual(engine.plan().stale_builds, ("api",))

    def test_only_changed_service_is_added_to_the_preparation_plan(self):
        for environment in ("windows", "wsl"):
            with self.subTest(environment=environment):
                engine = self.fixture(environment)
                raw = copy.deepcopy(engine.spec.raw)
                raw["tasks"]["compile-worker"] = {"command": ["fixture"], "role": "build"}
                raw["services"]["worker"] = {"command": ["fixture"]}
                raw["builds"]["api"]["watch"] = ["api"]
                raw["builds"]["worker"] = {
                    "watch": ["worker"],
                    "source_task": "compile-worker",
                    "resource_task": "compile-worker",
                    "structural_task": "compile-worker",
                }
                (engine.binding.source / "api").mkdir()
                (engine.binding.source / "worker").mkdir()
                source = engine.binding.source / "api" / "A.java"
                source.write_text("old")
                (engine.binding.source / "worker" / "W.java").write_text("old")
                (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw))
                engine.spec = parse_project(raw, environment)
                engine.executor.spec = engine.spec
                engine.prepare()
                source.write_text("new")
                plan = engine.plan()
                self.assertEqual(plan.stale_builds, ("api",))
                self.assertIn("compile", plan.pending_tasks)
                self.assertNotIn("compile-worker", plan.pending_tasks)
