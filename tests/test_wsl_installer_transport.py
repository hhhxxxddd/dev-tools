from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class WslInstallerTransportTests(unittest.TestCase):
    def capture_installation(self, python: str | None = None) -> list[str]:
        installer = Path(__file__).resolve().parents[1] / "scripts/install-wsl.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "scripts.json"
            check = root / "transport.ps1"
            check.write_text(
                r"""param($Installer, $Log, $PythonPath)
$ErrorActionPreference = 'Stop'
$env:DEV_TOOLS_PYTHON = 'caller-windows-python.exe'
$global:fixtureWslScripts = [Collections.Generic.List[string]]::new()
function global:wsl.exe {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq '--list') { return 'Fixture-Distro' }
    if ($args -contains 'wslpath') { return '/fixture/controller source' }
    $carrier = [string]$args[-1]
    if ($carrier -notmatch "printf '%s' '([A-Za-z0-9+/=]+)' \| base64 -d") {
        throw 'Unexpected WSL script transport'
    }
    $global:fixtureWslScripts.Add([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Matches[1])))
}
$options = @{Distro='Fixture-Distro'; Language='en'}
if ($PythonPath) { $options.Python = $PythonPath }
& $Installer @options
[IO.File]::WriteAllText($Log, (ConvertTo-Json -InputObject $global:fixtureWslScripts), [Text.UTF8Encoding]::new($false))
""",
                encoding="utf-8",
            )
            command = [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(check),
                str(installer),
                str(log),
            ]
            if python is not None:
                command.append(python)
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(log.read_text(encoding="utf-8"))

    def test_windows_python_override_does_not_cross_into_wsl(self):
        scripts = self.capture_installation()
        self.assertEqual(len(scripts), 3)
        for script in scripts[:2]:
            self.assertTrue(script.startswith("unset DEV_TOOLS_PYTHON\n"))
            self.assertNotIn("caller-windows-python.exe", script)

    def test_explicit_wsl_python_is_transported_as_data(self):
        python = "/opt/runtime space/python'\";$(touch injected)"
        scripts = self.capture_installation(python)
        for script in scripts[:2]:
            match = re.search(
                r"export DEV_TOOLS_PYTHON=\$\(printf %s ([A-Za-z0-9+/=]+) \| base64 -d\)",
                script,
            )
            self.assertIsNotNone(match)
            self.assertEqual(base64.b64decode(match[1]).decode(), python)
            self.assertNotIn(python, script)


if __name__ == "__main__":
    unittest.main()
