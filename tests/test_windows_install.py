from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class WindowsInstallerTests(unittest.TestCase):
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
