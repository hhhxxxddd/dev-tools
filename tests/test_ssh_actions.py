from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.cli import main, parser
from dev_tools.settings import Settings, defaults, editor_command
from dev_tools.ssh_config import collect_hosts


class SshActionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home = self.root / "home"
        self.home.mkdir()
        self.config = self.home / ".ssh/config"
        self.system = self.root / "system/ssh_config"
        self.settings = Settings(self.root / "preferences.toml", "missing", defaults(), {})
        self.settings.data["language"] = "en"
        self.native = "win" if os.name == "nt" else "wsl"
        self.remote = "wsl" if self.native == "win" else "win"
        self.enterContext(patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "0"}))
        self.enterContext(patch("dev_tools.ssh.sys.stdin", io.StringIO()))
        self.enterContext(patch("dev_tools.cli.load_settings", return_value=self.settings))
        self.enterContext(
            patch(
                "dev_tools.ssh.native_identity",
                return_value=(self.home, "native-user", self.system),
            )
        )
        self.enterContext(
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            )
        )

    def write(self, text, path=None):
        path = path or self.config
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def invoke(self, *arguments):
        with (
            contextlib.redirect_stdout(output := io.StringIO()),
            contextlib.redirect_stderr(errors := io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(list(arguments))
        return stopped.exception.code, output.getvalue(), errors.getvalue()

    def test_show_is_static_and_reports_include_source_without_executing_match(self):
        self.write('Include "conf.d/*.conf"\n')
        included = self.home / ".ssh/conf.d/servers.conf"
        self.write(
            '# preserve\r\nHost demo\r\n HostName demo.test\r\nMatch exec "never-execute"\r\n Port 2222\r\n',
            included,
        )
        before = {path: path.read_bytes() for path in (self.config, included)}
        with patch(
            "dev_tools.ssh.subprocess.run", side_effect=AssertionError("query executed a process")
        ):
            code, output, errors = self.invoke("ssh", "show", "DEMO", "--json")
        self.assertEqual((code, errors), (0, ""))
        value = json.loads(output)
        self.assertEqual((value["action"], value["operation"]), ("ssh", "show"))
        self.assertEqual(value["host"]["source"], str(included.resolve()))
        self.assertEqual(value["host"]["line"], 2)
        self.assertIn("port", value["host"]["unresolved_fields"])
        self.assertNotIn("never-execute", output)
        self.assertNotIn("hosts", value)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_show_requires_an_exact_unambiguous_alias(self):
        for contents, alias in (("Host demo-other\n", "demo"), ("Host demo DEMO\n", "demo")):
            with self.subTest(contents=contents):
                self.write(contents)
                code, output, errors = self.invoke("ssh", "show", alias, "--json")
                self.assertEqual((code, errors), (1, ""))
                self.assertEqual(json.loads(output)["action"], "ssh")

    def test_edit_opens_only_the_declaration_file_and_preserves_other_files(self):
        self.write('Include "conf.d/*.conf"\n# root comment\n')
        included = self.home / ".ssh/conf.d/servers.conf"
        self.write("# keep\r\nHost demo\r\n User old\r\n", included)
        original = self.config.read_bytes()

        def edit(command, **kwargs):
            self.assertEqual(command, ["editor", str(included.resolve())])
            included.write_bytes(included.read_bytes().replace(b"old", b"new"))
            return subprocess.CompletedProcess(command, 0)

        with (
            patch(
                "dev_tools.ssh.editor_command", return_value=["editor", str(included.resolve())]
            ) as editor,
            patch("dev_tools.ssh.subprocess.run", side_effect=edit),
        ):
            code, output, errors = self.invoke("ssh", "edit", "demo")
        self.assertEqual((code, errors), (0, ""))
        self.assertIn(str(included.resolve()) + ":2", output)
        editor.assert_called_once_with(self.settings, included.resolve(), line=2)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(included.read_bytes(), b"# keep\r\nHost demo\r\n User new\r\n")

    def test_edit_creates_private_native_user_config_only_when_requested(self):
        with (
            patch("dev_tools.ssh.editor_command", return_value=["editor"]),
            patch("dev_tools.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 0)),
        ):
            self.assertEqual(self.invoke("ssh", "edit")[0], 0)
        self.assertTrue(self.config.is_file())
        self.assertFalse(self.system.exists())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)

    def test_unknown_edit_alias_does_not_create_config_or_launch_editor(self):
        with patch(
            "dev_tools.ssh.editor_command", side_effect=AssertionError("unknown alias opened")
        ):
            self.assertEqual(self.invoke("ssh", "edit", "unknown")[0], 1)
        self.assertFalse(self.config.exists())

    def test_edit_can_repair_invalid_configuration_and_keeps_edits_on_failure(self):
        self.write("Host demo\n Port invalid\n")

        def edit(command, **kwargs):
            self.write("Host renamed\n Port 2200\n")
            return subprocess.CompletedProcess(command, 0)

        with (
            patch("dev_tools.ssh.editor_command", return_value=["editor"]),
            patch("dev_tools.ssh.subprocess.run", side_effect=edit),
        ):
            self.assertEqual(self.invoke("ssh", "edit", "demo")[0], 0)
        self.assertIn("renamed", self.config.read_text())
        with (
            patch("dev_tools.ssh.editor_command", return_value=["editor"]),
            patch("dev_tools.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 5)),
        ):
            self.assertEqual(self.invoke("ssh", "edit")[0], 1)
        self.assertIn("renamed", self.config.read_text())

    def test_edit_and_connect_reject_json_before_any_action(self):
        for operation in ("edit", "connect"):
            for arguments in (
                ["ssh", operation, "demo", "--json"],
                ["ssh", "--json", operation, "demo"],
            ):
                with (
                    self.subTest(arguments=arguments),
                    patch(
                        "dev_tools.ssh.subprocess.run",
                        side_effect=AssertionError("JSON action executed"),
                    ),
                ):
                    code, output, errors = self.invoke(*arguments)
                self.assertEqual((code, errors), (2, ""))
                self.assertEqual(json.loads(output)["error"]["code"], "invalid_arguments")

    def test_connect_preserves_native_arguments_stdio_and_session_exit_codes(self):
        for native_code, expected in (
            (0, 0),
            (2, 2),
            (7, 7),
            (255, 255),
            (-15, 143),
            (0xC000013A, 130),
        ):
            with (
                self.subTest(code=native_code),
                patch("dev_tools.ssh.shutil.which", return_value="native-ssh"),
                patch(
                    "dev_tools.ssh.subprocess.run",
                    return_value=subprocess.CompletedProcess([], native_code),
                ) as client,
            ):
                code, _, _ = self.invoke("ssh", "connect", "demo;literal")
            self.assertEqual(code, expected)
            self.assertEqual(client.call_args.args[0][-3:], ["native-ssh", "--", "demo;literal"])
            self.assertEqual(client.call_args.kwargs, {"check": False})

    def test_missing_client_and_invalid_destination_do_not_start_a_process(self):
        with (
            patch("dev_tools.ssh.shutil.which", return_value=None),
            patch(
                "dev_tools.ssh.subprocess.run",
                side_effect=AssertionError("missing client launched"),
            ),
        ):
            self.assertEqual(self.invoke("ssh", "connect", "demo")[0], 1)
        for alias in ("-oProxyCommand=bad", "two words", "bad\nname", "bad\x1bname"):
            with (
                self.subTest(alias=alias),
                patch(
                    "dev_tools.ssh.subprocess.run",
                    side_effect=AssertionError("invalid destination launched"),
                ),
            ):
                self.assertEqual(self.invoke("ssh", "connect", "--", alias)[0], 1)

    def test_remote_interactive_actions_probe_support_then_forward_without_capture(self):
        for operation, code in (("edit", 0), ("connect", 7), ("connect", 2), ("connect", 255)):
            with (
                self.subTest(operation=operation, code=code),
                patch(
                    "dev_tools.runtimes.router.forward_remote",
                    side_effect=[subprocess.CompletedProcess([], 0, "help", ""), code],
                ) as relay,
            ):
                result = self.invoke("ssh", operation, "demo", "-e", self.remote)
            self.assertEqual(result[0], code)
            self.assertEqual(
                relay.call_args_list[0].args, (self.remote, ["ssh", operation, "--help"])
            )
            self.assertEqual(relay.call_args_list[0].kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(relay.call_args.args, (self.remote, ["ssh", operation, "--", "demo"]))
            self.assertFalse(relay.call_args.kwargs["capture"])
            self.assertIsNone(relay.call_args.kwargs["timeout"])

    def test_remote_old_command_fails_before_editor_or_connection(self):
        for operation in ("edit", "connect"):
            with (
                self.subTest(operation=operation),
                patch(
                    "dev_tools.runtimes.router.forward_remote",
                    return_value=subprocess.CompletedProcess([], 2, "old help", ""),
                ) as relay,
            ):
                code, _, errors = self.invoke("ssh", operation, "demo", "-e", self.remote)
            self.assertEqual(code, 1)
            self.assertIn("does not support ssh " + operation, errors)
            self.assertEqual(relay.call_count, 1)

    def test_remote_show_is_rendered_locally_and_rejects_wrong_alias(self):
        self.write("Host demo\n User developer\n")
        value = {
            "schema_version": 3,
            "action": "ssh",
            "operation": "show",
            "environment": "windows" if self.remote == "win" else "wsl",
            **collect_hosts(self.home, "user", self.system),
        }
        value["host"] = value.pop("hosts")[0]
        with patch(
            "dev_tools.runtimes.router.forward_remote",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(value), ""),
        ) as relay:
            code, output, errors = self.invoke("ssh", "show", "demo", "-e", self.remote, "--json")
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(json.loads(output), value)
        self.assertEqual(
            relay.call_args.args, (self.remote, ["ssh", "show", "--json", "--", "demo"])
        )
        value["host"]["name"] = "other"
        with patch(
            "dev_tools.runtimes.router.forward_remote",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(value), ""),
        ):
            code, output, _ = self.invoke("ssh", "show", "demo", "-e", self.remote, "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["error"]["code"], "target_response_invalid")

    def test_environment_flags_are_accepted_on_every_new_command(self):
        for operation in ("show", "edit", "connect"):
            for arguments in (
                ["-e", "win", "ssh", operation, "demo"],
                ["ssh", "-e", "win", operation, "demo"],
                ["ssh", operation, "demo", "-e", "win"],
            ):
                self.assertEqual(parser().parse_args(arguments).env, "win")

    def test_editor_line_navigation_preserves_configured_flags(self):
        for executable, expected in (
            ("code.exe", ["--wait", "--goto", str(self.config) + ":12:1"]),
            ("vim.exe", ["--wait", "+12", str(self.config)]),
            ("notepad.exe", ["--wait", str(self.config)]),
        ):
            self.settings.data["editor"] = ["editor", "--wait"]
            with (
                self.subTest(executable=executable),
                patch("dev_tools.settings.shutil.which", return_value=executable),
            ):
                self.assertEqual(
                    editor_command(self.settings, self.config, line=12)[-len(expected) :], expected
                )

    @unittest.skipIf(os.name == "nt", "native WSL identity")
    def test_sudo_connection_uses_invoking_user_and_preserves_only_native_agent(self):
        from dev_tools.runtimes.platforms.preferences import user_command

        with (
            patch(
                "dev_tools.runtimes.platforms.preferences.invoking_user",
                return_value=SimpleNamespace(pw_name="developer", pw_uid=1001),
            ),
            patch("dev_tools.runtimes.platforms.preferences.os.geteuid", return_value=0),
            patch.dict(os.environ, {"SSH_AUTH_SOCK": "/native/agent"}),
        ):
            command = user_command(["ssh", "--", "demo"], preserve=("SSH_AUTH_SOCK",))
        self.assertEqual(
            command,
            ["sudo", "-u", "developer", "--preserve-env=SSH_AUTH_SOCK", "--", "ssh", "--", "demo"],
        )

    def test_native_connection_inherits_real_stdin_and_returns_real_child_status(self):
        client = self.root / "fixture_client.py"
        client.write_text(
            'import sys\nprint("native-client:"+input(), flush=True)\nsys.exit(7)\n',
            encoding="utf-8",
        )
        runner = self.root / "runner.py"
        runner.write_text(
            "import sys\nfrom unittest.mock import patch\nfrom dev_tools.cli import main\n"
            'with patch("dev_tools.ssh.shutil.which", return_value=sys.executable):\n'
            ' main(["ssh", "connect", sys.argv[1]])\n',
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.pop("SUDO_USER", None)
        environment["DEV_TOOLS_CONFIG"] = str(self.root / "missing.toml")
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        result = subprocess.run(
            [sys.executable, str(runner), client.name],
            input="Y\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            cwd=self.root,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout.strip(), "native-client:Y")


if __name__ == "__main__":
    unittest.main()
