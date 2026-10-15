"""Find native Windows PowerShell from WSL, including root's restricted PATH."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ...cli_errors import TargetError
from ...i18n import message


def windows_mounts() -> list[Path]:
    """Use actual drive mounts rather than assuming /mnt/c or a Windows username."""
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    mounts = []
    for line in lines:
        fields = line.split()
        if len(fields) < 4:
            continue
        source, target, filesystem = fields[:3]
        source = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), source)
        target = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), target)
        if filesystem in {"drvfs", "9p"} and re.fullmatch(r"[A-Za-z]:[\\/]?", source):
            path = Path(target)
            if path.is_absolute() and path not in mounts:
                mounts.append(path)
    return mounts


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _from_windows_path(value: str) -> Path | None:
    translator = shutil.which("wslpath") or "/usr/bin/wslpath"
    result = subprocess.run(
        [translator, "-u", value],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=5,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    return path if path.is_absolute() and _executable(path) else None


def powershell_entry() -> str:
    found = shutil.which("pwsh.exe")
    if found:
        return found
    mounts = windows_mounts()
    for mount in mounts:
        for version in ("7", "7-lts"):
            candidate = mount / "Program Files/PowerShell" / version / "pwsh.exe"
            if _executable(candidate):
                return str(candidate)
    # Portable/Scoop installations may only be discoverable through Windows PATH
    # or the PowerShell Core registry. The inbox shell is only a read-only locator.
    locate = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        "$entry=Get-Command pwsh.exe -CommandType Application -ErrorAction SilentlyContinue; "
        "if($entry){[Console]::WriteLine($entry.Source);exit 0}; "
        "$versions=Get-ChildItem 'HKLM:\\SOFTWARE\\Microsoft\\PowerShellCore\\InstalledVersions' -ErrorAction SilentlyContinue; "
        "foreach($version in $versions){"
        "$location=(Get-ItemProperty -LiteralPath $version.PSPath).InstallLocation; "
        "if($location){$path=Join-Path $location 'pwsh.exe'; "
        "if(Test-Path -LiteralPath $path -PathType Leaf){[Console]::WriteLine($path);exit 0}}}; exit 1"
    )
    for mount in mounts:
        bootstrap = mount / "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        if not _executable(bootstrap):
            continue
        try:
            result = subprocess.run(
                [str(bootstrap), "-NoProfile", "-NonInteractive", "-Command", locate],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
            value = result.stdout.strip()
            if result.returncode == 0 and re.fullmatch(r"[A-Za-z]:[\\/][^\r\n]+", value):
                candidate = _from_windows_path(value)
                if candidate:
                    return str(candidate)
        except OSError, subprocess.SubprocessError:
            continue
    raise TargetError(
        "target_unavailable",
        message(
            "Windows PowerShell 7 was not found in PATH or mounted Windows installations; check the Windows installation and WSL drive mounts (root/sudo may clear PATH)"
        ),
        environment="win",
        reason="powershell_missing",
    )
