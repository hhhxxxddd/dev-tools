"""Windows-only destination resolution and fixed-target privileged writer."""

from __future__ import annotations

import ctypes
import subprocess
import tempfile
from pathlib import Path

from ...hosts_config import digest, read_bytes
from ...i18n import message


def system_hosts_path() -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError(message("Could not locate the Windows hosts file"))
    return Path(buffer.value) / "System32/drivers/etc/hosts"


def write_system_hosts(before: bytes, after: bytes) -> bool:
    helper = Path(__file__).resolve().parents[4] / "scripts/dev-tools-hosts.ps1"
    with tempfile.TemporaryDirectory(prefix="dev-tools-hosts-") as temporary:
        staged = Path(temporary) / "content.hosts"
        staged.write_bytes(after)
        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(helper),
                "-ContentFile",
                str(staged),
                "-ExpectedHash",
                digest(before),
                "-ContentHash",
                digest(after),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    if result.returncode == 2:
        raise ValueError(message("Hosts changed during editing; reopen the file and retry"))
    if result.returncode == 3:
        raise ValueError(message("Windows permission request was cancelled; hosts was not changed"))
    if result.returncode not in {0, 4}:
        raise ValueError(
            message(
                "Could not write Windows hosts; check administrator permission, read-only attributes and security software"
            )
        )
    if read_bytes(system_hosts_path()) != after:
        raise ValueError(
            message("Hosts verification failed; the automatic backup is available for recovery")
        )
    return result.returncode == 0
