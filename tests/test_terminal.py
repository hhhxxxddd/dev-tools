from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.cli import main
from dev_tools.cli_errors import emit_error, request_scope
from dev_tools.command_help import LocalizedArgumentParser
from dev_tools.i18n import language_scope
from dev_tools.presentation import ANSI, colored, format_table, status_label, warning
from dev_tools.progress import progress_scope, stage
from dev_tools.projects.cli import emit
from dev_tools.projects.models import ProjectError
from tests import test_project_engine as engine_fixture


class Terminal(io.StringIO):
    def isatty(self):
        return True


class TerminalTests(unittest.TestCase):
    def setUp(self):
        environment = os.environ.copy()
        for name in ("NO_COLOR", "TERM", "DEV_TOOLS_TERMINAL"):
            environment.pop(name, None)
        self.enterContext(patch.dict(os.environ, environment, clear=True))

    def test_status_colors_and_ansi_table_keep_display_alignment(self):
        with contextlib.redirect_stdout(Terminal()), language_scope("zh"):
            values = [
                status_label(value)
                for value in ("running", "recovery-pending", "failed", "stopped")
            ]
        for value, code in zip(values, (32, 33, 31, 90), strict=True):
            self.assertTrue(value.startswith(f"\x1b[{code}m"), value)
        headers = ("名称", "状态", "详情")
        rows = [("项目", values[0], "正常"), ("demo", values[1], "等待")]
        plain = [(name, ANSI.sub("", value), detail) for name, value, detail in rows]
        self.assertEqual(ANSI.sub("", format_table(headers, rows)), format_table(headers, plain))

    def test_red_errors_yellow_notices_and_plain_redirects(self):
        with contextlib.redirect_stderr(stream := Terminal()), request_scope(["start", "demo"]):
            emit_error(ProjectError("fixture error"))
        self.assertIn("\x1b[31mdev-tools:", stream.getvalue())
        parser = LocalizedArgumentParser(prog="dev-tools")
        parser.deployment_notice = lambda: "fixture notice"
        parser.print_help(stream := Terminal())
        self.assertIn("\x1b[33mfixture notice", stream.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(status_label("running"), "运行中")
        with patch.dict(os.environ, {"NO_COLOR": ""}):
            self.assertEqual(warning("notice", stream=Terminal()), "notice")

    def test_json_has_no_ansi_even_in_a_terminal(self):
        with contextlib.redirect_stdout(stream := Terminal()):
            emit({"action": "list", "environment": "windows", "projects": []}, True)
        self.assertEqual(json.loads(stream.getvalue())["projects"], [])
        self.assertNotIn("\x1b", stream.getvalue())
        with (
            contextlib.redirect_stdout(stream := Terminal()),
            request_scope(["start", "demo", "--json"]),
        ):
            emit_error(ProjectError("fixture error"))
        self.assertEqual(json.loads(stream.getvalue())["error"]["code"], "preparation_failed")

    def test_partial_list_uses_yellow_for_unavailable_target_and_keeps_json(self):
        payload = {
            "action": "list",
            "environment": "all",
            "projects": [],
            "complete": False,
            "errors": [{"environment": "windows", "error": "target unavailable"}],
        }
        with contextlib.redirect_stdout(stream := Terminal()), language_scope("zh"):
            emit(payload, False)
        self.assertIn("\n\n原因摘要：\n  Windows [检查失败]\n", stream.getvalue())
        self.assertIn("\x1b[33m    原因：target unavailable\x1b[0m", stream.getvalue())
        self.assertNotIn("\x1b[31m", stream.getvalue())
        with contextlib.redirect_stdout(stream := Terminal()):
            emit(payload, True)
        self.assertEqual(json.loads(stream.getvalue()), payload)
        self.assertNotIn("\x1b", stream.getvalue())

    def test_transport_retains_terminal_capability_but_respects_no_color(self):
        with patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "1"}):
            self.assertIn("\x1b[31m", colored("error", "red", stream=io.StringIO()))
            with patch.dict(os.environ, {"NO_COLOR": "1"}):
                self.assertEqual(colored("error", "red", stream=Terminal()), "error")
        with patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "0"}):
            self.assertEqual(colored("error", "red", stream=Terminal()), "error")

    def test_start_reports_before_a_slow_task_and_keeps_heartbeat_language(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        engine = engine_fixture.EngineContractTests().fixture(root, "windows")
        heartbeat = threading.Event()

        class ObservedStream(io.StringIO):
            def write(self, value):
                if "still working" in value:
                    heartbeat.set()
                return super().write(value)

        stream = ObservedStream()

        def blocked_task(task):
            self.assertIn("Run dependency/build task: install", stream.getvalue())
            self.assertTrue(heartbeat.wait(2), "slow task must show progress while still running")

        engine.executor.on_task = blocked_task
        with (
            language_scope("en"),
            patch("dev_tools.progress.HEARTBEAT_SECONDS", 0.02),
            patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
            progress_scope("demo", stream=stream),
        ):
            engine.start()
        text = stream.getvalue()
        for fragment in (
            "Analyze declarations",
            "Check platform prerequisites",
            "Start service: api",
            "Health check: web",
            "Start file and dependency monitors",
        ):
            self.assertIn(fragment, text)
        self.assertIn("done (", text)
        self.assertNotIn("仍在处理", text)
        self.assertFalse(engine.state().get("recovery"))

    def test_failed_stage_reports_failure_and_background_calls_stay_silent(self):
        with (
            contextlib.redirect_stderr(stream := Terminal()),
            self.assertRaises(ProjectError),
            progress_scope("demo"),
            stage("Prepare project environment"),
        ):
            raise ProjectError("fixture")
        self.assertIn("\x1b[31m", stream.getvalue())
        with (
            contextlib.redirect_stderr(stream := io.StringIO()),
            stage("Prepare project environment"),
        ):
            pass
        self.assertEqual(stream.getvalue(), "")

    def test_closed_progress_pipe_does_not_abort_startup(self):
        class ClosedStream(io.StringIO):
            def write(self, value):
                raise BrokenPipeError("progress reader closed")

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        engine = engine_fixture.EngineContractTests().fixture(root, "windows")
        with (
            patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
            progress_scope("demo", stream=ClosedStream()),
        ):
            engine.start()
        self.assertEqual(engine.backend.running, {"api", "web", "__watch"})
        self.assertIsNone(engine.state().get("recovery"))

    def test_start_json_executes_without_progress_in_stdout(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        engine = engine_fixture.EngineContractTests().fixture(root, "windows")
        with (
            patch("dev_tools.projects.cli.Registry.resolve_name", return_value="demo"),
            patch("dev_tools.projects.cli.engine_for", return_value=engine),
            patch("dev_tools.projects.monitoring.git_snapshot", return_value={}),
            contextlib.redirect_stdout(stream := Terminal()),
            contextlib.redirect_stderr(diagnostic := Terminal()),
            self.assertRaises(SystemExit) as finished,
        ):
            main(["start", "demo", "--json"])
        self.assertEqual(finished.exception.code, 0)
        self.assertTrue(json.loads(stream.getvalue())["completed"])
        self.assertEqual(diagnostic.getvalue(), "")
