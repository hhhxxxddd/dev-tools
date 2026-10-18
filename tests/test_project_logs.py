from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.cli import main, parser
from dev_tools.projects.models import ProjectError
from dev_tools.projects.registry import Registry
from dev_tools.schemas import CLI_SCHEMA_VERSION
from dev_tools.settings import Settings, defaults
from tests import test_project_engine as fixture


class ProjectLogsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.engine = fixture.EngineContractTests().fixture(self.root, "windows")
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "DEV_TOOLS_REGISTRY_ROOT": str(self.root / "registry"),
                    "DEV_TOOLS_STATE_ROOT": str(self.root / "states"),
                },
            )
        )
        self.registry = Registry("windows")
        self.engine.binding = self.registry.register(self.engine.binding.source)
        self.enterContext(patch("dev_tools.projects.cli.Registry", return_value=self.registry))
        self.enterContext(patch("dev_tools.projects.cli.engine_for", return_value=self.engine))
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.settings = Settings(self.root / "config.toml", "missing", defaults(), {})
        self.settings.data["language"] = "en"
        self.enterContext(patch("dev_tools.cli.load_settings", return_value=self.settings))
        self.enterContext(
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            )
        )
        self.route = self.enterContext(
            patch("dev_tools.runtimes.router.forward_remote", return_value=None)
        )

    def log(self, name, content, *, task=False):
        path = self.engine.binding.state / ("logs/tasks" if task else "logs") / (name + ".log")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def invoke(self, *arguments):
        with (
            contextlib.redirect_stdout(out := io.StringIO()),
            contextlib.redirect_stderr(err := io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(list(arguments))
        return stopped.exception.code, out.getvalue(), err.getvalue()

    def test_explicit_project_from_unrelated_directory_aggregates_declared_logs_only(self):
        self.log("api", "old\nlatest api\n")
        self.log("web", "latest web")
        self.log("__watch", "watch\n")
        self.log("stray", "never show\n")
        self.log("install", "task only\n", task=True)
        code, output, errors = self.invoke("logs", "demo", "--lines", "1", "-e", "win")
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("[api] latest api\n", output)
        self.assertIn("[web] latest web\n", output)
        self.assertIn("[__watch] watch\n", output)
        self.assertNotIn("old", output)
        self.assertNotIn("never show", output)
        self.assertNotIn("task only", output)
        self.assertEqual(self.engine.backend.events, [])

    def test_explicit_service_preserves_raw_output_and_project_is_not_inferred(self):
        self.log("api", "raw output without newline")
        for args in (("logs", "demo", "api"), ("logs", "demo", "--service", "api")):
            code, output, errors = self.invoke(*args, "-e", "win")
            self.assertEqual((code, output, errors), (0, "raw output without newline", ""))

    def test_implicit_project_uses_current_directory_for_all_or_selected_logs(self):
        self.log("api", "api\n")
        with patch("pathlib.Path.cwd", return_value=self.engine.binding.source):
            self.assertEqual(self.invoke("logs", "-e", "win")[:2], (0, "[api] api\n"))
            self.assertEqual(self.invoke("logs", "--service", "api", "-e", "win")[:2], (0, "api\n"))

    def test_task_aggregation_and_single_monitor(self):
        self.log("install", "install\n", task=True)
        self.log("compile", "compile\n", task=True)
        self.log("__sync", "sync\n")
        result = self.invoke("logs", "demo", "--task", "-e", "win")
        self.assertEqual(result[0], 0)
        self.assertIn("[compile] compile", result[1])
        self.assertIn("[install] install", result[1])
        self.assertNotIn("sync", result[1])
        self.assertEqual(self.invoke("logs", "demo", "__sync", "-e", "win")[:2], (0, "sync\n"))

    def test_missing_logs_and_unknown_services_are_distinct(self):
        code, output, errors = self.invoke("logs", "demo", "-e", "win")
        self.assertEqual((code, output.strip(), errors), (0, "No logs yet.", ""))
        code, _, errors = self.invoke("logs", "demo", "unknown", "-e", "win")
        self.assertEqual(code, 1)
        self.assertIn("unknown service", errors)

    def test_conflicting_or_invalid_service_arguments_fail_before_dispatch(self):
        for args in (
            ("logs", "demo", "api", "--service", "web"),
            ("logs", "demo", "--service", "../outside"),
            ("logs", "demo", "--service", ""),
            ("logs", "demo", ""),
        ):
            with self.subTest(args=args):
                code, _, errors = self.invoke(*args, "-e", "win")
                self.assertEqual(code, 2)
                self.assertTrue(errors)
        self.route.assert_not_called()

    def test_follow_waits_for_missing_file_and_interrupts_with_130(self):
        with patch("dev_tools.projects.engine.read_logs", side_effect=KeyboardInterrupt):
            code, output, _ = self.invoke("logs", "demo", "api", "--follow", "-e", "win")
        self.assertEqual(code, 130)
        self.assertIn("waiting", output)
        self.assertEqual(self.engine.backend.events, [])

    def test_all_project_commands_keep_one_positional_as_project_except_rename_destination(self):
        for action in (
            "status",
            "show",
            "check",
            "prepare",
            "start",
            "stop",
            "restart",
            "build",
            "unregister",
            "logs",
        ):
            with self.subTest(action=action):
                self.assertEqual(parser().parse_args([action, "demo"]).name, "demo")
        renamed = parser().parse_args(["rename", "demo", "new-name"])
        self.assertEqual((renamed.name, renamed.new_name), ("demo", "new-name"))

    def test_remote_follow_and_long_actions_have_no_short_transport_timeout(self):
        remote = "wsl" if os.name == "nt" else "win"
        self.route.return_value = 0
        for args, timeout in (
            (["logs", "demo"], 30),
            (["logs", "demo", "--follow"], None),
            (["prepare", "demo"], None),
            (["start", "demo"], None),
            (["build", "demo"], None),
            (["status", "demo"], 30),
        ):
            if args[0] == "status" and os.name == "nt":
                continue  # Windows-to-WSL status uses its dedicated structured transport.
            with self.subTest(args=args):
                code, _, _ = self.invoke(*args, "-e", remote)
                self.assertEqual(code, 0)
                self.assertEqual(self.route.call_args.args, (remote, args))
                self.assertEqual(self.route.call_args.kwargs["timeout"], timeout)

    def test_multi_source_partial_lines_do_not_run_into_other_labels(self):
        self.log("api", "")
        with (
            patch(
                "dev_tools.projects.engine.read_logs",
                return_value=iter(
                    (("api", "first"), ("web", "second\nthird\n"), ("api", "more\n"))
                ),
            ),
            contextlib.redirect_stdout(out := io.StringIO()),
        ):
            self.engine.logs()
        self.assertEqual(out.getvalue(), "[api] first\n[web] second\n[web] third\n[api] more\n")

    def test_remote_query_timeout_is_independent_of_json_output(self):
        remote = "wsl" if os.name == "nt" else "win"
        result = {
            "schema_version": CLI_SCHEMA_VERSION,
            "action": "show",
            "environment": "windows" if remote == "win" else "wsl",
        }
        self.route.return_value = subprocess.CompletedProcess([], 0, json.dumps(result), "")
        code, _, errors = self.invoke("show", "demo", "--json", "-e", remote)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(self.route.call_args.kwargs["timeout"], 30)

    def test_invalid_engine_service_never_reads_a_path(self):
        with (
            patch("dev_tools.projects.engine.read_logs") as reader,
            self.assertRaises(ProjectError),
        ):
            self.engine.logs("../outside")
        reader.assert_not_called()
