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
from dev_tools.cli import main
from dev_tools.projects.targeting import REGISTRY_LOOKUP
from dev_tools.schemas import CLI_SCHEMA_VERSION
from dev_tools.settings import Settings, defaults


class ProjectTargetingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"DEV_TOOLS_REGISTRY_ROOT": str(self.root)}))
        self.native = "win" if os.name == "nt" else "wsl"
        self.remote = "wsl" if self.native == "win" else "win"
        settings = Settings(self.root / "settings.toml", "missing", defaults(), {})
        self.enterContext(patch("dev_tools.cli.load_settings", return_value=settings))
        self.enterContext(
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            )
        )
        self.operation = self.enterContext(patch("dev_tools.projects.cli.run", return_value=0))
        self.transport = self.enterContext(patch("dev_tools.runtimes.router.forward_remote"))

    def lookup(self, registered, **fields):
        value = {
            "schema_version": 1,
            "name": "demo",
            "environment": self.remote,
            "registered": registered,
        }
        return subprocess.CompletedProcess([], 0, json.dumps(value | fields), "")

    def invoke(self, *arguments):
        with (
            contextlib.redirect_stdout(output := io.StringIO()),
            contextlib.redirect_stderr(errors := io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(list(arguments))
        return stopped.exception.code, output.getvalue(), errors.getvalue()

    def test_lookup_reads_only_the_named_slot_and_returns_no_binding_data(self):
        for registered in (False, True):
            if registered:
                (self.root / "demo.json").write_text("private binding contents", encoding="utf-8")
            code, output, errors = self.invoke("-e", self.native, REGISTRY_LOOKUP, "demo")
            self.assertEqual((code, errors), (0, ""))
            self.assertEqual(
                json.loads(output),
                {
                    "schema_version": 1,
                    "name": "demo",
                    "environment": self.native,
                    "registered": registered,
                },
            )
        self.transport.assert_not_called()
        self.operation.assert_not_called()

    def test_only_local_registration_executes_locally_after_peer_lookup(self):
        (self.root / "demo.json").touch()
        self.transport.side_effect = [self.lookup(False), None]
        code, _, errors = self.invoke("stop", "demo")
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(
            self.transport.call_args_list[0].args, (self.remote, [REGISTRY_LOOKUP, "demo"])
        )
        self.operation.assert_called_once()
        self.assertEqual(self.operation.call_args.args[0].env, self.native)

    def test_only_remote_registration_forwards_once_after_lookup(self):
        for action in ("start", "stop"):
            with self.subTest(action=action):
                self.transport.reset_mock()
                payload = {
                    "schema_version": CLI_SCHEMA_VERSION,
                    "action": action,
                    "completed": True,
                }
                self.transport.side_effect = [
                    self.lookup(True),
                    subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
                ]
                flags = ["--service", "api"] if action == "start" else []
                code, output, errors = self.invoke(action, "demo", *flags, "--json")
                self.assertEqual((code, errors), (0, ""))
                self.assertEqual(json.loads(output), payload)
                lookup, action_call = self.transport.call_args_list
                self.assertEqual(lookup.args, (self.remote, [REGISTRY_LOOKUP, "demo"]))
                self.assertEqual(
                    action_call.args, (self.remote, [action, "demo", *flags, "--json"])
                )
                self.assertIsNone(action_call.kwargs["timeout"])
        self.operation.assert_not_called()

    def test_duplicate_missing_and_uncertain_registrations_never_execute(self):
        for local, remote, expected in (
            (False, self.lookup(False), "project_not_registered"),
            (True, self.lookup(True), "project_environment_ambiguous"),
            (True, self.lookup(False, name="other"), "project_environment_unconfirmed"),
            (True, self.lookup(0), "project_environment_unconfirmed"),
            (
                False,
                subprocess.CompletedProcess([], 2, "", "private error"),
                "project_environment_unconfirmed",
            ),
            (True, subprocess.TimeoutExpired([], 10), "project_environment_unconfirmed"),
        ):
            with self.subTest(local=local, expected=expected, reply=remote):
                path = self.root / "demo.json"
                if local:
                    path.touch()
                else:
                    path.unlink(missing_ok=True)
                self.transport.reset_mock(side_effect=True)
                self.transport.side_effect = [remote]
                code, output, errors = self.invoke("start", "demo", "--json")
                self.assertEqual((code, errors), (1, ""))
                self.assertEqual(json.loads(output)["error"]["code"], expected)
                self.transport.assert_called_once()
        self.operation.assert_not_called()

    def test_explicit_and_other_invocations_skip_registration_lookup(self):
        (self.root / "demo.json").touch()
        self.transport.return_value = None
        for arguments in (
            ["-e", self.native, "start", "demo"],
            ["stop", "demo", "--env=" + self.native],
            ["start"],
            ["stop"],
            ["start", "--all"],
            ["restart", "demo"],
        ):
            with self.subTest(arguments=arguments):
                self.transport.reset_mock()
                self.operation.reset_mock()
                code, _, errors = self.invoke(*arguments)
                self.assertEqual((code, errors), (0, ""))
                self.transport.assert_called_once()
                self.assertEqual(self.transport.call_args.args[0], self.native)
                self.assertNotIn(REGISTRY_LOOKUP, self.transport.call_args.args[1])
                self.operation.assert_called_once()
