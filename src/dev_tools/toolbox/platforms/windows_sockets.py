"""Read IP Helper tables and process identities without spawning PowerShell or WMI."""

from __future__ import annotations

import ctypes
import socket
import struct

DWORD = ctypes.c_uint32
TCP4 = struct.Struct("<6I")
TCP6 = struct.Struct("<16sII16sIIII")
UDP4 = struct.Struct("<3I")
UDP6 = struct.Struct("<16sIII")
TCP_TABLE_OWNER_PID_LISTENER = 3
UDP_TABLE_OWNER_PID = 1


def decode_table(data: bytes, protocol: str, family: int, port: int | None) -> list[dict]:
    """Decode TCP listener or UDP owner-PID tables, never unfiltered TCP tables.

    Fixed-width DWORD layouts match both 32-bit and 64-bit Windows ABIs.
    The TCP table class already selects listeners; some Windows builds return
    a zero state for those rows, so filtering dwState again would lose listeners.
    """
    ipv6 = family == socket.AF_INET6
    layout = (TCP6 if ipv6 else TCP4) if protocol == "tcp" else (UDP6 if ipv6 else UDP4)
    if len(data) < 4:
        raise ValueError("Truncated socket table")
    count = struct.unpack_from("<I", data)[0]
    if count > (len(data) - 4) // layout.size:
        raise ValueError("Truncated socket table")
    entries = []
    for index in range(count):
        values = layout.unpack_from(data, 4 + index * layout.size)
        address, scope = (values[0], values[1]) if ipv6 else (None, 0)
        if protocol == "tcp":
            local_port = values[2]
            if not ipv6:
                address = struct.pack("<I", values[1])
        else:
            local_port = values[2 if ipv6 else 1]
            if not ipv6:
                address = struct.pack("<I", values[0])
        number = socket.ntohs(local_port & 0xFFFF)
        if not number or port is not None and number != port:
            continue
        text = socket.inet_ntop(family, address)
        if scope:
            text += f"%{scope}"
        entries.append({"protocol": protocol, "address": text, "port": number, "pid": values[-1]})
    return entries


def socket_table(api, protocol: str, family: int, port: int | None) -> list[dict]:
    function = api.GetExtendedTcpTable if protocol == "tcp" else api.GetExtendedUdpTable
    function.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWORD), DWORD, DWORD, DWORD, DWORD]
    function.restype = DWORD
    size = DWORD()
    buffer = None
    table_class = TCP_TABLE_OWNER_PID_LISTENER if protocol == "tcp" else UDP_TABLE_OWNER_PID
    for _ in range(4):
        code = function(buffer, ctypes.byref(size), 0, family, table_class, 0)
        if code in {50, 232} and family == socket.AF_INET6:
            return []
        if code == 232:  # ERROR_NO_DATA
            return []
        if code == 0 and buffer is not None:
            return decode_table(buffer.raw[: size.value], protocol, family, port)
        if code != 122 or not 4 <= size.value <= 64 * 1024 * 1024:
            raise OSError(code, "Socket table unavailable")
        buffer = ctypes.create_string_buffer(size.value)
    raise OSError("Socket table changed repeatedly")


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", DWORD),
        ("usage", DWORD),
        ("pid", DWORD),
        ("heap", ctypes.c_size_t),
        ("module", DWORD),
        ("threads", DWORD),
        ("parent_pid", DWORD),
        ("priority", ctypes.c_int32),
        ("flags", DWORD),
        ("name", ctypes.c_wchar * 260),
    ]


class FileTime(ctypes.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


def process_snapshot(api, pids: set[int]) -> dict:
    """Collect ancestry cheaply, then open only owners and their live ancestors."""
    api.CreateToolhelp32Snapshot.argtypes = [DWORD, DWORD]
    api.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int32
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(api, name)
        function.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry)]
        function.restype = ctypes.c_int32
    snapshot = api.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot in {None, ctypes.c_void_p(-1).value}:
        raise OSError("Process snapshot unavailable")
    records = {}
    try:
        entry = ProcessEntry(size=ctypes.sizeof(ProcessEntry))
        present = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            records[entry.pid] = {"name": entry.name, "parent_pid": entry.parent_pid}
            present = api.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        api.CloseHandle(snapshot)
    api.OpenProcess.argtypes = [DWORD, ctypes.c_int32, DWORD]
    api.OpenProcess.restype = ctypes.c_void_p
    api.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        DWORD,
        ctypes.c_wchar_p,
        ctypes.POINTER(DWORD),
    ]
    api.QueryFullProcessImageNameW.restype = ctypes.c_int32
    api.GetProcessTimes.argtypes = [ctypes.c_void_p, *([ctypes.POINTER(FileTime)] * 4)]
    api.GetProcessTimes.restype = ctypes.c_int32
    processes = {}
    pending = list(pids)
    while pending:
        pid = pending.pop()
        if str(pid) in processes or pid not in records:
            continue
        record = {**records[pid], "executable": None, "started": None}
        processes[str(pid)] = record
        parent = record["parent_pid"]
        if parent:
            pending.append(parent)
        handle = api.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue  # Exited or protected processes remain partially described.
        try:
            length = DWORD(32768)
            path = ctypes.create_unicode_buffer(length.value)
            if api.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                record["executable"] = path.value
            created, exited, kernel, user = (FileTime() for _ in range(4))
            if api.GetProcessTimes(handle, *map(ctypes.byref, (created, exited, kernel, user))):
                record["started"] = ((created.high << 32) | created.low) / 10_000_000 - 11644473600
        finally:
            api.CloseHandle(handle)
    return processes


def port_snapshot(*, port: int | None = None, protocol: str = "all") -> dict:
    api = ctypes.WinDLL("iphlpapi", use_last_error=True)
    entries, warnings = [], []
    for kind in ("tcp", "udp") if protocol == "all" else (protocol,):
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                entries.extend(socket_table(api, kind, family, port))
            except OSError, ValueError:
                warnings.append("socket_table_unavailable")
    processes = {}
    if entries:
        try:
            processes = process_snapshot(
                ctypes.WinDLL("kernel32", use_last_error=True), {row["pid"] for row in entries}
            )
        except OSError:
            warnings.append("process_details_unavailable")
    return {"entries": entries, "processes": processes, "warnings": warnings}
