from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dev_tools import __version__


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class WindowsEntrypointTests(unittest.TestCase):
    def test_bound_python_runs_without_mise_and_install_override_is_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = Path(__file__).resolve().parents[1]
            deployment = root / "deployment"
            scripts = deployment / "scripts"
            scripts.mkdir(parents=True)
            for name in ("dev-tools.ps1", "dev-tools-messages.ps1"):
                shutil.copyfile(repository / "scripts" / name, scripts / name)
            shutil.copytree(repository / "src/dev_tools", deployment / "src/dev_tools")
            binding = deployment / ".host-python.win"
            binding.write_text(str(Path(sys.executable).resolve()) + "\n", encoding="utf-8")
            wsl_binding = deployment / ".host-python"
            wsl_binding.write_text("/opt/shared/python/bin/python3\n", encoding="utf-8")
            config = root / "config.toml"
            config.write_text('language = "en"\n', encoding="utf-8")
            check = root / "check.ps1"
            check.write_text(
                """param($Entrypoint, $Config, $FallbackPythonRoot)
$ErrorActionPreference = 'Stop'
$env:DEV_TOOLS_CONFIG = $Config
$env:DEV_TOOLS_PYTHON = 'missing-install-override.exe'
function mise {
  if (-not $FallbackPythonRoot) { throw 'Bound startup must not invoke mise' }
  if ($args -notcontains 'where') { throw 'Startup may only query mise' }
  $global:LASTEXITCODE = 0
  $FallbackPythonRoot
}
& $Entrypoint --version
""",
                encoding="utf-8",
            )
            command = [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(check),
                str(scripts / "dev-tools.ps1"),
                str(config),
            ]
            version = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=30,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertEqual(version.stdout.strip(), "dev-tools " + __version__)
            binding.write_text(str(deployment / "missing/python.exe"), encoding="utf-8")
            missing = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=30,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("existing absolute Windows path", missing.stderr)
            # A WSL source binding cannot replace the native Windows fallback.
            binding.unlink()
            fallback = subprocess.run(
                [*command, sys.prefix],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=30,
            )
            self.assertEqual(fallback.returncode, 0, fallback.stderr)
            self.assertEqual(fallback.stdout.strip(), "dev-tools " + __version__)
            self.assertEqual(wsl_binding.read_text(), "/opt/shared/python/bin/python3\n")

    def test_native_flags_and_scan_cannot_import_project_python_code(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            deployment = Path(temporary) / "controller"
            shutil.copytree(repository / "scripts", deployment / "scripts")
            shutil.copytree(repository / "src/dev_tools", deployment / "src/dev_tools")
            (deployment / ".host-python.win").write_text(
                str(Path(sys.executable).resolve()) + "\n", encoding="utf-8"
            )
            script = deployment / "scripts/dev-tools.ps1"
            root = Path(temporary) / "project"
            root.mkdir()
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
