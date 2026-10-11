from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dev_tools.cli import parser
from dev_tools.i18n import error_detail
from dev_tools.projects.cli import run
from dev_tools.projects.config import parse_project, render_toml
from dev_tools.projects.execution import Executor
from dev_tools.projects.models import ProjectBinding, ProjectError
from dev_tools.projects.monitoring import watch_loop
from dev_tools.projects.process_errors import process_error
from dev_tools.runtimes.platforms.windows import WindowsBackend
from tests import test_project_engine as engine_fixture


class ProcessFailureTests(unittest.TestCase):
    def test_subprocess_diagnostics_never_include_arguments_or_output(self):
        command = ["/fixture/bin/tool", "--password", "secret-value"]
        failures = (
            subprocess.TimeoutExpired(command, 15, output="secret-value", stderr="secret-value"),
            subprocess.CalledProcessError(7, command, output="secret-value", stderr="secret-value"),
            subprocess.SubprocessError("secret-value"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                error = process_error(failure, command)
                self.assertIsInstance(error, ProjectError)
                self.assertIn("tool", str(error))
                self.assertNotIn("secret-value", str(error))
                self.assertNotIn("--password", str(error))
                self.assertNotIn("secret-value", json.dumps(error_detail(error)))

    def test_native_windows_control_converts_timeouts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend = WindowsBackend(ProjectBinding("demo", "windows", root, root, root / "state"))
            with (
                patch(
                    "subprocess.run", side_effect=subprocess.TimeoutExpired(["pwsh", "secret"], 30)
                ),
                self.assertRaises(ProjectError) as failure,
            ):
                backend._native("query", root)
            self.assertIn("pwsh", str(failure.exception))
            self.assertNotIn("secret", str(failure.exception))

    def test_check_reports_a_docker_timeout_as_a_blocked_plan_without_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), "windows")
            raw = {
                "schema": 1,
                "name": "demo",
                "toolchain": "system",
                "services": {"stack": {"driver": "compose"}},
            }
            (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw))
            (engine.binding.source / "compose.yaml").write_text("services: {}")
            engine._set_spec(parse_project(raw, "windows"))
            engine.backend = WindowsBackend(engine.binding)
            output = io.StringIO()
            with (
                patch("dev_tools.projects.cli.engine_for", return_value=engine),
                patch("shutil.which", return_value="available"),
                patch(
                    "subprocess.run", side_effect=subprocess.TimeoutExpired(["docker", "info"], 15)
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    run(parser().parse_args(["check", "demo", "-e", "win", "--json"])), 1
                )
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["ready"])
            self.assertIn("timed out", payload["unresolved"][0])
            self.assertFalse(engine.binding.state.exists())

    def test_failed_task_has_a_readable_recovery_record_and_retry_restores_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), "windows")
            engine.backend.running.update(("api", "__watch"))
            engine.executor = Executor(engine.spec, engine.binding, engine.backend)
            with (
                patch(
                    "subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["fixture", "secret"], 20),
                ),
                self.assertRaises(ProjectError),
            ):
                engine.prepare()
            recovery = engine.state()["recovery"]
            self.assertIn("timed out", recovery["error"])
            self.assertNotIn("secret", recovery["error"])
            self.assertEqual(
                recovery["error_detail"]["source"], "command timed out after {seconds}s: {command}"
            )
            self.assertFalse(engine.backend.running)
            with patch("subprocess.run", return_value=subprocess.CompletedProcess(["fixture"], 0)):
                engine.prepare()
            self.assertIsNone(engine.state()["recovery"])
            self.assertEqual(engine.backend.running, {"api", "__watch"})

    def test_git_timeout_does_not_terminate_maintenance(self):
        from dev_tools.projects.monitoring import git_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), "windows")
            engine.executor = Executor(engine.spec, engine.binding, engine.backend)
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 10)):
                self.assertEqual(git_snapshot(engine.executor), {})

    def test_monitor_survives_a_transient_task_timeout_and_retries(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), "windows")
            engine.backend.running.add("__watch")
            engine.executor = Executor(engine.spec, engine.binding, engine.backend)
            clock = {"time": 0.0, "loops": 0}

            class Stop:
                def wait(self, _):
                    clock["time"] += 40.0
                    clock["loops"] += 1
                    return clock["loops"] > 2

            ready = Mock()
            output = io.StringIO()
            with (
                patch(
                    "dev_tools.projects.monitoring.time.monotonic",
                    side_effect=lambda: clock["time"],
                ),
                patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
                patch(
                    "subprocess.run",
                    side_effect=[
                        subprocess.TimeoutExpired(["fixture"], 20),
                        subprocess.CompletedProcess(["fixture"], 0),
                    ],
                ),
                contextlib.redirect_stdout(output),
            ):
                watch_loop(engine, Stop(), ready)
            ready.assert_called_once()
            self.assertIn("timed out", output.getvalue())
            self.assertIsNone(engine.state()["recovery"])
            self.assertIsNone(engine.state()["maintenance_error"])
            self.assertTrue(engine.state()["prepared_revision"])
            self.assertIn("__watch", engine.backend.running)

    @unittest.skipIf(os.name == "nt", "native WSL adapter")
    def test_wsl_adapter_converts_timeout_and_preserves_readonly_diagnostics(self):
        import pwd

        from dev_tools.runtimes.platforms.wsl import WslBackend

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = ProjectBinding(
                "demo",
                "wsl",
                root,
                root / "workspace",
                root / "state",
                pwd.getpwuid(os.geteuid()).pw_name,
                root,
            )
            backend = WslBackend(binding)
            spec = parse_project(
                {"schema": 1, "name": "demo", "services": {"stack": {"driver": "compose"}}}, "wsl"
            )
            with (
                patch("shutil.which", return_value="available"),
                patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["docker"], 15)),
            ):
                requirements = backend.requirements(spec)
                with self.assertRaises(ProjectError):
                    backend._run(["docker", "info"], timeout=15)
            self.assertIn("timed out", str(requirements.unresolved[0]))
            self.assertFalse(binding.state.exists())


if __name__ == "__main__":
    unittest.main()
