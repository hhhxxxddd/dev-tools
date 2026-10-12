from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.projects.config import render_toml
from dev_tools.projects.monitoring import watch_loop
from tests import test_project_engine as engine_fixture


class NativeMonitorTests(unittest.TestCase):
    def exercise(self, environment: str, *, change_configuration=False):
        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), environment)
            source = engine.binding.source / "A.java"
            source.write_text("initial")
            clock = {"time": 0.0, "loops": 0}
            test = self

            class Stop:
                def wait(self, _):
                    clock["time"] += 2
                    clock["loops"] += 1
                    if clock["loops"] == 1:
                        source.write_text("edited source")
                        if change_configuration:
                            raw = copy.deepcopy(engine.spec.raw)
                            raw["services"]["api"]["env"] = {"MODE": "changed"}
                            (engine.binding.source / "dev-tools.toml").write_text(
                                render_toml(raw), encoding="utf-8"
                            )
                    if environment == "wsl" and not change_configuration and clock["loops"] == 3:
                        test.assertNotIn("task:compile", engine.backend.events)
                        engine.backend.sync(engine.spec)
                    return clock["loops"] > 6

            with (
                patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
                patch(
                    "dev_tools.projects.monitoring.time.monotonic",
                    side_effect=lambda: clock["time"],
                ),
            ):
                engine.start()
                engine.backend.events.clear()
                watch_loop(engine, Stop())
            self.assertTrue(engine.status()["ready"])
            self.assertEqual((engine.binding.workspace / "A.java").read_text(), "edited source")
            self.assertEqual(
                engine.backend.events.count("task:compile"), 0 if change_configuration else 1
            )

    def test_wsl_builds_after_source_changes_reach_the_native_workspace(self):
        self.exercise("wsl")

    def test_windows_still_builds_source_edits_directly(self):
        self.exercise("windows")

    def test_wsl_maintenance_refreshes_native_baseline_without_duplicate_builds(self):
        self.exercise("wsl", change_configuration=True)
