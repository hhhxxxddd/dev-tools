from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.cli import main, parser
from dev_tools.controller import _run_windows_installer, _update_windows, _upgrade, run
from dev_tools.runtimes.router import forward_remote
from dev_tools.settings import Settings, defaults


class EnvironmentRoutingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = Settings(self.root / "caller.toml", "missing", defaults(), {})
        self.settings.data["language"] = "en"
        self.enterContext(patch("dev_tools.cli.load_settings", return_value=self.settings))
        self.native = "win" if os.name == "nt" else "wsl"
        self.remote = "wsl" if os.name == "nt" else "win"

    def invoke(self, *arguments):
        with (
            contextlib.redirect_stdout(output := io.StringIO()),
            contextlib.redirect_stderr(errors := io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(list(arguments))
        return stopped.exception.code, output.getvalue(), errors.getvalue()

    def test_toolbox_groups_default_to_help_without_running_actions(self):
        for language in ("zh", "en"):
            self.settings.data["language"] = language
            for command in ("ssh", "hosts"):
                with (
                    patch("dev_tools.ssh.run", side_effect=AssertionError("SSH action")),
                    patch("dev_tools.hosts.run", side_effect=AssertionError("hosts action")),
                    patch("dev_tools.runtimes.router.forward_remote") as relay,
                    patch("dev_tools.runtimes.diagnostics.target_status") as probe,
                ):
                    expected = self.invoke(command, "-h")
                    self.assertEqual(expected[0], 0, expected[2])
                    self.assertEqual(expected[2], "")
                    self.assertIn("list", expected[1])
                    self.assertIn("edit", expected[1])
                    for arguments in (
                        [command],
                        [command, "--help"],
                        ["-e", self.native, command],
                        [command, "--env=" + self.native],
                        [command, "--config", str(self.settings.path)],
                    ):
                        with self.subTest(language=language, arguments=arguments):
                            self.assertEqual(self.invoke(*arguments), expected)
                    relay.assert_not_called()
                    probe.assert_not_called()

    def test_remote_toolbox_groups_request_target_help_without_caller_config(self):
        for command in ("ssh", "hosts"):
            for arguments in (
                [command, "-e", self.remote],
                ["--env=" + self.remote, command],
                [command, "-e", self.remote, "-h"],
                [command, "--help", "-e", self.remote],
            ):
                with (
                    self.subTest(arguments=arguments),
                    patch(
                        "dev_tools.runtimes.diagnostics.target_status",
                        return_value={"state": "installed", "version": "9.8.7"},
                    ),
                    patch(
                        "dev_tools.runtimes.router.forward_remote",
                        return_value=subprocess.CompletedProcess([], 0, "Target help\n", ""),
                    ) as relay,
                ):
                    code, output, errors = self.invoke(
                        "--config", str(self.settings.path), *arguments
                    )
                    self.assertEqual((code, errors), (0, ""))
                    self.assertIn("Target help\n", output)
                    self.assertIn("9.8.7", output)
                    target, requested = relay.call_args.args
                    self.assertEqual(target, self.remote)
                    self.assertEqual(requested[0], command)
                    self.assertIn(requested[1], ("-h", "--help"))
                    self.assertEqual(len(requested), 2)

    def test_toolbox_help_default_keeps_invalid_and_incomplete_actions_as_errors(self):
        for arguments in (
            ["ssh", "unknown"],
            ["ssh", "show"],
            ["ssh", "connect"],
            ["hosts", "unknown"],
            ["hosts", "use"],
            ["ssh", "--unknown"],
            ["hosts", "--unknown"],
        ):
            with self.subTest(arguments=arguments):
                code, _, errors = self.invoke(*arguments)
                self.assertEqual(code, 2)
                self.assertTrue(errors)

    def test_native_helpers_accept_explicit_same_environment_without_transport(self):
        for command, function in (
            ("config", "config_command"),
            ("sysinfo", "cmd_sysinfo"),
            ("report", "cmd_report"),
        ):
            for flags in ([], ["-e", self.native]):
                with (
                    self.subTest(command=command, flags=flags),
                    patch("dev_tools.cli." + function, return_value=0) as local,
                    patch("dev_tools.runtimes.router.forward_remote") as transport,
                ):
                    self.assertEqual(self.invoke(command, *flags)[0], 0)
                    self.assertIs(local.call_args.args[0].settings, self.settings)
                    transport.assert_not_called()

    def test_remote_helpers_use_target_config_and_preserve_json_and_exit_status(self):
        for command in ("config", "sysinfo", "report"):
            for flags in (["-e", self.remote], ["--env=" + self.remote]):
                payload = {"target": self.remote, "settings": {"editor": ["target-editor"]}}
                result = subprocess.CompletedProcess([], 1, json.dumps(payload), "")
                with (
                    self.subTest(command=command, flags=flags),
                    patch("dev_tools.runtimes.diagnostics.preflight"),
                    patch("dev_tools.runtimes.router.forward_remote", return_value=result) as relay,
                    patch(
                        "dev_tools.cli.config_command", side_effect=AssertionError("local config")
                    ),
                    patch("dev_tools.cli.cmd_sysinfo", side_effect=AssertionError("local sysinfo")),
                    patch("dev_tools.cli.cmd_report", side_effect=AssertionError("local report")),
                ):
                    code, output, errors = self.invoke(
                        "--config", str(self.settings.path), command, *flags, "--json"
                    )
                self.assertEqual(code, 1, errors)
                self.assertEqual(json.loads(output), payload)
                self.assertEqual(relay.call_args.args, (self.remote, [command, "--json"]))
                self.assertIsNone(relay.call_args.kwargs["timeout"])

    def test_remote_editor_and_report_actions_do_not_capture_interactive_output(self):
        for arguments in (["config", "edit"], ["report", "--refresh", "--output", "new.json"]):
            with (
                self.subTest(arguments=arguments),
                patch("dev_tools.runtimes.diagnostics.preflight"),
                patch("dev_tools.runtimes.router.forward_remote", return_value=0) as relay,
            ):
                self.assertEqual(self.invoke(*arguments, "-e", self.remote)[0], 0)
                self.assertFalse(relay.call_args.kwargs["capture"])
                self.assertEqual(relay.call_args.args[1], arguments)

    def test_remote_helper_errors_remain_one_validated_error_document(self):
        error = {
            "schema_version": 3,
            "action": "report",
            "error": {
                "code": "invalid_config",
                "message": "target configuration invalid",
                "details": {},
            },
        }
        with (
            patch("dev_tools.runtimes.diagnostics.preflight"),
            patch(
                "dev_tools.runtimes.router.forward_remote",
                return_value=subprocess.CompletedProcess([], 1, json.dumps(error), ""),
            ),
        ):
            code, output, errors = self.invoke("report", "-e", self.remote, "--json")
        self.assertEqual(code, 1, errors)
        self.assertEqual(json.loads(output), error)
        with (
            patch("dev_tools.runtimes.diagnostics.preflight"),
            patch(
                "dev_tools.runtimes.router.forward_remote",
                return_value=subprocess.CompletedProcess([], 2, "not JSON", ""),
            ),
        ):
            code, output, _ = self.invoke("config", "-e", self.remote, "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["error"]["code"], "target_response_invalid")

    def test_native_status_never_queries_the_other_side(self):
        with patch(
            "dev_tools.runtimes.diagnostics.target_status",
            side_effect=AssertionError("foreign probe"),
        ):
            code, output, errors = self.invoke("self", "status", "--json")
        self.assertEqual(code, 0, errors)
        value = json.loads(output)
        self.assertEqual(value["environment"], "windows" if self.native == "win" else "wsl")
        self.assertEqual(value["controller_version"], __version__)
        self.assertEqual(value["deployment"]["state"], "installed")

    def test_remote_status_returns_target_version_or_missing_state(self):
        value = {
            "schema_version": 1,
            "environment": "windows" if self.remote == "win" else "wsl",
            "controller_version": "9.8.7",
            "deployment": {"state": "installed", "version": "9.8.7"},
        }
        with (
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": "9.8.7"},
            ),
            patch(
                "dev_tools.runtimes.router.forward_remote",
                return_value=subprocess.CompletedProcess([], 0, json.dumps(value), ""),
            ),
        ):
            code, output, errors = self.invoke("self", "status", "-e", self.remote, "--json")
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output), value)
        with (
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "missing", "version": None},
            ),
            patch("dev_tools.runtimes.router.forward_remote") as relay,
        ):
            code, output, errors = self.invoke("self", "status", "-e", self.remote, "--json")
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["deployment"]["state"], "missing")
        self.assertIsNone(json.loads(output)["controller_version"])
        relay.assert_not_called()

    def test_wsl_to_windows_controller_mutations_are_forwarded_without_local_work(self):
        for action in ("update", "upgrade", "recover"):
            args = parser().parse_args(["self", action, "-e", "win", "--yes"])
            args.settings = self.settings
            if action != "recover":
                args.source = str(self.root)
            with (
                self.subTest(action=action),
                patch("dev_tools.controller.WINDOWS", False),
                patch("dev_tools.runtimes.diagnostics.forward_command", return_value=0) as relay,
                patch(
                    "dev_tools.controller.verify_source", side_effect=AssertionError("local source")
                ),
                patch(
                    "dev_tools.controller._install_wsl", side_effect=AssertionError("local deploy")
                ),
            ):
                self.assertEqual(run(args), 0)
                expected = ["self", action]
                if args.source:
                    expected += ["--source", str(self.root)]
                self.assertEqual(relay.call_args.args[:2], ("win", [*expected, "--yes"]))

    def test_windows_redeploy_verifies_before_stopping_and_preserves_recovery(self):
        args = parser().parse_args(["self", "update", "-e", "win", "--yes"])
        with (
            patch("dev_tools.controller.verify_source", side_effect=ValueError("untrusted")),
            patch("dev_tools.controller_update.update_with_recovery") as update,
            self.assertRaises(ValueError),
        ):
            _update_windows(args)
        update.assert_not_called()
        events = []

        def recovery(session, install):
            self.assertTrue(session.yes)
            events.append("stop")
            result = install()
            events.append("restore")
            return result

        with (
            patch("dev_tools.controller.verify_source"),
            patch("dev_tools.controller.clear_controller_bytecode"),
            patch("dev_tools.controller._scoop_installation", return_value=False),
            patch(
                "dev_tools.controller._run_windows_installer",
                side_effect=lambda _: events.append("install") or 0,
            ),
            patch("dev_tools.controller_update.update_with_recovery", side_effect=recovery),
        ):
            self.assertEqual(_update_windows(args), 0)
        self.assertEqual(events, ["stop", "install", "restore"])

    def test_windows_can_deploy_from_an_explicit_native_wsl_source(self):
        args = parser().parse_args(
            ["self", "update", "-e", "wsl", "--source", "/tmp/source", "--yes"]
        )
        args.settings = self.settings
        with (
            patch("dev_tools.controller.WINDOWS", True),
            patch("dev_tools.runtimes.diagnostics.forward_command", return_value=0) as relay,
            patch(
                "dev_tools.controller._install_wsl", side_effect=AssertionError("Windows source")
            ),
        ):
            self.assertEqual(run(args), 0)
        self.assertEqual(
            relay.call_args.args[:2],
            ("wsl", ["self", "update", "--source", "/tmp/source", "--yes"]),
        )

    def test_windows_installer_preserves_scoop_shim_and_native_host_binding(self):
        (self.root / ".host-python.win").write_text("native-controller-python", encoding="utf-8")
        with (
            patch("dev_tools.controller.REPOSITORY", self.root),
            patch("dev_tools.controller._scoop_installation", return_value=True),
            patch(
                "dev_tools.controller.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as process,
        ):
            self.assertEqual(_run_windows_installer(self.root), 0)
        command = process.call_args.args[0]
        self.assertEqual(command[-1], "-NoProfile")
        self.assertEqual(
            process.call_args.kwargs["env"]["DEV_TOOLS_PYTHON"], "native-controller-python"
        )

    def test_status_rejects_an_old_target_that_ignores_environment(self):
        with (
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            ),
            patch(
                "dev_tools.runtimes.router.forward_remote",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps({"controller_version": __version__, "wsl": {"state": "installed"}}),
                    "",
                ),
            ),
        ):
            code, output, _ = self.invoke("self", "status", "-e", self.remote, "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["error"]["code"], "target_command_unsupported")

    def test_scoop_upgrade_rejects_a_source_that_would_leave_the_shim_on_old_code(self):
        args = parser().parse_args(["upgrade", "-e", "win", "--source", str(self.root)])
        with (
            patch("dev_tools.controller.WINDOWS", True),
            patch("dev_tools.controller._scoop_installation", return_value=True),
            patch("dev_tools.controller_update.UpdateSession") as session,
            self.assertRaisesRegex(ValueError, "Scoop upgrades do not accept"),
        ):
            _upgrade(args)
        session.assert_not_called()

    def test_wsl_windows_path_conversion_preserves_subprocess_options(self):
        for arguments, expected in (
            (["self", "status", "--json"], ["self", "status", "--json"]),
            (
                ["self", "update", "--source", "source"],
                ["self", "update", "--source", "mapped:source"],
            ),
            (["upgrade", "--source=source"], ["upgrade", "--source", "mapped:source"]),
            (["report", "--output=new 报告.json"], ["report", "--output", "mapped:new 报告.json"]),
        ):
            with (
                self.subTest(arguments=arguments),
                patch(
                    "dev_tools.runtimes.router.os",
                    SimpleNamespace(name="posix", environ=os.environ),
                ),
                patch(
                    "dev_tools.runtimes.platforms.windows_interop.powershell_entry",
                    return_value="pwsh.exe",
                ),
                patch(
                    "dev_tools.runtimes.router._windows_path",
                    side_effect=lambda value: "mapped:" + str(value),
                ),
                patch(
                    "dev_tools.runtimes.router._windows_entry", return_value=["-File", "entry.ps1"]
                ) as entry,
                patch(
                    "dev_tools.runtimes.router._run_transport",
                    return_value=subprocess.CompletedProcess([], 0, "{}", ""),
                ) as process,
            ):
                forward_remote("win", arguments, settings=self.settings, capture=True, timeout=None)
            self.assertEqual(entry.call_args.args[0], expected)
            self.assertTrue(process.call_args.kwargs["capture_output"])
            self.assertIsNone(process.call_args.kwargs["timeout"])
