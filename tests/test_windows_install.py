from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dev_tools.release import REPOSITORY, build


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class WindowsInstallerTests(unittest.TestCase):
    def test_scoop_hooks_migrate_only_owned_profile_blocks_and_reject_live_workers(self):
        import zipfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package"
            archive = build(REPOSITORY, root / "dist")["archive"]
            with zipfile.ZipFile(archive) as payload:
                payload.extractall(package)
            check = root / "check.ps1"
            check.write_text(
                """param($Package, $Root)
$ErrorActionPreference = 'Stop'
$env:DEV_TOOLS_STATE_ROOT = Join-Path $Root 'state'
$env:LOCALAPPDATA = Join-Path $Root 'local'
$env:USERPROFILE = Join-Path $Root 'user'
$profileFile = Join-Path $Root 'Documents/PowerShell/profile.ps1'
$legacyProfile = Join-Path $Root 'Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1'
New-Item -ItemType Directory -Path (Split-Path $profileFile), (Split-Path $legacyProfile) -Force | Out-Null
$PROFILE = [pscustomobject]@{CurrentUserCurrentHost = $profileFile; CurrentUserAllHosts = $profileFile}
[IO.File]::WriteAllText($profileFile, "# personal`n# >>> dev-tools >>>`nfunction dev-tools {} `n# <<< dev-tools <<<`n# retained`n")
[IO.File]::WriteAllText($legacyProfile, [IO.File]::ReadAllText($profileFile))
$global:HostInstalls = 0
function mise {
  if ($args -notcontains '--no-config' -or $args[-1] -ne 'python@3.14.8') { throw 'Unexpected runtime installation' }
  $global:HostInstalls++
  $global:LASTEXITCODE = 0
}
$hook = Join-Path $Package 'scripts/scoop.ps1'
& $hook -Action install
$content = [IO.File]::ReadAllText($profileFile)
if ($content -ne "# personal`n# retained`n") { throw 'Profile migration removed unrelated content' }
if ([IO.File]::ReadAllText($legacyProfile) -ne $content) { throw 'Legacy profile still shadows the shim' }
& $hook -Action uninstall
if ($global:HostInstalls -ne 1) { throw 'Uninstall installed a runtime' }
$native = Join-Path $env:DEV_TOOLS_STATE_ROOT 'demo/native'
New-Item -ItemType Directory -Path $native -Force | Out-Null
$process = Get-Process -Id $PID
@{pid=$PID; start_ticks=$process.StartTime.ToUniversalTime().Ticks} | ConvertTo-Json | Set-Content (Join-Path $native 'web.json')
$blocked = $false
try { & $hook -Action check } catch { if ($_.Exception.Message -like '*dev-tools -e win stop*') { $blocked = $true } else { throw } }
if (-not $blocked) { throw 'Active worker was ignored' }
if (-not (Test-Path -LiteralPath (Join-Path $native 'web.json'))) { throw 'Native state removed' }
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(check),
                    str(package),
                    str(root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_install_and_uninstall_only_manage_host_and_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            scripts = repo / "scripts"
            scripts.mkdir(parents=True)
            installer = scripts / "install.ps1"
            shutil.copyfile(Path(__file__).parents[1] / "scripts/install.ps1", installer)
            shutil.copyfile(
                Path(__file__).parents[1] / "scripts/uninstall.ps1", scripts / "uninstall.ps1"
            )
            personal = root / "personal.toml"
            personal.write_text('[tools]\njava = "25"\n', encoding="utf-8")
            profile = root / "profile.ps1"
            profile.write_text(
                "$env:MISE_GLOBAL_CONFIG_FILE = '" + str(personal).replace("'", "''") + "'\n",
                encoding="utf-8",
            )
            bootstrap = root / "check.ps1"
            bootstrap.write_text(
                """param($Installer, $UserRoot, $Personal, $ProfileFile)
$ErrorActionPreference = 'Stop'
$env:USERPROFILE = $UserRoot
$env:MISE_GLOBAL_CONFIG_FILE = $Personal
$PROFILE = [pscustomobject]@{CurrentUserCurrentHost = $ProfileFile}
$global:HostInstalls = 0
function mise {
    if ($args[-2] -ne 'install' -or $args[-1] -ne 'python@3.14.8' -or $args -notcontains '--no-config') {
        throw 'Installer must only install its explicit private Python host'
    }
    $global:HostInstalls++
    $global:LASTEXITCODE = 0
}
& $Installer
& $Installer
if ($env:MISE_GLOBAL_CONFIG_FILE -ne $Personal) { throw 'Personal override lost' }
if ($global:HostInstalls -ne 2) { throw 'Unexpected host install count' }
if (Test-Path -LiteralPath (Join-Path $UserRoot '.config/mise')) { throw 'Installer wrote global mise configuration' }
$profileContent = [IO.File]::ReadAllText($ProfileFile)
if ([regex]::Matches($profileContent, '# >>> dev-tools >>>').Count -ne 1) { throw 'Duplicate entrypoint' }
& (Join-Path (Split-Path $Installer) 'uninstall.ps1')
if ($env:MISE_GLOBAL_CONFIG_FILE -ne $Personal) { throw 'Uninstaller changed personal configuration' }
if ([IO.File]::ReadAllText($ProfileFile).Contains('# >>> dev-tools >>>')) { throw 'Uninstaller left entrypoint' }
if ($global:HostInstalls -ne 2) { throw 'Uninstaller touched runtime installations' }
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoProfile",
                    "-File",
                    str(bootstrap),
                    str(installer),
                    str(root / "user"),
                    str(personal),
                    str(profile),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(personal.read_text(encoding="utf-8"), '[tools]\njava = "25"\n')
            content = profile.read_text(encoding="utf-8")
            self.assertIn(str(personal), content)
            self.assertNotIn("# >>> dev-tools >>>", content)


if __name__ == "__main__":
    unittest.main()
