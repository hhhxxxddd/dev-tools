"""Linux /proc collectors; no elevation or executable discovery for port queries."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def parse_table(text: str, protocol: str, ipv6: bool) -> list[dict]:
    entries = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or (protocol == "tcp" and fields[3] != "0A"):
            continue
        try:
            address, port = fields[1].split(":")
            if int(port, 16) == 0:
                continue
            raw = b"".join(
                int(address[index : index + 8], 16).to_bytes(4, sys.byteorder)
                for index in range(0, len(address), 8)
            )
            entries.append(
                {
                    "protocol": protocol,
                    "address": socket.inet_ntop(socket.AF_INET6 if ipv6 else socket.AF_INET, raw),
                    "port": int(port, 16),
                    "inode": fields[9],
                }
            )
        except ValueError, OSError, OverflowError:
            continue
    return entries


def process_info(path: Path, boot: float, ticks: int) -> dict:
    text = (path / "stat").read_text()
    end = text.rindex(")")
    fields = text[end + 2 :].split()
    try:
        executable = str((path / "exe").readlink())
    except OSError:
        executable = None
    try:
        units = sorted(
            {
                part
                for line in (path / "cgroup").read_text().splitlines()
                for part in line.split(":", 2)[-1].split("/")
                if part.endswith(".service")
            }
        )
    except OSError:
        units = []
    return {
        "name": text[text.index("(") + 1 : end],
        "executable": executable,
        "parent_pid": int(fields[1]),
        "started": boot + int(fields[19]) / ticks,
        "units": units,
    }


def port_snapshot(
    proc: Path = Path("/proc"), *, port: int | None = None, protocol: str = "all"
) -> dict:
    rows, warnings = [], []
    for kind in ("tcp", "udp") if protocol == "all" else (protocol,):
        for ipv6 in (False, True):
            path = proc / "net" / (kind + ("6" if ipv6 else ""))
            try:
                rows.extend(
                    row
                    for row in parse_table(path.read_text(), kind, ipv6)
                    if port is None or row["port"] == port
                )
            except FileNotFoundError:
                if not ipv6:
                    warnings.append("socket_table_unavailable")
            except OSError:
                warnings.append("socket_table_unavailable")
    if not rows:
        return {"entries": [], "processes": {}, "warnings": warnings}
    boot = next(
        float(line.split()[1])
        for line in (proc / "stat").read_text().splitlines()
        if line.startswith("btime ")
    )
    ticks = os.sysconf("SC_CLK_TCK")
    inodes = {row["inode"] for row in rows}
    owners, processes = {}, {}
    for path in proc.iterdir():
        if not path.name.isdecimal():
            continue
        try:
            matched = False
            for fd in (path / "fd").iterdir():
                try:
                    link = str(fd.readlink())
                    if link.startswith("socket:[") and link.endswith("]") and link[8:-1] in inodes:
                        owners.setdefault(link[8:-1], set()).add(int(path.name))
                        matched = True
                except OSError:
                    pass  # Descriptors and processes may disappear during a snapshot.
            if matched:
                processes[path.name] = process_info(path, boot, ticks)
        except OSError, ValueError, IndexError:
            continue
    entries = []
    for row in rows:
        inode = row.pop("inode")
        for pid in sorted(owners.get(inode, ())) or [None]:
            entries.append({**row, "pid": pid})
    return {"entries": entries, "processes": processes, "warnings": warnings}


def network_info() -> dict:
    value = {"dns_servers": [], "wsl_networking_mode": None, "wsl_configuration": {}}
    try:
        text = Path("/etc/resolv.conf").read_text(encoding="utf-8")[:65536]
        value["dns_servers"] = list(
            dict.fromkeys(
                fields[1]
                for line in text.splitlines()
                if len(fields := line.split()) >= 2 and fields[0] == "nameserver"
            )
        )
    except OSError, UnicodeError:
        value["warnings"] = ["resolver_configuration_unreadable"]
    if client := shutil.which("wslinfo"):
        try:
            result = subprocess.run(
                [client, "--networking-mode"],
                capture_output=True,
                text=True,
                timeout=3,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            mode = result.stdout.strip().lower()
            if result.returncode == 0 and mode in {
                "nat",
                "mirrored",
                "none",
                "virtioproxy",
                "bridged",
            }:
                value["wsl_networking_mode"] = mode
        except OSError, subprocess.SubprocessError:
            pass
    return value
