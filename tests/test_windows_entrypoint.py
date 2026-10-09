from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dev_tools import __version__


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class WindowsEntrypointTests(unittest.TestCase):
    def test_native_flags_and_scan_cannot_import_project_python_code(self):
        script = Path(__file__).resolve().parents[1] / "scripts/dev-tools.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("socket.py", "subprocess.py", "sitecustomize.py"):
                (root / name).write_text(
                    'raise RuntimeError("project code must never execute during scanning")\n'
                )
            (root / ".nvmrc").write_text("22")
            command = ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script)]
            version = subprocess.run(
                [*command, "--version"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=30,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertEqual(version.stdout.strip(), "dev-tools " + __version__)
            scan = subprocess.run(
                [*command, "scan", str(root), "-e", "win", "--json"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=30,
            )
            self.assertEqual(scan.returncode, 0, scan.stderr)
            self.assertEqual(json.loads(scan.stdout)["schema_version"], 3)
            help_result = subprocess.run(
                [*command, "-e", "wsl", "--help"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=30,
            )
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("dev-tools -e wsl list", help_result.stdout)
            self.assertNotIn("dev-tools cli ", help_result.stdout)
