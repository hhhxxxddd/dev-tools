from __future__ import annotations

import os
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.toolbox import ports
from dev_tools.toolbox.platforms import windows_sockets, wsl


class SocketTableTests(unittest.TestCase):
    def test_windows_tables_decode_both_families_and_filter_listeners(self):
        for family in (socket.AF_INET, socket.AF_INET6):
            address = "127.0.0.1" if family == socket.AF_INET else "::1"
            packed = socket.inet_pton(family, address)
            for protocol in ("tcp", "udp"):
                with self.subTest(family=family, protocol=protocol):
                    port = socket.htons(43210)
                    if family == socket.AF_INET6:
                        row = (
                            windows_sockets.TCP6.pack(packed, 0, port, bytes(16), 0, 0, 2, 123)
                            if protocol == "tcp"
                            else windows_sockets.UDP6.pack(packed, 0, port, 123)
                        )
                    else:
                        native = struct.unpack("<I", packed)[0]
                        row = (
                            windows_sockets.TCP4.pack(2, native, port, 0, 0, 123)
                            if protocol == "tcp"
                            else windows_sockets.UDP4.pack(native, port, 123)
                        )
                    table = struct.pack("<I", 1) + row
                    result = windows_sockets.decode_table(table, protocol, family, 43210)
                    self.assertEqual(
                        result,
                        [{"protocol": protocol, "address": address, "port": 43210, "pid": 123}],
                    )
                    self.assertEqual(
                        windows_sockets.decode_table(table, protocol, family, 43211), []
                    )
                    with self.assertRaises(ValueError):
                        windows_sockets.decode_table(table[:-1], protocol, family, None)
        connected = struct.pack("<I", 1) + windows_sockets.TCP4.pack(5, 0, 80, 0, 0, 123)
        self.assertEqual(windows_sockets.decode_table(connected, "tcp", socket.AF_INET, None), [])

    def test_empty_linux_filter_never_enumerates_processes_or_other_protocols(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            (proc / "net").mkdir()
            header = "sl local_address rem_address st tx rx tr uid timeout inode\n"
            row = "0: 0100007F:0BB8 00000000:0000 0A 0:0 00:0 0 0 0 123"
            (proc / "net/tcp").write_text(header + row)
            (proc / "net/tcp6").write_text(header)
            with patch.object(Path, "iterdir", side_effect=AssertionError("Unexpected PID scan")):
                snapshot = wsl.port_snapshot(proc, port=4000, protocol="tcp")
            self.assertEqual(snapshot, {"entries": [], "processes": {}, "warnings": []})

    def test_empty_port_query_does_not_load_project_registrations(self):
        with (
            patch("dev_tools.toolbox.ports.native") as platform,
            patch("dev_tools.toolbox.ports.Registry", side_effect=AssertionError("Registry scan")),
        ):
            platform.return_value.port_snapshot.return_value = {
                "entries": [],
                "processes": {},
                "warnings": [],
            }
            self.assertTrue(ports.collect_ports("windows", port=65535, protocol="tcp")["complete"])
            platform.return_value.port_snapshot.assert_called_once_with(port=65535, protocol="tcp")

    @unittest.skipUnless(os.name == "nt", "native Windows IP Helper and process APIs")
    def test_native_windows_tcp_udp_owners_and_process_identity(self):
        for family in (socket.AF_INET, socket.AF_INET6):
            for kind, protocol in ((socket.SOCK_STREAM, "tcp"), (socket.SOCK_DGRAM, "udp")):
                with (
                    self.subTest(family=family, protocol=protocol),
                    socket.socket(family, kind) as server,
                ):
                    try:
                        server.bind(("127.0.0.1" if family == socket.AF_INET else "::1", 0))
                    except OSError:
                        if family == socket.AF_INET6 and not socket.has_ipv6:
                            continue
                        raise
                    if kind == socket.SOCK_STREAM:
                        server.listen()
                    port = server.getsockname()[1]
                    value = windows_sockets.port_snapshot(port=port, protocol=protocol)
                    self.assertFalse(value["warnings"])
                    self.assertTrue(any(row["pid"] == os.getpid() for row in value["entries"]))
                    self.assertTrue(all(row["port"] == port for row in value["entries"]))
                    process = value["processes"][str(os.getpid())]
                    self.assertEqual(process["parent_pid"], os.getppid())
                    self.assertTrue(Path(process["executable"]).is_file())
                    self.assertGreater(process["started"], 0)
