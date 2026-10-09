from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.cli import main
from dev_tools.i18n import join_messages, language_scope, message, render
from dev_tools.projects.config import parse_project
from dev_tools.projects.models import ProjectError
from dev_tools.settings import (
    SettingsError,
    default_path,
    edit_settings,
    load_settings,
    render_settings,
    resolve_editor,
    split_config,
)


class SettingsTests(unittest.TestCase):
    def test_workers_ignore_interactive_preferences_and_diagnostics_survive_serialization(self):
        with (
            patch(
                "dev_tools.cli.load_settings",
                side_effect=AssertionError("worker must not read preferences"),
            ),
            patch("dev_tools.projects.cli.run", return_value=0),
        ):
            self.assertEqual(self.invoke("_worker", "demo:__watch")[0], 0)
        detail = message(
            "project preparation is unresolved: {unresolved}",
            unresolved=join_messages(
                "; ",
                [
                    message("root mise configuration is missing; run dev-tools init"),
                    message("required tool has no version declaration"),
                ],
            ),
        )
        saved = json.loads(json.dumps(detail.as_dict()))
        with language_scope("zh"):
            self.assertIn("缺少根级 mise 配置", render(saved))
            self.assertNotIn("required tool", render(saved))
        with language_scope("en"):
            self.assertEqual(render(saved), str(detail))

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(list(arguments))
        return stopped.exception.code, stdout.getvalue(), stderr.getvalue()

    def test_missing_config_is_read_only_and_native(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            with patch.dict(
                os.environ,
                {"DEV_TOOLS_CONFIG": str(path), "DEV_TOOLS_LANG": "en", "VISUAL": "", "EDITOR": ""},
            ):
                code, output, _ = self.invoke("config", "--json")
                data = json.loads(output)
                self.assertEqual(code, 0)
                self.assertEqual(data["state"], "missing")
                self.assertEqual(data["settings"]["language"], "zh")
                self.assertEqual(
                    data["settings"]["editor"], ["notepad.exe"] if os.name == "nt" else ["vi"]
                )
                self.assertFalse(path.exists())
                self.assertEqual(load_settings().path, path.resolve())
        self.assertNotIn("Projects", str(default_path()))

    def test_precedence_rendering_and_list_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, explicit = root / "environment.toml", root / "explicit.toml"
            environment.write_text('language = "en"\n', encoding="utf-8")
            explicit.write_text(
                '[sysinfo]\ntools = []\n[report]\ncollectors = ["git"]\n', encoding="utf-8"
            )
            with patch.dict(os.environ, {"DEV_TOOLS_CONFIG": str(environment)}):
                self.assertEqual(load_settings().language, "en")
                settings = load_settings(explicit)
                self.assertEqual(settings.language, "zh")
                self.assertEqual(settings.data["sysinfo"]["tools"], [])
                self.assertEqual(settings.data["report"]["collectors"], ["git"])
                self.assertEqual(settings.origins["report.collectors"], str(explicit.resolve()))
                self.assertEqual(tomllib.loads(render_settings(settings.data)), settings.data)

    def test_bad_types_unknown_fields_and_syntax_never_leak_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            for source in (
                'secret = "SECRET_VALUE"',
                "[report]\ntimeout = true",
                "[report]\nmax_depth = -1",
                'language = "es"',
                'editor = "SECRET_VALUE"',
                'language = "en"\n[sysinfo]\nshow_missing = 1',
                'secret = "SECRET_VALUE',
            ):
                path.write_text(source, encoding="utf-8")
                with self.assertRaises(SettingsError) as error:
                    load_settings(path)
                self.assertNotIn("SECRET_VALUE", render(error.exception))
                code, output, stderr = self.invoke("--config", str(path), "config", "check")
                self.assertEqual(code, 1)
                self.assertNotIn("SECRET_VALUE", output + stderr)
                self.assertEqual(self.invoke("--config", str(path), "help")[0], 0)

    def test_edit_preserves_file_and_passes_paths_as_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "space & quote'" / "config.toml"
            path.parent.mkdir()
            source = 'language = "en"\neditor = ["my-editor", "--wait", "argument with spaces"]\n'
            path.write_text(source, encoding="utf-8")
            with (
                patch("dev_tools.settings.shutil.which", return_value="/editor"),
                patch(
                    "dev_tools.settings.subprocess.run", return_value=SimpleNamespace(returncode=0)
                ) as runner,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                edit_settings(load_settings(path))
            self.assertEqual(
                runner.call_args.args[0][-4:],
                ["/editor", "--wait", "argument with spaces", str(path.resolve())],
            )
            self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_edit_creates_template_and_bad_saved_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"

            def save_bad(command, **_):
                path.write_text('language = "unsupported"\n', encoding="utf-8")
                return SimpleNamespace(returncode=0)

            with (
                patch.dict(os.environ, {"EDITOR": "", "VISUAL": ""}),
                patch("dev_tools.settings.shutil.which", return_value="/editor"),
                patch("dev_tools.settings.subprocess.run", side_effect=save_bad),
                self.assertRaises(SettingsError),
            ):
                edit_settings(load_settings(path))
            self.assertTrue(path.exists())

    def test_editor_configuration_precedes_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('editor = ["configured", "--wait"]\n', encoding="utf-8")
            with patch.dict(
                os.environ, {"VISUAL": 'visual --flag "two words"', "EDITOR": "fallback"}
            ):
                self.assertEqual(resolve_editor(load_settings(path))[0], ["configured", "--wait"])
                self.assertEqual(
                    resolve_editor(load_settings(path.with_name("missing")))[0],
                    ["visual", "--flag", "two words"],
                )

    def test_config_flags_do_not_reach_project_transport(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('language = "en"\n[wsl]\ndistro = "TestDistro"\n', encoding="utf-8")
            for arguments in (
                ["--config", str(path), "-e", "wsl", "list"],
                ["list", "--config=" + str(path), "-ewsl"],
            ):
                with patch("dev_tools.runtimes.router.forward_remote", return_value=0) as route:
                    self.assertEqual(self.invoke(*arguments)[0], 0)
                self.assertEqual(route.call_args.args, ("wsl", ["list"]))
                self.assertEqual(route.call_args.kwargs["settings"].distro, "TestDistro")
            self.assertEqual(
                split_config(["scan", "--", "--config=source"]),
                (None, ["scan", "--", "--config=source"]),
            )

    def test_language_covers_all_help_and_errors_but_keeps_json_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('language = "en"\n', encoding="utf-8")
            for topic in (
                None,
                "config",
                "init",
                "scan",
                "register",
                "list",
                "prepare",
                "start",
                "stop",
                "restart",
                "status",
                "build",
                "logs",
                "unregister",
                "show",
                "sync",
                "rename",
                "sysinfo",
                "report",
                "help",
                "self",
            ):
                args = ["--config", str(path), "help", *([topic] if topic else [])]
                code, output, _ = self.invoke(*args)
                self.assertEqual(code, 0)
                self.assertNotRegex(output, r"[\u4e00-\u9fff]")
            for source, expected in (("zh", "无法识别"), ("en", "unrecognized")):
                path.write_text(f'language = "{source}"\n', encoding="utf-8")
                code, _, error = self.invoke("--config", str(path), "sysinfo", "--lang", "en")
                self.assertEqual(code, 2)
                self.assertIn(expected, error)
            with language_scope("en"):
                with self.assertRaises(ProjectError) as error:
                    parse_project({"schema": 7}, "windows")
                self.assertIn("requires schema = 1", render(error.exception))
            with language_scope("zh"):
                self.assertIn("必须声明", render(error.exception))
            detail = message("command exited with code {code}", code=3).as_dict()
            with language_scope("zh"):
                self.assertEqual(render(detail), "命令退出码为 3")
            with language_scope("en"):
                self.assertEqual(render(detail), "command exited with code 3")

    @unittest.skipIf(os.name == "nt", "native WSL preferences")
    def test_sudo_uses_invoking_users_preferences_and_editor(self):
        from dev_tools.runtimes.platforms.preferences import editor_identity, user_config_directory

        user = SimpleNamespace(
            pw_dir="/home/test-caller", pw_uid=1001, pw_gid=1001, pw_name="test-caller"
        )
        with (
            patch.dict(os.environ, {"SUDO_USER": "test-caller", "XDG_CONFIG_HOME": ""}),
            patch("os.geteuid", return_value=0),
            patch("pwd.getpwnam", return_value=user),
        ):
            self.assertEqual(user_config_directory(), Path("/home/test-caller/.config"))
            self.assertEqual(
                editor_identity(["vi", "file"]), ["sudo", "-u", "test-caller", "--", "vi", "file"]
            )
