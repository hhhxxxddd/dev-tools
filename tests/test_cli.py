from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.cli import cmd_init, cmd_prepare


class CliTests(unittest.TestCase):
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

    def test_prepare_installs_only_from_existing_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mise.toml").write_text('[tools]\nnode = "22"\n', encoding="utf-8")

            with (
                patch("dev_tools.cli.shutil.which", return_value="/usr/bin/mise"),
                patch("dev_tools.cli.subprocess.run") as run,
            ):
                run.return_value.returncode = 0
                code = cmd_prepare(argparse.Namespace(path=str(root), dry_run=False))

            self.assertEqual(code, 0)
            command, = run.call_args.args
            self.assertEqual(
                command,
                ["mise", "--yes", "-C", str(root.resolve()), "install"],
            )
            self.assertFalse(run.call_args.kwargs["check"])
            self.assertEqual(run.call_args.kwargs["env"]["MISE_GLOBAL_CONFIG_FILE"], os.devnull)

    def test_prepare_refuses_project_without_mise_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                code = cmd_prepare(argparse.Namespace(path=temporary, dry_run=False))

            self.assertEqual(code, 1)
            self.assertIn("dev-tools project init", error.getvalue())


if __name__ == "__main__":
    unittest.main()
