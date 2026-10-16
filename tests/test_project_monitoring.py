from __future__ import annotations

import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dev_tools.projects.config import load_project, render_toml
from dev_tools.projects.monitoring import (
    IdlePoll,
    _resume_snapshots,
    file_snapshot,
    source_snapshot,
    sync_loop,
    watch_loop,
)
from dev_tools.projects.registry import write_json
from tests import test_project_engine as engine_fixture


class NativeMonitorTests(unittest.TestCase):
    def fixture(self, environment="wsl"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        engine = engine_fixture.EngineContractTests().fixture(Path(temporary.name), environment)
        with patch("dev_tools.projects.monitoring.git_snapshot", return_value={}):
            engine.start()
        engine.backend.events.clear()
        return engine

    def clock_stop(self, iterations, on_wait=lambda _: None):
        class ClockStop:
            now = 100.0

            def __init__(self):
                self.waits = []

            def wait(self, delay):
                self.waits.append(delay)
                self.now += delay
                if len(self.waits) > iterations:
                    return True
                on_wait(len(self.waits))
                return False

        return ClockStop()

    def publish(self, engine, *, observed=100.0, **changes):
        record = {
            "schema": 1,
            "intent": engine.intent()["token"],
            "observed_at": observed,
            "git": {},
            "revision": engine.state()["prepared_revision"],
            **changes,
        }
        write_json(engine.binding.state / "source-observation.json", record)

    def test_idle_poll_is_bounded_and_returns_to_fast_polling_after_activity(self):
        poll = IdlePoll(0.75, 3)
        for expected in (1.5, 3, 3, 3):
            poll.update(False)
            self.assertEqual(poll.delay, expected)
        poll.update(True)
        self.assertEqual(poll.delay, 0.75)

    def test_sync_backs_off_without_transfers_and_reuses_the_loaded_configuration(self):
        engine = self.fixture()
        clock = self.clock_stop(8)
        with (
            patch("dev_tools.projects.monitoring.time.monotonic", side_effect=lambda: clock.now),
            patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
            patch("dev_tools.projects.monitoring.load_project", wraps=load_project) as load,
            patch.object(
                engine.backend, "sync", side_effect=[False] * 5 + [True, False, False]
            ) as sync,
        ):
            sync_loop(engine, clock)
        self.assertEqual(sync.call_count, 8)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(clock.waits, [0.75, 0.75, 1.5, 3, 3, 3, 0.75, 1.5, 3])
        self.assertFalse(
            any(event.startswith(("start:", "stop:")) for event in engine.backend.events)
        )

    def test_watch_reuses_fresh_source_checks_without_rescanning_the_windows_tree(self):
        engine = self.fixture()
        self.publish(engine)
        clock = self.clock_stop(4)
        with (
            patch("dev_tools.projects.monitoring.time.monotonic", side_effect=lambda: clock.now),
            patch("dev_tools.projects.monitoring.git_snapshot") as git,
            patch("dev_tools.projects.monitoring.fingerprint") as metadata,
        ):
            watch_loop(engine, clock)
        git.assert_not_called()
        metadata.assert_not_called()
        self.assertEqual(clock.waits, [0.5, 1.0, 1.5, 1.5, 1.5])

    def test_native_edits_recheck_git_before_building_even_with_a_fresh_idle_cache(self):
        engine = self.fixture()
        for root in (engine.binding.source, engine.binding.workspace):
            (root / "A.java").write_text("before")
        self.publish(engine)

        def edit_and_republish(iteration):
            if iteration == 1:
                for root in (engine.binding.source, engine.binding.workspace):
                    (root / "A.java").write_text("checkout in progress")
            self.publish(engine, observed=clock.now)

        clock = self.clock_stop(10, edit_and_republish)
        with (
            patch("dev_tools.projects.monitoring.time.monotonic", side_effect=lambda: clock.now),
            patch("dev_tools.projects.monitoring.git_snapshot", return_value={"busy": True}) as git,
            patch.object(engine, "rebuild") as build,
            patch.object(engine, "maintain") as maintain,
        ):
            watch_loop(engine, clock)
        self.assertGreater(git.call_count, 0)
        build.assert_not_called()
        maintain.assert_not_called()

    def test_missing_stale_invalid_and_previous_session_observations_fall_back(self):
        engine = self.fixture()
        path = engine.binding.state / "source-observation.json"
        for changes in (
            {"observed_at": 93.9},
            {"observed_at": 101},
            {"observed_at": float("nan")},
            {"intent": "previous-start"},
            {"revision": []},
            {"git": {"busy": "false"}},
        ):
            self.publish(engine, **changes)
            with (
                patch("dev_tools.projects.monitoring.time.monotonic", return_value=100),
                patch("dev_tools.projects.monitoring.git_snapshot", return_value={}) as git,
                patch(
                    "dev_tools.projects.monitoring.fingerprint", return_value="fresh"
                ) as metadata,
            ):
                self.assertEqual(source_snapshot(engine, shared=True), ({}, "fresh"))
            git.assert_called_once()
            metadata.assert_called_once()
        for content in (None, "{incomplete"):
            if content is None:
                path.unlink()
            else:
                path.write_text(content)
            with patch("dev_tools.projects.monitoring.git_snapshot", return_value={}):
                self.assertTrue(source_snapshot(engine, shared=True)[1])

    def test_maintenance_invalidates_pre_action_observations(self):
        engine = self.fixture()
        self.publish(engine)
        with (
            patch("dev_tools.projects.monitoring.time.monotonic", return_value=102),
            patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
            patch("dev_tools.projects.monitoring.fingerprint", return_value="after-lock-writeback"),
        ):
            self.assertEqual(
                source_snapshot(engine, shared=True, since=101), ({}, "after-lock-writeback")
            )

    def test_sync_publishes_dependency_changes_without_copying_an_unprepared_tree(self):
        engine = self.fixture()
        (engine.binding.source / "new-module").mkdir()
        (engine.binding.source / "new-module/package.json").write_text(
            '{"dependencies":{"new":"2"}}'
        )
        clock = self.clock_stop(1)
        with (
            patch("dev_tools.projects.monitoring.time.monotonic", side_effect=lambda: clock.now),
            patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
            patch.object(engine.backend, "sync") as sync,
        ):
            sync_loop(engine, clock)
            _, revision = source_snapshot(engine, shared=True)
        sync.assert_not_called()
        self.assertNotEqual(revision, engine.state()["prepared_revision"])

    def test_sync_blocks_git_operations_recovers_from_io_errors_and_honors_stop(self):
        engine = self.fixture()
        clock = self.clock_stop(3)
        ready = Mock()
        with (
            patch("dev_tools.projects.monitoring.time.monotonic", side_effect=lambda: clock.now),
            patch(
                "dev_tools.projects.monitoring.source_snapshot",
                side_effect=[
                    OSError("transient I/O"),
                    ({"busy": True}, engine.state()["prepared_revision"]),
                    ({}, engine.state()["prepared_revision"]),
                ],
            ),
            patch.object(engine.backend, "sync", return_value=False) as sync,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            sync_loop(engine, clock, ready)
        ready.assert_called_once()
        sync.assert_called_once()
        engine.set_intent(running=False)
        with patch("dev_tools.projects.monitoring.source_snapshot") as source:
            sync_loop(engine, self.clock_stop(1))
        source.assert_not_called()

    def test_sync_rechecks_stop_after_acquiring_the_operation_lock(self):
        engine = self.fixture()
        clock = self.clock_stop(1)

        @contextlib.contextmanager
        def cancel_before_lock(*_, **__):
            engine.set_intent(running=False)
            yield

        with (
            patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
            patch("dev_tools.runtimes.platforms.locking.operation_lock", cancel_before_lock),
            patch.object(engine.backend, "sync") as sync,
        ):
            sync_loop(engine, clock)
        sync.assert_not_called()

    def test_repeated_start_intent_does_not_terminate_an_active_sync_worker(self):
        engine = self.fixture()
        clock = self.clock_stop(2)
        changed = False

        @contextlib.contextmanager
        def new_start_before_lock(*_, **__):
            nonlocal changed
            if not changed:
                engine.set_intent(running=True)
                changed = True
            yield

        with (
            patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
            patch("dev_tools.runtimes.platforms.locking.operation_lock", new_start_before_lock),
            patch.object(engine.backend, "sync", return_value=False) as sync,
        ):
            sync_loop(engine, clock)
        sync.assert_called_once()

    def test_monitor_handover_preserves_edits_saved_before_the_new_watch_starts(self):
        for environment in ("windows", "wsl"):
            engine = self.fixture(environment)
            source = engine.binding.workspace / "A.java"
            source.write_text("before")
            baseline = {
                policy.service: file_snapshot(engine.binding.workspace, policy)
                for policy in engine.spec.builds
            }
            write_json(
                engine.binding.state / "watch-handover.json",
                {
                    "schema": 1,
                    "intent": engine.intent()["token"],
                    "revision": engine.state()["prepared_revision"],
                    "created_at": 100,
                    "snapshots": baseline,
                },
            )
            source.write_text("saved during handover")
            if environment == "wsl":
                (engine.binding.source / "A.java").write_text("saved during handover")
            clock = self.clock_stop(8)
            with (
                patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
                patch(
                    "dev_tools.projects.monitoring.time.monotonic",
                    side_effect=lambda clock=clock: clock.now,
                ),
            ):
                watch_loop(engine, clock)
            self.assertEqual(engine.backend.events.count("task:compile"), 1)
            self.assertFalse((engine.binding.state / "watch-handover.json").exists())
            self.assertFalse(any(event.startswith("stop:") for event in engine.backend.events))

    def test_handover_rejects_old_sessions_invalid_records_and_old_preparation(self):
        engine = self.fixture("windows")
        for changes in (
            {"intent": "previous-start"},
            {"revision": "previous-dependencies"},
            {"created_at": -1000},
            {"snapshots": {"api": {"A.java": ["bad", 1]}}},
        ):
            write_json(
                engine.binding.state / "watch-handover.json",
                {
                    "schema": 1,
                    "intent": engine.intent()["token"],
                    "revision": engine.state()["prepared_revision"],
                    "created_at": 100,
                    "snapshots": {"api": {}},
                    **changes,
                },
            )
            with patch("dev_tools.projects.monitoring.time.monotonic", return_value=100):
                self.assertEqual(
                    _resume_snapshots(engine, {"api": {"live.java": (1, 2)}}),
                    {"api": {"live.java": (1, 2)}},
                )

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
