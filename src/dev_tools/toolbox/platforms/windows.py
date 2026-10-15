"""Windows resolver configuration and native socket snapshots."""

from __future__ import annotations

import configparser
import json
import subprocess
from pathlib import Path

from ...i18n import message
from ...projects.models import ProjectError
from .windows_sockets import port_snapshot

__all__ = ["network_info", "port_snapshot"]


def query(script: str) -> dict:
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        raise ProjectError(message("Native network information could not be collected"))
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise TypeError
        return value
    except (TypeError, ValueError) as exc:
        raise ProjectError(message("Native network information could not be collected")) from exc


def network_info() -> dict:
    value = query(r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
@{dns_servers=@(Get-DnsClientServerAddress | ForEach-Object ServerAddresses | Sort-Object -Unique)} |
  ConvertTo-Json -Depth 3 -Compress
""")
    value.update(wsl_networking_mode=None, wsl_configuration={})
    path = Path.home() / ".wslconfig"
    if path.is_file():
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(path.read_text(encoding="utf-8-sig")[:65536])
            if parser.has_section("wsl2"):
                value["wsl_configuration"] = {
                    key: parser.get("wsl2", key)
                    for key in (
                        "networkingMode",
                        "dnsTunneling",
                        "autoProxy",
                        "localhostForwarding",
                    )
                    if parser.has_option("wsl2", key)
                }
        except OSError, UnicodeError, configparser.Error:
            value["warnings"] = ["wsl_configuration_unreadable"]
    return value
