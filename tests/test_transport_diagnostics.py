from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.cli import main
from dev_tools.cli_errors import TargetError, error_payload
from dev_tools.i18n import message
from dev_tools.runtimes.diagnostics import preflight, response_payload, target_status


class TransportDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(language="en", errors=(), distro="Fixture")
        self.target = "wsl" if os.name == "nt" else "win"

    def test_old_target_fails_before_execution_but_stop_remains_available(self):
        with (
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": "0.4.0"},
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            for command, code in (
                ("check", "target_command_unsupported"),
                ("prepare", "target_version_mismatch"),
                ("start", "target_version_mismatch"),
            ):
                with self.subTest(command=command), self.assertRaises(TargetError) as failed:
                    preflight(self.target, [command, "demo", "--json"], self.settings)
                self.assertEqual(error_payload(failed.exception)["error"]["code"], code)
                self.assertEqual(failed.exception.details["target_version"], "0.4.0")
            self.assertEqual(preflight(self.target, ["stop", "demo"], self.settings), "0.4.0")

    def test_missing_target_is_explicit_and_has_safe_details(self):
        with (
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "missing", "version": None},
            ),
            self.assertRaises(TargetError) as failed,
        ):
            preflight(self.target, ["status", "demo"], self.settings)
        self.assertEqual(failed.exception.code, "target_unavailable")
        self.assertEqual(failed.exception.details["environment"], self.target)
        self.assertNotIn("upgrade -e", str(failed.exception))
        self.assertNotIn("self update", str(failed.exception))

    def test_windows_discovery_failure_is_preserved_through_preflight_and_help(self):
        failure = TargetError(
            "target_unavailable",
            message("Windows PowerShell discovery failed in root shell"),
            environment="win",
            reason="powershell_missing",
        )
        with patch("dev_tools.runtimes.router.forward_remote", side_effect=failure):
            status = target_status("win", self.settings)
        self.assertEqual(status["reason"], "powershell_missing")
        with (
            patch("dev_tools.runtimes.diagnostics.target_status", return_value=status),
            patch("dev_tools.runtimes.diagnostics.os.name", "posix"),
            self.assertRaises(TargetError) as failed,
        ):
            preflight("win", ["list"], self.settings)
        self.assertIn("discovery", str(failed.exception))
        self.assertEqual(failed.exception.details["reason"], "powershell_missing")

    def test_windows_probe_distinguishes_timeout_and_bad_entrypoint(self):
        for result, reason in (
            (subprocess.TimeoutExpired([], 5), "probe_timeout"),
            (subprocess.CompletedProcess([], 1, "", "SECRET"), "entrypoint_failed"),
        ):
            with patch(
                "dev_tools.runtimes.router.forward_remote",
                side_effect=result if isinstance(result, BaseException) else None,
                return_value=result,
            ):
                status = target_status("win", self.settings)
            self.assertEqual(status["reason"], reason)
            self.assertNotIn("SECRET", str(status))

    def test_explicit_version_and_help_are_queried_from_target_readonly(self):
        for arguments in (["-v"], ["--version"], ["help"], ["help", "check"], ["check", "--help"]):
            with (
                self.subTest(arguments=arguments),
                patch("dev_tools.cli.load_settings", return_value=self.settings),
                patch(
                    "dev_tools.runtimes.diagnostics.target_status",
                    return_value={"state": "installed", "version": "0.4.0"},
                ),
                patch(
                    "dev_tools.runtimes.router.forward_remote",
                    return_value=subprocess.CompletedProcess([], 0, "ACTUAL TARGET HELP", ""),
                ) as remote,
                contextlib.redirect_stdout(output := io.StringIO()),
                self.assertRaises(SystemExit) as stopped,
            ):
                main(["-e", self.target, *arguments])
            self.assertEqual(stopped.exception.code, 0)
            self.assertIn("0.4.0", output.getvalue())
            if arguments[0] in {"-v", "--version"}:
                remote.assert_not_called()
            else:
                self.assertIn("ACTUAL TARGET HELP", output.getvalue())
                self.assertIn("--help", remote.call_args.args[1])
                self.assertNotIn("--config", remote.call_args.args[1])

    def test_invalid_response_keeps_stderr_but_excludes_it_from_json(self):
        result = subprocess.CompletedProcess([], 2, "usage: SECRET argv", "native error detail\n")
        with (
            contextlib.redirect_stderr(output := io.StringIO()),
            self.assertRaises(TargetError) as failed,
        ):
            response_payload(result, self.target, "check")
        self.assertEqual(output.getvalue(), result.stderr)
        payload = error_payload(failed.exception, action="check")
        self.assertEqual(payload["error"]["code"], "target_response_invalid")
        self.assertNotIn("SECRET", json.dumps(payload))
        self.assertEqual(payload["error"]["details"]["returncode"], 2)

    def test_valid_target_error_and_exit_status_are_preserved(self):
        value = {
            "schema_version": 3,
            "action": "status",
            "error": {"code": "project_not_registered", "message": "target message", "details": {}},
        }
        with (
            patch("dev_tools.cli.load_settings", return_value=self.settings),
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            ),
            patch(
                "dev_tools.runtimes.router.forward_remote",
                return_value=subprocess.CompletedProcess([], 2, json.dumps(value), ""),
            ),
            contextlib.redirect_stdout(output := io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(["-e", self.target, "status", "demo", "--json"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue()), value)
