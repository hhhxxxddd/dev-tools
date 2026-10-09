from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class WindowsRuntimeTests(unittest.TestCase):
    def test_native_stop_checks_identity_and_accepts_an_already_terminated_process(self):
        script = Path(__file__).parents[1] / "scripts/dev-tools-native.ps1"
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                harness = root / "stop.ps1"
                harness.write_text(
                    """param($Native, $ChildId, $RequestPath)
$ErrorActionPreference = 'Stop'
$global:FixturePid = [int]$ChildId
$candidate = Get-Process -Id $global:FixturePid
$ticks = $candidate.StartTime.ToUniversalTime().Ticks
@{pid=$global:FixturePid; start_ticks=$ticks+1} | ConvertTo-Json | Set-Content $RequestPath
function taskkill.exe { throw 'Mismatched identity must not be terminated' }
$answer = & $Native -Action stop -RequestPath $RequestPath | ConvertFrom-Json
if ($answer.active) { throw 'Wrong identity reported active' }
if (-not (Get-Process -Id $global:FixturePid)) { throw 'Unrelated process was killed' }
@{pid=$global:FixturePid; start_ticks=$ticks} | ConvertTo-Json | Set-Content $RequestPath
function taskkill.exe { $global:LASTEXITCODE = 0 }
try {
    $answer = & $Native -Action stop -RequestPath $RequestPath
    throw 'Stop claimed success while the worker was still alive'
} catch {
    if (-not $_.Exception.Message.Contains('无法停止本机进程')) { throw }
}
function taskkill.exe {
    Stop-Process -Id $global:FixturePid -Force
    $global:LASTEXITCODE = 128
    'A descendant already exited'
}
$answer = & $Native -Action stop -RequestPath $RequestPath | ConvertFrom-Json
if ($answer.active) { throw 'Terminated process reported active' }
""",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [
                        "pwsh",
                        "-NoProfile",
                        "-File",
                        str(harness),
                        str(script),
                        str(child.pid),
                        str(root / "identity.json"),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    def test_real_native_lifecycle_and_recovery(self):
        from tests.integration.lifecycle_smoke import smoke

        result = smoke()
        self.assertEqual(result["schema_version"], 3)
        self.assertFalse(result["native_workspace"])


if __name__ == "__main__":
    unittest.main()
