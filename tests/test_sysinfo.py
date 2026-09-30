from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.cli import parser
from dev_tools.sysinfo import collect_sysinfo, print_sysinfo


class SysinfoTests(unittest.TestCase):
    def test_missing_config_is_read_only_and_does_not_execute_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing.json"
            with patch("subprocess.run", side_effect=AssertionError("must not execute")):
                result = collect_sysinfo(path)
            self.assertEqual(result["config_state"], "missing")
            self.assertEqual(result["directories"], [])
            self.assertFalse(path.exists())

    def test_personal_secrets_are_excluded_from_both_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local.json"
            native_key = "windows" if os.name == "nt" else "wsl"
            config = {
                "directories": [
                    {"command": "pro", "description": "Projects", native_key: temporary}
                ],
                "tools": [{"command": "dev-tools", "description": "Tools", "help": "SECRET_HELP"}],
                "sshConnections": [{"target": "SECRET_HOST", "identity": "SECRET_KEY"}],
                "token": "SECRET_TOKEN",
                "proxy": "SECRET_PROXY",
            }
            path.write_text(json.dumps(config), encoding="utf-8")
            before = path.read_bytes()
            result = collect_sysinfo(path)
            self.assertEqual(result["config_state"], "loaded")
            self.assertTrue(result["directories"][0]["exists"])
            for as_json in (False, True):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    print_sysinfo(result, as_json=as_json)
                self.assertNotIn("SECRET_", output.getvalue())
                self.assertNotIn("sshConnections", output.getvalue())
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_config_has_safe_fallback_without_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local.json"
            for content in ('{"SECRET_TOKEN":', "[]"):
                path.write_text(content, encoding="utf-8")
                result = collect_sysinfo(path)
                self.assertEqual(result["config_state"], "invalid")
                self.assertEqual(result["directories"], [])
                self.assertNotIn("SECRET", json.dumps(result))

    def test_malformed_entries_and_terminal_control_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local.json"
            path.write_text(
                json.dumps(
                    {
                        "directories": "bad",
                        "tools": [
                            None,
                            {"command": "cmd; echo SECRET"},
                            {"command": "dev-tools", "description": "a\x1bb"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = collect_sysinfo(path)
            self.assertEqual(result["directories"], [])
            self.assertEqual(result["tools"], [{"command": "dev-tools", "description": "ab"}])

    def test_explicit_config_precedes_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local.json"
            path.write_text("{}", encoding="utf-8-sig")
            with patch.dict(os.environ, {"DEV_TOOLS_SYSINFO_CONFIG": str(path)}):
                self.assertEqual(collect_sysinfo()["config_state"], "loaded")
                self.assertEqual(
                    collect_sysinfo(path.with_name("missing"))["config_state"], "missing"
                )

    def test_cli_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = parser().parse_args(
                ["sysinfo", "--json", "--config", str(Path(temporary) / "missing")]
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(args.func(args), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["config_state"], "missing")
            self.assertIn("available_on_path", result["commands"][0])
