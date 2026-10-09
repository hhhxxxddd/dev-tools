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
from dev_tools.i18n import language_scope, render
from dev_tools.settings import SettingsError, render_settings
from dev_tools.sysinfo import collect_sysinfo, print_sysinfo


class SysinfoTests(unittest.TestCase):
    def test_missing_config_is_read_only_and_does_not_execute_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing.toml"
            with patch("subprocess.run", side_effect=AssertionError("must not execute")):
                result = collect_sysinfo(path)
            self.assertEqual(result["config_state"], "missing")
            self.assertEqual(result["directories"], [])
            self.assertFalse(path.exists())

    def test_tools_and_descriptions_drive_the_same_ordered_path_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text(
                render_settings(
                    {
                        "sysinfo": {
                            "tools": [
                                {
                                    "command": "first",
                                    "description": {"zh": "工具一", "en": "First tool"},
                                },
                                {"command": "second", "description": "Tool two"},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch("dev_tools.sysinfo.shutil.which", side_effect=["/bin/first", None]) as lookup,
                language_scope("en"),
            ):
                result = collect_sysinfo(path)
            self.assertEqual([call.args[0] for call in lookup.call_args_list], ["first", "second"])
            self.assertEqual(result["tools"][0]["description"], "First tool")
            self.assertEqual(
                result["commands"],
                [
                    {"command": "first", "available_on_path": True},
                    {"command": "second", "available_on_path": False},
                ],
            )

    def test_unknown_sensitive_fields_and_invalid_toml_never_leak(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            for content in (
                'token = "SECRET_TOKEN"',
                '[sysinfo]\ntools = [{command="git", token="SECRET_TOKEN"}]',
                'token = "SECRET_TOKEN',
            ):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(SettingsError) as error:
                    collect_sysinfo(path)
                self.assertNotIn("SECRET_TOKEN", render(error.exception))

    def test_descriptions_are_plain_text_and_paths_are_native(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            config = {
                "sysinfo": {
                    "directories": [{"command": "pro", "path": ".", "description": "Projects"}],
                    "tools": [{"command": "dev-tools", "description": "a\x1bb"}],
                }
            }
            # TOML represents the control character explicitly, then the reader sanitizes it.
            text = render_settings(config).replace("\\u001b", "\\u001B")
            path.write_text(text, encoding="utf-8")
            before = path.read_bytes()
            result = collect_sysinfo(path)
            self.assertEqual(result["tools"][0]["description"], "ab")
            self.assertEqual(result["directories"][0]["path"], str(path.parent.resolve()))
            self.assertTrue(result["directories"][0]["exists"])
            self.assertEqual(path.read_bytes(), before)
            path.write_text('[sysinfo]\ntools = [{command="cmd; echo SECRET"}]', encoding="utf-8")
            with self.assertRaises(SettingsError):
                collect_sysinfo(path)

    def test_disabled_sections_perform_no_probes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text("[sysinfo]\nsections = []\n", encoding="utf-8")
            with (
                patch(
                    "dev_tools.sysinfo.shutil.which", side_effect=AssertionError("no PATH lookup")
                ),
                patch("platform.system", side_effect=AssertionError("no system probe")),
                patch("pathlib.Path.is_dir", side_effect=AssertionError("no directory probe")),
            ):
                result = collect_sysinfo(path)
            self.assertEqual(result["system"], {})
            self.assertEqual(result["commands"], [])
            self.assertEqual(result["directories"], [])

    def test_show_missing_filters_tools_and_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text(
                render_settings(
                    {
                        "sysinfo": {
                            "show_missing": False,
                            "tools": [{"command": "absent"}],
                            "directories": [{"path": "absent"}],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("dev_tools.sysinfo.shutil.which", return_value=None):
                result = collect_sysinfo(path)
            self.assertEqual(result["commands"], [])
            self.assertEqual(result["directories"], [])

    def test_explicit_config_precedes_shared_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text("", encoding="utf-8-sig")
            with patch.dict(os.environ, {"DEV_TOOLS_CONFIG": str(path)}):
                self.assertEqual(collect_sysinfo()["config_state"], "loaded")
                self.assertEqual(
                    collect_sysinfo(path.with_name("missing"))["config_state"], "missing"
                )

    def test_cli_json_contract_and_language_independent_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            values = []
            for language in ("zh", "en"):
                path.write_text(f'language = "{language}"\n', encoding="utf-8")
                args = parser().parse_args(["sysinfo", "--json", "--config", str(path)])
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(args.func(args), 0)
                values.append(json.loads(output.getvalue()))
            self.assertEqual(values[0], values[1])
            self.assertIn("available_on_path", values[0]["commands"][0])
            with language_scope("en"), contextlib.redirect_stdout(io.StringIO()) as output:
                print_sysinfo(values[0])
            self.assertIn("System:", output.getvalue())
