from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class WslCheckTransportTests(unittest.TestCase):
    def test_auxiliary_routing_keeps_queries_unprivileged_and_controller_identity(self):
        relay = Path(__file__).resolve().parents[1] / "scripts/dev-tools-wsl.ps1"
        for arguments, root_required in (
            (["config", "edit"], False),
            (["self", "status", "--json"], False),
            (["self", "update", "--source", "/tmp/source"], True),
            (["report", "--no-refresh", "--output", "new 报告.json"], False),
            (["report", "--output=new 报告.json"], False),
            (["ssh", "show", "--json", "--", "服务器"], False),
            (["ssh", "connect", "--", "server"], False),
            (["ssh", "list", "--", "--env=win"], False),
            (["ssh", "list", "--", "$(literal) & 'quoted'"], False),
        ):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                log = directory / "calls.json"
                harness = directory / "relay.ps1"
                quote = lambda value: "'" + str(value).replace("'", "''") + "'"
                harness.write_text(
                    """$ErrorActionPreference='Stop'
$global:calls=[Collections.Generic.List[object]]::new()
function global:Get-CimInstance { throw 'unexpected keepalive' }
function global:wsl.exe {
    $global:calls.Add([string[]]$args)
    [IO.File]::WriteAllText(LOG, (ConvertTo-Json -Depth 5 -InputObject $global:calls))
    $global:LASTEXITCODE=0
    if ($args -contains 'wslpath') { return '/mapped/new 报告.json' }
    if ($args -contains 'id') { return 'fixture-user' }
}
& RELAY -Distro Fixture -Language en ARGUMENTS
""".replace("LOG", quote(log))
                    .replace("RELAY", quote(relay))
                    .replace(
                        "ARGUMENTS",
                        "-ArgumentsPayload "
                        + quote(
                            base64.b64encode(
                                json.dumps(arguments, ensure_ascii=False).encode("utf-8")
                            ).decode("ascii")
                        ),
                    ),
                    encoding="utf-8",
                )
                result = subprocess.run(
                    ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(harness)],
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = json.loads(log.read_text(encoding="utf-8"))
                invocation = calls[-1]
                self.assertEqual("root" in invocation[: invocation.index("--exec")], root_required)
                if root_required:
                    self.assertIn("SUDO_USER=fixture-user", invocation)
                if arguments[0] == "report":
                    self.assertTrue(
                        any(value.endswith("/mapped/new 报告.json") for value in invocation)
                    )
                    self.assertEqual(calls[0][-2], "-u")
                else:
                    self.assertEqual(invocation[-len(arguments) :], arguments)

    def test_check_is_forwarded_without_root_keepalive_or_bootstrap(self):
        relay = Path(__file__).resolve().parents[1] / "scripts/dev-tools-wsl.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "calls.json"
            cwd = root / "项目 with spaces"
            cwd.mkdir()
            harness = root / "relay.ps1"
            harness.write_text(
                r"""param($Relay, $Log)
$ErrorActionPreference = 'Stop'
$global:fixtureWslCalls = [Collections.Generic.List[object]]::new()
$global:fixtureWslLog = $Log
function global:Get-CimInstance { throw 'Read-only check tried to start a keepalive process' }
function global:New-CimInstance { throw 'Read-only check tried to start a keepalive process' }
function global:wsl.exe {
    if ($env:WSL_UTF8 -ne '1') { throw 'WSL diagnostics must use UTF-8' }
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
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-4:], ["wsl", "check", "fixture-project", "--json"])
            self.assertTrue(any(value.startswith("DEV_TOOLS_TERMINAL=") for value in calls[0]))
            self.assertNotIn("-u", calls[0][: calls[0].index("--exec")])
            self.assertEqual(Path(calls[0][calls[0].index("--cd") + 1]).resolve(), cwd.resolve())
            for arguments in calls:
                self.assertNotIn("sudo", arguments)
                self.assertNotIn("install", arguments)
                self.assertNotIn("wslpath", arguments)


if __name__ == "__main__":
    unittest.main()
