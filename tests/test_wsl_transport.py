from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class WslCheckTransportTests(unittest.TestCase):
    def test_check_is_forwarded_without_root_keepalive_or_bootstrap(self):
        relay = Path(__file__).resolve().parents[1] / "scripts/dev-tools-wsl.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "calls.json"
            harness = root / "relay.ps1"
            harness.write_text(
                r"""param($Relay, $Log)
$ErrorActionPreference = 'Stop'
$global:fixtureWslCalls = [Collections.Generic.List[object]]::new()
$global:fixtureWslLog = $Log
function global:Get-CimInstance { throw 'Read-only check tried to start a keepalive process' }
function global:New-CimInstance { throw 'Read-only check tried to start a keepalive process' }
function global:wsl.exe {
    $global:fixtureWslCalls.Add([string[]]$args)
    [IO.File]::WriteAllText($global:fixtureWslLog, (ConvertTo-Json -Depth 5 -InputObject $global:fixtureWslCalls), [Text.UTF8Encoding]::new($false))
    $global:LASTEXITCODE = 0
    if ($args -contains 'wslpath') { return '/fixture/source' }
}
& $Relay -Distro Fixture-Distro -Language en check fixture-project --json
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(harness),
                    str(relay),
                    str(log),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(len(calls), 2)
            self.assertIn("wslpath", calls[0])
            self.assertEqual(calls[1][-4:], ["wsl", "check", "fixture-project", "--json"])
            self.assertNotIn("-u", calls[1][: calls[1].index("--exec")])
            for arguments in calls:
                self.assertNotIn("sudo", arguments)
                self.assertNotIn("install", arguments)


if __name__ == "__main__":
    unittest.main()
