from __future__ import annotations

import contextlib
import copy
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.cli import main
from dev_tools.controller_update import (
    UpdateSession,
    active_projects,
    deploy_wsl,
    recover_controller,
    restore_receipt,
    update_with_recovery,
)
from dev_tools.projects.config import render_toml
from dev_tools.projects.registry import read_json, write_json
from tests import test_project_engine as engine_fixture


class ControllerUpdateTests(unittest.TestCase):
    def fixture(self, environment):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        engine = engine_fixture.EngineContractTests().fixture(root, environment)
        engine.backend.running = {"api", "__watch"}
        engine.set_intent(running=True, selected=["api"])
        path = root / "controller-state/.controller-update/recovery.json"
        self.enterContext(
            patch("dev_tools.controller_update.native_environment", return_value=environment)
        )
        self.enterContext(patch("dev_tools.controller_update.recovery_path", return_value=path))
        self.enterContext(
            patch("dev_tools.controller_update.Registry.names", return_value=("demo",))
        )
        self.enterContext(patch("dev_tools.controller_update.engine_for", return_value=engine))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        return engine, path

    def restore_process(self, command, **kwargs):
        self.assertIn("_controller-restore", " ".join(map(str, command)))
        self.assertNotIn("--config", command)
        return subprocess.CompletedProcess(command, restore_receipt(self.path))

    def test_y_stops_and_restores_only_original_services_and_monitors_on_both_platforms(self):
        for environment in ("windows", "wsl"):
            with self.subTest(environment=environment):
                engine, self.path = self.fixture(environment)
                original = set(engine.backend.running)
                with (
                    patch("builtins.input", return_value="Y"),
                    patch(
                        "dev_tools.controller_update.subprocess.run",
                        side_effect=self.restore_process,
                    ),
                ):

                    def install(engine=engine):
                        self.assertFalse(engine.backend.running)
                        self.assertFalse(engine.intent()["running"])
                        return 0

                    self.assertEqual(
                        update_with_recovery(
                            UpdateSession(entry=Path("controller/scripts/dev-tools")), install
                        ),
                        0,
                    )
                self.assertEqual(engine.backend.running, original)
                self.assertNotIn("start:web", engine.backend.events)
                self.assertNotIn("start:__sync", engine.backend.events)
                self.assertEqual(read_json(self.path)["phase"], "restored")

    def test_no_or_eof_cancels_without_stopping_or_updating(self):
        engine, path = self.fixture("windows")
        for answer in ("", "N", "unexpected", EOFError()):
            with (
                self.subTest(answer=answer),
                patch(
                    "builtins.input",
                    side_effect=answer if isinstance(answer, BaseException) else None,
                    return_value=answer,
                ),
                patch("dev_tools.controller_update.subprocess.run") as process,
            ):
                with self.assertRaisesRegex(ValueError, "cancelled"):
                    update_with_recovery(
                        UpdateSession(entry=Path("entry")), lambda: self.fail("update ran")
                    )
                self.assertEqual(engine.backend.running, {"api", "__watch"})
                self.assertFalse(path.exists())
                process.assert_not_called()

    def test_failed_and_interrupted_update_attempt_restoration(self):
        engine, self.path = self.fixture("windows")
        for failure in (ValueError("install failed"), KeyboardInterrupt()):
            with (
                self.subTest(failure=failure),
                patch(
                    "dev_tools.controller_update.subprocess.run", side_effect=self.restore_process
                ),
            ):

                def action(failure=failure):
                    raise failure

                with self.assertRaises(type(failure)):
                    update_with_recovery(UpdateSession(yes=True, entry=Path("entry")), action)
                self.assertEqual(engine.backend.running, {"api", "__watch"})

    def test_failed_restore_remains_retryable_and_blocks_another_update(self):
        engine, self.path = self.fixture("windows")
        engine.backend.fail_start = "api"
        with patch("dev_tools.controller_update.subprocess.run", side_effect=self.restore_process):
            self.assertEqual(
                update_with_recovery(UpdateSession(yes=True, entry=Path("entry")), lambda: 0), 1
            )
        self.assertEqual(read_json(self.path)["phase"], "recovery_pending")
        with self.assertRaisesRegex(ValueError, "needs recovery"):
            UpdateSession(yes=True).stop()
        engine.backend.fail_start = ""
        self.assertEqual(restore_receipt(self.path), 0)
        self.assertEqual(engine.backend.running, {"api", "__watch"})

    def test_user_stop_during_update_is_respected(self):
        engine, self.path = self.fixture("windows")
        with patch("dev_tools.controller_update.subprocess.run", side_effect=self.restore_process):

            def action():
                engine.stop()
                return 0

            self.assertEqual(
                update_with_recovery(UpdateSession(yes=True, entry=Path("entry")), action), 0
            )
        self.assertFalse(engine.backend.running)
        self.assertFalse(engine.intent()["running"])

    def test_inactive_projects_do_not_prompt_or_get_started(self):
        engine, path = self.fixture("windows")
        engine.backend.running.clear()
        with (
            patch("builtins.input", side_effect=AssertionError("unexpected prompt")),
            patch("dev_tools.controller_update.subprocess.run") as process,
        ):
            self.assertEqual(active_projects(), [])
            self.assertEqual(update_with_recovery(UpdateSession(), lambda: 0), 0)
            process.assert_not_called()
        self.assertEqual(read_json(path)["phase"], "restored")

    def test_stop_during_restoration_is_not_reversed_by_retry(self):
        engine, self.path = self.fixture("windows")
        start = engine.backend.start

        def stop_before_monitor(worker):
            if worker == "api":
                engine.stop()
            start(worker)

        with (
            patch.object(engine.backend, "start", side_effect=stop_before_monitor),
            patch("dev_tools.controller_update.subprocess.run", side_effect=self.restore_process),
        ):
            self.assertEqual(
                update_with_recovery(UpdateSession(yes=True, entry=Path("entry")), lambda: 0), 1
            )
        self.assertFalse(engine.backend.running)
        self.assertFalse(engine.intent()["running"])
        self.assertEqual(restore_receipt(self.path), 0)
        self.assertFalse(engine.backend.running)

    def test_new_service_dependencies_do_not_start_previously_stopped_services(self):
        engine, self.path = self.fixture("windows")

        def change_project():
            raw = copy.deepcopy(engine.spec.raw)
            raw["services"]["extra"] = {"command": ["fixture"]}
            raw["services"]["api"]["depends_on"] = ["extra"]
            (engine.binding.source / "dev-tools.toml").write_text(
                render_toml(raw), encoding="utf-8"
            )
            return 0

        with patch("dev_tools.controller_update.subprocess.run", side_effect=self.restore_process):
            self.assertEqual(
                update_with_recovery(UpdateSession(yes=True, entry=Path("entry")), change_project),
                1,
            )
        self.assertFalse(engine.backend.running)
        self.assertEqual(read_json(self.path)["phase"], "recovery_pending")

    def test_repaired_verified_deployment_can_resume_blocked_file_recovery(self):
        engine, self.path = self.fixture("windows")
        session = UpdateSession(yes=True, entry=Path("entry"))
        session.stop()
        record = read_json(self.path)
        record["controller_ready"] = False
        write_json(self.path, record)
        controller = self.path.parent / "deployment"
        controller.mkdir()
        (controller / ".release.json").write_text("{}")
        with (
            patch.dict("os.environ", {"DEV_TOOLS_INSTALL_ROOT": str(controller)}),
            patch("dev_tools.controller.verify_source") as verify,
        ):
            self.assertEqual(recover_controller(), 0)
        verify.assert_called_once_with(controller)
        self.assertEqual(engine.backend.running, {"api", "__watch"})

    def test_malformed_recovery_records_fail_before_any_project_is_changed(self):
        engine, path = self.fixture("windows")
        session = UpdateSession(yes=True, entry=Path("entry"))
        session.stop()
        original = read_json(path)
        malformed = [
            {**original, "phase": []},
            {**original, "projects": [None]},
            {**original, "projects": [{**original["projects"][0], "workers": [1]}]},
            {**original, "projects": [{**original["projects"][0], "intent": {"selected": "api"}}]},
            {**original, "projects": [*original["projects"], *original["projects"]]},
        ]
        for record in malformed:
            with self.subTest(record=record):
                write_json(path, record)
                previous_events = list(engine.backend.events)
                with self.assertRaisesRegex(ValueError, "Invalid controller recovery record"):
                    restore_receipt(path)
                self.assertEqual(engine.backend.events, previous_events)
                self.assertEqual(read_json(path), record)

    def test_private_restoration_interrupt_returns_130_without_traceback(self):
        self.fixture("windows")
        with (
            patch("dev_tools.controller_update.restore_receipt", side_effect=KeyboardInterrupt),
            self.assertRaises(SystemExit) as interrupted,
        ):
            main(["_controller-restore"])
        self.assertEqual(interrupted.exception.code, 130)


@unittest.skipUnless(os.name != "nt" and shutil.which("rsync"), "native Linux rsync required")
class WslControllerRollbackTests(unittest.TestCase):
    def test_partial_failure_and_interruption_restore_owned_files_and_actual_units(self):
        real_run = subprocess.run
        for failure in (1, KeyboardInterrupt()):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                root, source, units = base / "installed", base / "source", base / "units"
                (root / "src/dev_tools").mkdir(parents=True)
                (root / "systemd").mkdir()
                source.mkdir()
                units.mkdir()
                version = root / "src/dev_tools/__init__.py"
                version.write_text("old controller")
                (root / ".source").write_text("old checkout")
                unit = units / "dev-tools-worker@.service"
                unit.write_text("actual previous unit")
                (root / "systemd" / unit.name).write_text("bundled previous unit")

                def partial_install(
                    command,
                    *,
                    root=root,
                    version=version,
                    unit=unit,
                    units=units,
                    failure=failure,
                    **kwargs,
                ):
                    if command[0] == "bash":
                        version.write_text("new controller")
                        (root / "src/dev_tools/new.py").write_text("new module")
                        (root / ".release.json").write_text("new receipt")
                        (root / ".source").write_text("new checkout")
                        (root / "docs").mkdir()
                        unit.write_text("new unit")
                        (units / "dev-tools-added.service").write_text("new unit")
                        if isinstance(failure, BaseException):
                            raise failure
                        return subprocess.CompletedProcess(command, failure)
                    if command[0] == "systemctl":
                        return subprocess.CompletedProcess(command, 0)
                    return real_run(command, **kwargs)

                with (
                    patch.dict(
                        os.environ,
                        {
                            "DEV_TOOLS_INSTALL_ROOT": str(root),
                            "DEV_TOOLS_UNIT_DIR": str(units),
                            "DEV_TOOLS_STATE_ROOT": str(base / "state"),
                        },
                    ),
                    patch("dev_tools.controller.verify_source"),
                    patch("dev_tools.controller_update.Registry.names", return_value=()),
                    patch(
                        "dev_tools.controller_update.subprocess.run", side_effect=partial_install
                    ),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    if isinstance(failure, BaseException):
                        with self.assertRaises(KeyboardInterrupt):
                            deploy_wsl(source, yes=True)
                    else:
                        self.assertEqual(deploy_wsl(source, yes=True), 1)
                self.assertEqual(version.read_text(), "old controller")
                self.assertEqual((root / ".source").read_text(), "old checkout")
                self.assertFalse((root / ".release.json").exists())
                self.assertFalse((root / "src/dev_tools/new.py").exists())
                self.assertFalse((root / "docs").exists())
                self.assertEqual(unit.read_text(), "actual previous unit")
                self.assertFalse((units / "dev-tools-added.service").exists())
