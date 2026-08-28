from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from dev_tools.cli import cmd_init


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


if __name__ == "__main__":
    unittest.main()
