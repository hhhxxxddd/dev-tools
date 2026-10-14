from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.cli import main
from dev_tools.projects.cli import project_columns, start_all
from dev_tools.projects.models import ProjectError
from tests import test_project_engine as engine_fixture


class ProjectBulkTests(unittest.TestCase):
    def fixture(self, root):
        return engine_fixture.EngineContractTests().fixture(root, "windows")

    def test_batch_skips_running_and_continues_after_one_project_fails(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        engines = []
        for name in ("running", "broken", "stopped"):
            child = root / name
            child.mkdir()
            engines.append(self.fixture(child))
        engines[0].backend.running.add("api")
        engines[1].backend.unresolved = ("fixture failure",)
        with (
            patch(
                "dev_tools.projects.cli.Registry.names",
                return_value=("running", "broken", "stopped"),
            ),
            patch("dev_tools.projects.cli.engine_for", side_effect=engines),
            contextlib.redirect_stdout(output := io.StringIO()),
        ):
            self.assertEqual(start_all("windows", True), 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(
            [item["status"] for item in payload["projects"]], ["skipped", "failed", "started"]
        )
        self.assertFalse(payload["complete"])
        self.assertEqual(engines[0].backend.events, [])
        self.assertIn("web", engines[2].backend.running)

    def test_list_defaults_to_all_and_explicit_environment_limits_query(self):
        for arguments, expected in (
            (["list", "--json"], ["windows", "wsl"]),
            (["list", "--all", "--json"], ["windows", "wsl"]),
            (["list", "-e", "wsl", "--json"], ["wsl"]),
            (["list", "-e", "win", "--json"], ["windows"]),
        ):
            with (
                self.subTest(arguments=arguments),
                patch("dev_tools.projects.cli.list_projects", return_value=[]) as collect,
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as stopped,
            ):
                main(arguments)
            self.assertEqual(stopped.exception.code, 0)
            self.assertEqual([call.args[0] for call in collect.call_args_list], expected)

    def test_batch_rejects_project_or_service_arguments(self):
        for arguments in (["start", "demo", "--all"], ["start", "--all", "--service", "api"]):
            with (
                self.subTest(arguments=arguments),
                patch(
                    "dev_tools.projects.cli.start_all", side_effect=AssertionError("must not start")
                ),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as stopped,
            ):
                main(arguments)
            self.assertEqual(stopped.exception.code, 2)

    def test_columns_read_declared_ports_and_mixed_runtime_types_without_executing(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        engine = self.fixture(root)
        (engine.binding.source / "mise.toml").write_text(
            '[tools]\npython="3.14.8"\njava="21"\nnode="22"\n', encoding="utf-8"
        )
        engine.spec.services["api"].health.update(tcp=8080, http="http://localhost:8080/health")
        engine.spec.services["web"].health.update(http="http://localhost:5173/")
        with patch("subprocess.run", side_effect=AssertionError("columns must not execute tools")):
            self.assertEqual(
                project_columns(engine),
                {"ports": [5173, 8080], "types": ["java", "node", "python"]},
            )

    def test_unknown_columns_are_empty_and_conflicting_mise_is_reported(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        engine = self.fixture(root)
        self.assertEqual(project_columns(engine), {"ports": [], "types": []})
        (engine.binding.source / "mise.toml").write_text(
            '[tools]\npython="3.14.8"\n', encoding="utf-8"
        )
        (engine.binding.source / ".mise.toml").write_text(
            '[tools]\npython="3.13"\n', encoding="utf-8"
        )
        with self.assertRaises(ProjectError):
            project_columns(engine)
