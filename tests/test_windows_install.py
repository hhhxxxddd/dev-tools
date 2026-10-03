from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class WindowsInstallerTests(unittest.TestCase):
    def test_reinstall_preserves_personal_config_and_links_single_cli_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            scripts = repo / "scripts"
            scripts.mkdir(parents=True)
            installer = scripts / "install.ps1"
            shutil.copyfile(Path(__file__).parents[1] / "scripts/install.ps1", installer)
            cli = repo / "config/windows-cli-tools/mise.toml"
            cli.parent.mkdir(parents=True)
            cli.write_text('[tools]\nnode = "latest"\n', encoding="utf-8")
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
function mise { $global:LASTEXITCODE = 0 }
& $Installer
& $Installer
if ($env:MISE_GLOBAL_CONFIG_FILE -ne $Personal) { throw 'Personal override lost' }
$link = Get-Item -LiteralPath (Join-Path $UserRoot '.config/mise/conf.d/windows-cli-tools')
if ($link.LinkType -ne 'Junction') { throw 'Expected junction' }
$fragment = Join-Path $link.FullName 'mise.toml'
$content = [IO.File]::ReadAllText($fragment)
$source = Join-Path (Split-Path (Split-Path $Installer)) 'config/windows-cli-tools/mise.toml'
[IO.File]::WriteAllText($source, $content.Replace('latest', '24'))
if (-not [IO.File]::ReadAllText($fragment).Contains('24')) { throw 'CLI source drifted' }
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
            self.assertEqual(content.count("# >>> dev-tools >>>"), 1)
            self.assertNotIn("config\\windows-cli-tools\\mise.toml'", content)


if __name__ == "__main__":
    unittest.main()
