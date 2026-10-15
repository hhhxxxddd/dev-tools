from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.cli_errors import TargetError
from dev_tools.runtimes.platforms.windows_interop import powershell_entry, windows_mounts


class WindowsInteropTests(unittest.TestCase):
    def test_path_entry_is_used_without_probing_drives(self):
        with (
            patch(
                "dev_tools.runtimes.platforms.windows_interop.shutil.which",
                return_value="/native/pwsh.exe",
            ),
            patch("dev_tools.runtimes.platforms.windows_interop.windows_mounts") as mounts,
        ):
            self.assertEqual(powershell_entry(), "/native/pwsh.exe")
        mounts.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Linux mount paths")
    def test_drive_mounts_are_read_without_assuming_mount_root_or_drive_letter(self):
        content = "drivers /usr/lib/wsl/drivers 9p ro,aname=drivers 0 0\nZ:\\134 /windows\\040disks/z 9p rw,aname=drvfs 0 0\nR: /native/r drvfs rw 0 0\n/dev/sda / ext4 rw 0 0\n"
        with patch("pathlib.Path.read_text", return_value=content):
            self.assertEqual(windows_mounts(), [Path("/windows disks/z"), Path("/native/r")])

    def test_restricted_root_path_finds_powershell_on_mounted_volume(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary)
            candidate = mount / "Program Files/PowerShell/7/pwsh.exe"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"fixture")
            candidate.chmod(0o755)
            with (
                patch(
                    "dev_tools.runtimes.platforms.windows_interop.shutil.which", return_value=None
                ),
                patch(
                    "dev_tools.runtimes.platforms.windows_interop.windows_mounts",
                    return_value=[mount],
                ),
                patch("dev_tools.runtimes.platforms.windows_interop.subprocess.run") as execute,
            ):
                self.assertEqual(powershell_entry(), str(candidate))
            execute.assert_not_called()

    def test_scoop_or_portable_powershell_uses_native_readonly_lookup_without_profiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary)
            bootstrap = mount / "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            candidate = mount / "user-controlled-space/pwsh.exe"
            bootstrap.parent.mkdir(parents=True)
            candidate.parent.mkdir()
            for file in (bootstrap, candidate):
                file.write_bytes(b"fixture")
                file.chmod(0o755)
            responses = [
                subprocess.CompletedProcess([], 0, "Z:\\portable path\\pwsh.exe\n", ""),
                subprocess.CompletedProcess([], 0, str(candidate) + "\n", ""),
            ]
            with (
                patch(
                    "dev_tools.runtimes.platforms.windows_interop.shutil.which", return_value=None
                ),
                patch(
                    "dev_tools.runtimes.platforms.windows_interop.windows_mounts",
                    return_value=[mount],
                ),
                patch(
                    "dev_tools.runtimes.platforms.windows_interop.subprocess.run",
                    side_effect=responses,
                ) as execute,
            ):
                self.assertEqual(powershell_entry(), str(candidate))
            first, second = execute.call_args_list
            self.assertIn("-NoProfile", first.args[0])
            self.assertIn("-NonInteractive", first.args[0])
            self.assertIn("-CommandType Application", first.args[0][-1])
            self.assertNotIn("-File", first.args[0])
            self.assertEqual(second.args[0][1:], ["-u", "Z:\\portable path\\pwsh.exe"])

    def test_missing_powershell_is_specific_and_does_not_change_environment(self):
        with (
            patch("dev_tools.runtimes.platforms.windows_interop.shutil.which", return_value=None),
            patch("dev_tools.runtimes.platforms.windows_interop.windows_mounts", return_value=[]),
            patch.dict(os.environ, {"PATH": "restricted", "WSL_INTEROP": "unchanged"}),
            self.assertRaises(TargetError) as failed,
        ):
            powershell_entry()
        self.assertEqual(failed.exception.details["reason"], "powershell_missing")
        self.assertIn("root/sudo", str(failed.exception))
