from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from dev_tools import __version__
from dev_tools.cli import cmd_init, main, parser


class CliTests(unittest.TestCase):
    def test_init_does_not_write_a_partial_config_when_versions_are_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                '{"engines":{"node":"<20"},"packageManager":"pnpm@10.0.0"}'
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cmd_init(argparse.Namespace(path=str(root), dry_run=False, json=True))
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["action"], "unresolved")
            self.assertFalse((root / "mise.toml").exists())

    def test_version_matches_project_metadata(self) -> None:
        metadata = tomllib.loads(
            (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        )

        self.assertEqual(__version__, "0.4.0")
        self.assertEqual(metadata["project"]["version"], __version__)

    def test_version_flag_prints_version(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_context:
            parser().parse_args(["--version"])

        self.assertEqual(exit_context.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "dev-tools 0.4.0")

    def test_init_creates_parseable_mise_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".nvmrc").write_text("22\n", encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = cmd_init(argparse.Namespace(path=str(root), dry_run=False, json=True))

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["action"], "created")
            self.assertEqual((root / "mise.toml").read_text(encoding="utf-8"), payload["content"])

    def test_existing_config_is_preserved_even_when_legacy_files_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "apps/a").mkdir(parents=True)
            (root / "apps/b").mkdir(parents=True)
            config = root / "mise.toml"
            config.write_text('[tools]\nnode = "22"\n', encoding="utf-8")
            (root / "apps/a/.nvmrc").write_text("20\n", encoding="utf-8")
            (root / "apps/b/.nvmrc").write_text("22\n", encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = cmd_init(argparse.Namespace(path=str(root), dry_run=False, json=True))

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["action"], "preserved")

    def test_native_commands_use_the_same_parser(self) -> None:
        for environment in ("win", "wsl"):
            args = parser().parse_args(
                ["--env", environment, "prepare", "demo", "--dry-run", "--json"]
            )
            self.assertEqual(args.name, "demo")
            self.assertTrue(args.dry_run)
            self.assertTrue(args.json)
            self.assertEqual(args.func.__module__, "dev_tools.projects.cli")

    def test_environment_defaults_to_native_and_removed_forms_are_rejected(self):
        args = parser().parse_args(["list"])
        self.assertEqual(args.env, "win" if os.name == "nt" else "wsl")
        for arguments in (
            ["cli", "status"],
            ["project", "list"],
            ["compile", "demo"],
            ["--en", "wsl", "list"],
            ["list", "--en", "wsl"],
            ["--env", "windows", "list"],
            ["--env", "local", "list"],
            ["doctor", "demo"],
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                parser().parse_args(arguments)
            self.assertEqual(error.exception.code, 2)

    def test_environment_flags_before_and_after_flat_command(self):
        for environment in ("win", "wsl"):
            for flags in (["-e", environment], ["--env=" + environment], ["-e" + environment]):
                for tokens in ([*flags, "status", "demo"], ["status", "demo", *flags]):
                    args = parser().parse_args(tokens)
                    self.assertEqual(args.env, environment)
                    self.assertEqual(args.name, "demo")

    def test_optional_name_keeps_logs_service_and_rename_destination_unambiguous(self):
        for tokens, name, service in (
            (["logs", "api"], None, "api"),
            (["logs", "demo", "api"], "demo", "api"),
        ):
            args = parser().parse_args(tokens)
            self.assertEqual((args.name, args.service), (name, service))
        args = parser().parse_args(["rename", "new-name"])
        self.assertIsNone(args.name)
        self.assertEqual(args.new_name, "new-name")

    def test_help_lists_every_public_command_and_hides_worker(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            main(["help"])
        self.assertEqual(stopped.exception.code, 0)
        help_text = output.getvalue()
        for command in (
            "init",
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
            "scan",
            "show",
            "sync",
            "rename",
            "report",
            "sysinfo",
            "help",
        ):
            self.assertRegex(help_text, rf"(?m)^  {command}\s+")
        self.assertNotIn("_worker", help_text)
        self.assertNotIn("project COMMAND", help_text)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            main(["help", "unregister"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--purge", output.getvalue())
        self.assertIn("[项目名]", output.getvalue())

    def test_machine_commands_reject_project_environment_instead_of_ignoring_it(self):
        for command in ("sysinfo", "report"):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as stopped,
            ):
                main(["-e", "wsl", command])
            self.assertEqual(stopped.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
