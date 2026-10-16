from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt", "Windows Job Object ownership")
class WindowsJobTests(unittest.TestCase):
    def test_worker_exit_and_forced_stop_release_orphaned_descendant_port_and_lock(self):
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        child_code = """import ctypes, http.server, json, msvcrt, os, sys
from pathlib import Path
lock = Path(sys.argv[1]).open("w+b")
lock.write(b"1"); lock.flush(); lock.seek(0)
msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
class Startup(ctypes.Structure):
    _fields_ = [("cb",ctypes.c_uint32),("reserved",ctypes.c_void_p),("desktop",ctypes.c_void_p),("title",ctypes.c_void_p),("x",ctypes.c_uint32),("y",ctypes.c_uint32),("width",ctypes.c_uint32),("height",ctypes.c_uint32),("columns",ctypes.c_uint32),("rows",ctypes.c_uint32),("fill",ctypes.c_uint32),("flags",ctypes.c_uint32),("show",ctypes.c_uint16),("reserved_size",ctypes.c_uint16),("reserved_data",ctypes.c_void_p),("stdin",ctypes.c_void_p),("stdout",ctypes.c_void_p),("stderr",ctypes.c_void_p)]
startup = Startup(); startup.cb = ctypes.sizeof(startup)
ctypes.windll.kernel32.GetStartupInfoW(ctypes.byref(startup))
Path(sys.argv[2]).write_text(json.dumps({"pid":os.getpid(),"port":server.server_port,"console":ctypes.windll.kernel32.GetConsoleWindow(),"show":startup.show,"show_flags":startup.flags}))
server.serve_forever()
"""
        wrapper_code = """import subprocess, sys
from dev_tools.runtimes.platforms.windows import WindowsBackend
subprocess.Popen([sys.executable,"-c",sys.argv[1],*sys.argv[2:]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **WindowsBackend(None).child_options())
"""
        worker_code = """import subprocess, sys
from dev_tools.runtimes.platforms.windows import WindowsBackend
backend = WindowsBackend(None)
backend.guard_worker()
subprocess.run([sys.executable,"-c",sys.argv[1],*sys.argv[2:]], check=True, **backend.child_options())
sys.stdin.readline()
"""
        for forced in (False, True):
            with self.subTest(forced=forced), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                marker, lock = root / "ready.json", root / "data.lock"
                environment = {
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                }
                worker = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        worker_code,
                        wrapper_code,
                        child_code,
                        str(lock),
                        str(marker),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                descendant = None
                try:
                    deadline = time.monotonic() + 12
                    while (
                        not marker.is_file()
                        and time.monotonic() < deadline
                        and worker.poll() is None
                    ):
                        time.sleep(0.05)
                    if not marker.is_file():
                        worker.kill()
                        self.fail(str(worker.communicate(timeout=5)))
                    record = json.loads(marker.read_text())
                    descendant = kernel.OpenProcess(0x0001 | 0x1000, False, record["pid"])
                    self.assertTrue(descendant)
                    self.assertEqual(record["console"], 0)
                    self.assertTrue(record["show_flags"] & 1)
                    self.assertEqual(record["show"], 1)
                    with socket.create_connection(("127.0.0.1", record["port"]), timeout=1):
                        pass
                    with lock.open("r+b") as stream, self.assertRaises(OSError):
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    if forced:
                        worker.terminate()
                    else:
                        worker.stdin.write(b"exit\n")
                        worker.stdin.flush()
                    worker.communicate(timeout=10)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        with socket.socket() as probe:
                            probe.settimeout(0.1)
                            if probe.connect_ex(("127.0.0.1", record["port"])) != 0:
                                break
                        time.sleep(0.05)
                    else:
                        self.fail("worker left its orphaned HTTP descendant running")
                    with lock.open("r+b") as stream:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                finally:
                    if worker.poll() is None:
                        worker.kill()
                    worker.communicate(timeout=5)
                    if descendant:
                        kernel.TerminateProcess(descendant, 1)
                        kernel.CloseHandle(descendant)


if __name__ == "__main__":
    unittest.main()
