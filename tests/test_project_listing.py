from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.cli import main, parser
from dev_tools.i18n import language_scope
from dev_tools.projects.cli import list_projects, run
from dev_tools.projects.models import ProjectError


def project(environment):
    return {
        "name": "same-name",
        "environment": environment,
        "ready": True,
        "services": {"web": {"phase": "running", "health": "healthy"}},
    }


class ProjectListingTests(unittest.TestCase):
    def invoke(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(arguments)
        return stopped.exception.code, output.getvalue(), errors.getvalue()

    def test_all_queries_both_environments_and_keeps_identical_names(self):
        for language in ("zh", "en"):
            for as_json in (False, True):
                with self.subTest(language=language, as_json=as_json):
                    settings = SimpleNamespace(language=language, errors=(), distro="Configured")
                    projects = [project(environment) for environment in ("windows", "wsl")]
                    with (
                        patch("dev_tools.cli.load_settings", return_value=settings),
                        patch("dev_tools.runtimes.router.forward_remote") as transport,
                        patch(
                            "dev_tools.projects.cli.list_projects",
                            side_effect=lambda environment, settings: [project(environment)],
                        ) as collect,
                    ):
                        code, output, errors = self.invoke(
                            ["list", "--all", *(["--json"] if as_json else [])]
                        )
                    self.assertEqual(code, 0, errors)
                    self.assertEqual(
                        [call.args for call in collect.call_args_list],
                        [("windows", settings), ("wsl", settings)],
                    )
                    transport.assert_not_called()
                    if as_json:
                        payload = json.loads(output)
                        self.assertEqual(payload["environment"], "all")
                        self.assertEqual(payload["projects"], projects)
                        self.assertTrue(payload["complete"])
                        self.assertEqual(payload["errors"], [])
                    else:
                        self.assertEqual(output.count("same-name"), 2)
                        self.assertIn("Windows", output)
                        self.assertIn("WSL", output)
                        self.assertIn("状态" if language == "zh" else "State", output)
                        self.assertIn(
                            "全部环境" if language == "zh" else "All environments", output
                        )

    def test_all_preserves_results_and_reports_unavailable_environment(self):
        for failed in ("windows", "wsl"):
            for as_json in (False, True):
                with self.subTest(failed=failed, as_json=as_json):

                    def collect(environment, settings, failed=failed):
                        if environment == failed:
                            raise OSError("control unavailable")
                        return [project(environment)]

                    output = io.StringIO()
                    with (
                        patch("dev_tools.projects.cli.list_projects", side_effect=collect),
                        contextlib.redirect_stdout(output),
                    ):
                        code = run(
                            parser().parse_args(["list", "--all", *(["--json"] if as_json else [])])
                        )
                    self.assertEqual(code, 1)
                    if as_json:
                        payload = json.loads(output.getvalue())
                        self.assertFalse(payload["complete"])
                        self.assertEqual(len(payload["projects"]), 1)
                        self.assertNotEqual(payload["projects"][0]["environment"], failed)
                        self.assertEqual(payload["errors"][0]["environment"], failed)
                    else:
                        self.assertIn("same-name", output.getvalue())
                        self.assertIn("control unavailable", output.getvalue())

    def test_all_does_not_report_failed_queries_as_empty_registries(self):
        with (
            patch("dev_tools.projects.cli.list_projects", side_effect=OSError("unavailable")),
            contextlib.redirect_stdout(output := io.StringIO()),
            language_scope("en"),
        ):
            self.assertEqual(run(parser().parse_args(["list", "--all"])), 1)
        self.assertNotIn("No registered projects", output.getvalue())
        self.assertEqual(output.getvalue().count("unavailable"), 2)

    def test_all_rejects_explicit_environment_before_querying(self):
        for environment in ("win", "wsl"):
            for arguments in (
                ["-e", environment, "list", "--all"],
                ["list", "--all", "--env=" + environment],
            ):
                with (
                    self.subTest(arguments=arguments),
                    patch("dev_tools.projects.cli.list_projects") as collect,
                ):
                    code, _, errors = self.invoke(arguments)
                self.assertEqual(code, 2)
                self.assertIn("--all", errors)
                collect.assert_not_called()

    def test_remote_list_is_rendered_locally_from_json(self):
        environment = "wsl" if os.name == "nt" else "windows"
        target = "win" if environment == "windows" else environment
        payload = {
            "schema_version": 3,
            "action": "list",
            "environment": environment,
            "projects": [project(environment)],
        }
        settings = SimpleNamespace(language="en", errors=(), distro="Configured")
        with (
            patch("dev_tools.cli.load_settings", return_value=settings),
            patch(
                "dev_tools.runtimes.router.forward_remote",
                return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
            ) as remote,
        ):
            code, output, errors = self.invoke(["list", "-e", target])
        self.assertEqual(code, 0, errors)
        self.assertIn("Name", output)
        self.assertIn("same-name", output)
        self.assertIn("Running", output)
        remote.assert_called_once_with(target, ["list", "--json"], settings=settings, capture=True)

    def test_invalid_remote_responses_and_timeouts_are_controlled_errors(self):
        environment = "wsl" if os.name == "nt" else "windows"
        valid = {"schema_version": 3, "action": "list", "environment": environment, "projects": []}
        responses = [
            subprocess.CompletedProcess([], 1, "", "remote failure"),
            subprocess.CompletedProcess([], 0, "invalid json", ""),
            subprocess.CompletedProcess([], 0, json.dumps({**valid, "projects": {}}), ""),
            subprocess.CompletedProcess([], 0, json.dumps({**valid, "environment": "all"}), ""),
            subprocess.CompletedProcess([], 0, json.dumps({**valid, "schema_version": 999}), ""),
            subprocess.CompletedProcess([], 0, json.dumps({**valid, "projects": [None]}), ""),
            subprocess.TimeoutExpired("list", 30),
        ]
        for response in responses:
            with (
                self.subTest(response=response),
                patch("dev_tools.runtimes.router.forward_remote", side_effect=[response]),
                self.assertRaises(ProjectError),
            ):
                list_projects(environment)
