from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.cli import main, parser
from dev_tools.cli_errors import JSON_COMMANDS
from dev_tools.i18n import message
from dev_tools.projects.models import ProjectError
from dev_tools.projects.process_errors import process_error
from dev_tools.schemas import (
    BINDING_SCHEMA_VERSION,
    CLI_SCHEMA_VERSION,
    PROJECT_CONFIG_SCHEMA_VERSION,
)
from dev_tools.settings import Settings, SettingsError, defaults
from tests import test_project_engine as engine_fixture


class CliContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(self.root / "config.toml", "missing", defaults(), {})

    def invoke(self, *arguments, settings_error=None, remote=None):
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch(
                "dev_tools.cli.load_settings",
                return_value=self.settings,
                side_effect=settings_error,
            ),
            patch("dev_tools.runtimes.router.forward_remote", return_value=remote) as transport,
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(list(arguments))
        self.remote_call = transport.call_args
        return stopped.exception.code, output.getvalue(), errors.getvalue()

    def assert_error(self, result, code, *, status=1, action=None, details=None):
        exit_status, output, errors = result
        self.assertEqual(exit_status, status, output + errors)
        value = json.loads(output)  # Also rejects multiple documents or text prefixes.
        self.assertEqual(set(value), {"schema_version", "action", "error"})
        self.assertEqual(value["schema_version"], CLI_SCHEMA_VERSION)
        self.assertEqual(value["action"], action)
        self.assertEqual(set(value["error"]), {"code", "message", "details"})
        self.assertEqual(value["error"]["code"], code)
        self.assertIsInstance(value["error"]["message"], str)
        self.assertEqual(value["error"]["details"], details or {})
        self.assertNotIn("SECRET", output + errors)
        self.assertNotIn("Traceback", output + errors)
        self.assertEqual(errors, "")
        return value

    def test_json_option_catalog_and_independent_format_versions(self):
        command_parser = parser()
        commands = command_parser._subparsers._group_actions[0].choices
        supported = {
            name
            for name, command in commands.items()
            if any("--json" in argument.option_strings for argument in command._actions)
        }
        self.assertEqual(supported, JSON_COMMANDS)
        self.assertEqual(CLI_SCHEMA_VERSION, 3)
        self.assertEqual(BINDING_SCHEMA_VERSION, 3)
        self.assertEqual(PROJECT_CONFIG_SCHEMA_VERSION, 1)

    def test_status_query_succeeds_when_services_are_not_ready(self):
        engine = engine_fixture.EngineContractTests().fixture(self.root, "windows")
        with patch("dev_tools.projects.cli.engine_for", return_value=engine):
            code, output, errors = self.invoke("status", "demo", "-e", "win", "--json")
        self.assertEqual(code, 0, errors)
        value = json.loads(output)
        self.assertEqual(
            set(value),
            {
                "schema_version",
                "action",
                "name",
                "environment",
                "ready",
                "healthy",
                "services",
                "workers",
                "recovery",
                "prepared",
                "maintenance_error",
            },
        )
        self.assertEqual(value["schema_version"], CLI_SCHEMA_VERSION)
        self.assertEqual(value["action"], "status")
        self.assertEqual(value["environment"], "windows")
        self.assertFalse(value["ready"])
        self.assertFalse(value["prepared"])
        self.assertIsNone(value["healthy"])
        self.assertEqual(value["services"]["api"]["phase"], "stopped")
        self.assertEqual(value["services"]["api"]["health"], "unknown")
        self.assertNotIn("error", value)

    def test_scan_init_and_check_blocking_results_exit_one(self):
        project = self.root / "scan-source"
        project.mkdir()
        (project / "package.json").write_text('{"engines":{"node":"<20"}}', encoding="utf-8")
        for action in ("scan", "init"):
            with self.subTest(action=action):
                code, output, errors = self.invoke(action, str(project), "--json")
                self.assertEqual(code, 1, errors)
                value = json.loads(output)
                self.assertEqual(value["schema_version"], CLI_SCHEMA_VERSION)
                self.assertTrue(value["unresolved"])
                self.assertNotIn("error", value)
        engine = engine_fixture.EngineContractTests().fixture(self.root, "windows")
        engine.backend.unresolved = (message("fixture prerequisite is missing"),)
        with patch("dev_tools.projects.cli.engine_for", return_value=engine):
            code, output, errors = self.invoke("check", "demo", "-e", "win", "--json")
        self.assertEqual(code, 1, errors)
        value = json.loads(output)
        self.assertEqual(value["action"], "check")
        self.assertFalse(value["ready"])
        self.assertIsInstance(value["unresolved"], list)

    def test_partial_list_preserves_results_with_exit_one(self):
        project = {"name": "demo", "environment": "windows", "ready": False}
        with patch(
            "dev_tools.projects.cli.list_projects",
            side_effect=[[project], OSError("unavailable")],
        ):
            code, output, errors = self.invoke("list", "--all", "--json")
        self.assertEqual(code, 1, errors)
        value = json.loads(output)
        self.assertEqual(value["schema_version"], CLI_SCHEMA_VERSION)
        self.assertEqual(value["action"], "list")
        self.assertEqual(value["environment"], "all")
        self.assertFalse(value["complete"])
        self.assertEqual(value["projects"], [project])
        self.assertEqual(value["errors"][0]["environment"], "wsl")

    def test_unknown_project_has_a_safe_localized_error_document(self):
        values = []
        for language in ("zh", "en"):
            self.settings.data["language"] = language
            with patch(
                "dev_tools.projects.cli.engine_for",
                side_effect=ProjectError(message("project is not registered: {name}", name="demo")),
            ):
                value = self.assert_error(
                    self.invoke("status", "demo", "--json"),
                    "project_not_registered",
                    action="status",
                )
            values.append(value)
        self.assertNotEqual(values[0]["error"]["message"], values[1]["error"]["message"])
        for value in values:
            value["error"].pop("message")
        self.assertEqual(values[0], values[1])

    def test_invalid_config_before_parsing_is_still_a_json_error(self):
        for arguments in (
            ("--config", "fixture.toml", "sysinfo", "--json"),
            ("sysinfo", "--json", "--config=fixture.toml"),
        ):
            with self.subTest(arguments=arguments):
                self.assert_error(
                    self.invoke(*arguments, settings_error=SettingsError("SECRET")),
                    "invalid_config",
                    action="sysinfo",
                )

    def test_malformed_binding_cache_root_has_a_safe_error_document(self):
        registry = self.root / "registry"
        registry.mkdir()
        state = self.root / "state"
        for value in (1, {"SECRET": "credential"}):
            with self.subTest(value=value):
                (registry / "demo.json").write_text(
                    json.dumps(
                        {
                            "schema_version": BINDING_SCHEMA_VERSION,
                            "name": "demo",
                            "environment": "windows",
                            "source": str(self.root.resolve()),
                            "workspace": str(self.root.resolve()),
                            "state": str((state / "demo-id").resolve()),
                            "cache_root": value,
                        }
                    ),
                    encoding="utf-8",
                )
                with patch("dev_tools.projects.registry.storage", return_value=(registry, state)):
                    self.assert_error(
                        self.invoke("status", "demo", "-e", "win", "--json"),
                        "operation_failed",
                        action="status",
                    )

    def test_invalid_resolved_config_preserves_config_diagnostics_and_error_code(self):
        broken = Settings(
            self.settings.path,
            "invalid",
            defaults(),
            {},
            (message("invalid setting: {field}", field="language"),),
        )
        with patch("dev_tools.cli.load_settings", return_value=broken):
            output, errors = io.StringIO(), io.StringIO()
            with (
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
                self.assertRaises(SystemExit) as stopped,
            ):
                main(["config", "check", "--json"])
        self.assertEqual(stopped.exception.code, 1)
        value = json.loads(output.getvalue())
        self.assertEqual(value["state"], "invalid")
        self.assertTrue(value["errors"])
        self.assertEqual(value["error"]["code"], "invalid_config")
        self.assertEqual(value["error"]["details"], {})
        self.settings = broken
        self.assert_error(self.invoke("sysinfo", "--json"), "invalid_config", action="sysinfo")

    def test_timeout_and_process_failures_do_not_expose_arguments_or_output(self):
        command = ["fixture", "--password", "SECRET"]
        timeout = subprocess.TimeoutExpired(command, 15, output="SECRET", stderr="SECRET")
        failures = (
            (timeout, "timeout", {"timeout_seconds": 15}),
            (process_error(timeout, command), "timeout", {"timeout_seconds": 15}),
            (
                subprocess.CalledProcessError(7, command, output="SECRET", stderr="SECRET"),
                "operation_failed",
                {"returncode": 7},
            ),
            (subprocess.SubprocessError("SECRET"), "operation_failed", {}),
            (OSError("SECRET"), "operation_failed", {}),
        )
        for error, code, details in failures:
            with (
                self.subTest(error=type(error).__name__),
                patch("dev_tools.projects.cli.engine_for", side_effect=error),
            ):
                self.assert_error(
                    self.invoke("status", "demo", "--json"),
                    code,
                    action="status",
                    details=details,
                )

    def test_prepare_failure_has_one_error_document_and_no_success_result(self):
        engine = engine_fixture.EngineContractTests().fixture(self.root, "windows")
        with (
            patch("dev_tools.projects.cli.engine_for", return_value=engine),
            patch.object(engine, "prepare", side_effect=ProjectError("SECRET task arguments")),
        ):
            self.assert_error(
                self.invoke("prepare", "demo", "--json"),
                "preparation_failed",
                action="prepare",
            )

    def test_usage_errors_exit_two_and_never_echo_secret_arguments_in_json(self):
        for arguments, action in (
            (("unknown-command", "--json", "--password", "SECRET"), None),
            (("status", "demo", "--json", "--password", "SECRET"), "status"),
            (("status", "demo", "--json", "-e", "unsupported"), "status"),
            (("status", "SECRET invalid name", "--json"), "status"),
            (("config", "edit", "--json"), "config"),
            (("self", "update", "--json"), "self"),
            (("status", "demo", "--json", "--config"), "status"),
        ):
            with self.subTest(arguments=arguments):
                self.assert_error(
                    self.invoke(*arguments),
                    "invalid_arguments",
                    status=2,
                    action=action,
                )

    def test_interrupt_exits_130_and_has_a_json_document(self):
        with patch("dev_tools.projects.cli.engine_for", side_effect=KeyboardInterrupt):
            self.assert_error(
                self.invoke("status", "demo", "--json"),
                "interrupted",
                status=130,
                action="status",
            )

    def test_downstream_json_flags_do_not_enable_controller_json_errors(self):
        for arguments in (
            ("logs", "demo", "api", "--json"),
            ("logs", "demo", "api", "--", "--json"),
            ("compose", "--", "--json"),
            ("status", "demo", "--", "--json"),
        ):
            with self.subTest(arguments=arguments):
                code, output, errors = self.invoke(*arguments)
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertTrue(errors)

    def test_remote_json_is_validated_and_external_output_stays_on_stderr(self):
        value = {"schema_version": CLI_SCHEMA_VERSION, "action": "start", "completed": True}
        result = subprocess.CompletedProcess([], 0, json.dumps(value), "native progress\n")
        code, output, errors = self.invoke("start", "demo", "--json", remote=result)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), value)
        self.assertEqual(errors, "native progress\n")
        self.assertTrue(self.remote_call.kwargs["capture"])
        self.assertIsNone(self.remote_call.kwargs["timeout"])
        for result in (
            subprocess.CompletedProcess([], 17, "", "SECRET arguments"),
            subprocess.CompletedProcess([], 0, "not json SECRET", "SECRET arguments"),
            subprocess.CompletedProcess([], 0, json.dumps({"schema_version": 999}), ""),
            subprocess.CompletedProcess(
                [], 0, json.dumps({"schema_version": CLI_SCHEMA_VERSION, "action": "scan"}), ""
            ),
            subprocess.CompletedProcess([], 0, "{}\n{}", ""),
        ):
            with self.subTest(result=result):
                code, output, errors = self.invoke("status", "demo", "--json", remote=result)
                self.assertEqual(code, 1)
                payload = json.loads(output)
                self.assertEqual(payload["error"]["code"], "target_response_invalid")
                self.assertNotIn("SECRET", output)
                self.assertEqual(errors, result.stderr)

    def test_reports_keep_payloads_and_mark_only_explicit_failures_incomplete(self):
        base = {"collectors": {}, "repositories": [], "discovery_issues": []}
        cases = []
        for status in (
            "ok",
            "skipped",
            "missing",
            "updates",
            "failed",
            "timeout",
            "partial",
            "parse-error",
        ):
            value = copy.deepcopy(base)
            value["collectors"]["fixture"] = {"status": status}
            cases.append((value, int(status in {"failed", "timeout", "partial", "parse-error"})))
        for field, result in (
            ("fetch", {"status": "failed"}),
            ("worktree", "error"),
            ("tracking", "tracking-error"),
        ):
            value = copy.deepcopy(base)
            value["repositories"] = [{field: result}]
            cases.append((value, 1))
        value = copy.deepcopy(base)
        value["discovery_issues"] = [{"status": "unreadable"}]
        cases.append((value, 1))
        for value, expected in cases:
            with (
                self.subTest(value=value),
                patch("dev_tools.cli.collect_report", return_value=value),
            ):
                code, output, errors = self.invoke("report", "--json")
            self.assertEqual(code, expected, errors)
            self.assertEqual(json.loads(output), value)

    def test_report_save_failure_does_not_append_an_error_after_a_success_document(self):
        with patch("dev_tools.cli.collect_report", return_value={"collectors": {}}):
            self.assert_error(
                self.invoke("report", "--json", "--output", str(self.root / "missing/report.json")),
                "operation_failed",
                action="report",
            )


if __name__ == "__main__":
    unittest.main()
