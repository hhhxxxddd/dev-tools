"""Isolated real shared-server/client lifecycle, with no GUI or downloaded tools."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from dev_tools.projects.config import render_toml

APPLICATION = """import http.server, json, os, subprocess, sys, threading, urllib.request
from pathlib import Path
role, port, upstream = sys.argv[1:]
if role == "wrapper":
    raise SystemExit(subprocess.call([sys.executable, __file__, "desktop", port, upstream]))
lock = None
if role == "backend":
    path = Path(".local/data/backend.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.open("w+b"); lock.write(b"1"); lock.flush(); lock.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"application":"fixture", "ready":True, "shared":os.environ.get("SHARED_BACKEND")}
        if role == "frontend":
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:"+upstream+"/health", timeout=1) as response:
                body = json.load(response)
        self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(body).encode())
        if self.path == "/close" and role == "desktop":
            threading.Thread(target=server.shutdown, daemon=True).start()
    def log_message(self, *_): pass
server = http.server.HTTPServer(("127.0.0.1", int(port)), Handler)
try: server.serve_forever()
finally:
    server.server_close()
    if lock: lock.close()
"""


def smoke() -> dict:
    native = "win" if os.name == "nt" else "wsl"
    with (
        tempfile.TemporaryDirectory(prefix="dev-tools-desktop-") as temporary,
        contextlib.ExitStack() as resources,
    ):
        root = Path(temporary).resolve()
        name = "desktop-" + root.name[-10:].lower()
        source = root / "source"
        source.mkdir()
        sockets = [resources.enter_context(socket.socket()) for _ in range(3)]
        for item in sockets:
            item.bind(("127.0.0.1", 0))
        ports = [item.getsockname()[1] for item in sockets]
        for item in sockets:
            item.close()
        (source / "app.py").write_text(APPLICATION, encoding="utf-8")
        services = {}
        for index, name in enumerate(("backend", "frontend", "desktop")):
            services[name] = {
                "command": [
                    sys.executable,
                    "app.py",
                    "wrapper" if name == "desktop" else name,
                    str(ports[index]),
                    str(ports[0]),
                ],
                "depends_on": [] if index == 0 else ["backend" if index == 1 else "frontend"],
                "health": {
                    "tcp": ports[index],
                    "http": f"http://127.0.0.1:{ports[index]}/health",
                    "timeout": 30,
                    "json": {"application": "fixture", "ready": True},
                },
                "required": name != "desktop",
                "restart": "never",
                "env": {"SHARED_BACKEND": "1"},
            }
        raw = {
            "schema": 1,
            "name": name,
            "toolchain": "system",
            "rebuild_on_branch": False,
            "services": services,
        }
        (source / "dev-tools.toml").write_text(render_toml(raw), encoding="utf-8")
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "PYTHONUTF8": "1",
            "DEV_TOOLS_REGISTRY_ROOT": str(root / "registry"),
            "DEV_TOOLS_STATE_ROOT": str(root / "state"),
            "DEV_TOOLS_CACHE_ROOT": str(root / "cache"),
        }
        if native == "wsl":
            environment["DEV_TOOLS_TEST_TRANSIENT"] = "1"

        def invoke(*args, success=True):
            result = subprocess.run(
                [sys.executable, "-m", "dev_tools", "-e", native, *args],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
                check=False,
            )
            if success and result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            return result

        def status():
            return json.loads(invoke("status", name, "--json").stdout)

        def get(port, path="/health"):
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{port}{path}", timeout=2
            ) as response:
                return json.load(response)

        invoke("register", str(source), "--json")
        try:
            invoke("start", name, "--json")
            first = status()
            assert first["ready"] and all(
                item["phase"] == "running" for item in first["services"].values()
            )
            shared_pids = [first["services"][name]["pid"] for name in ("backend", "frontend")]
            assert get(ports[2])["shared"] == "1"
            get(ports[2], "/close")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                closed = status()
                if closed["services"]["desktop"]["phase"] == "stopped":
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("client did not close")
            assert closed["ready"] and get(ports[1])["ready"]
            (source / "edited.rs").write_text("// simulated Rust source edit\n")
            invoke("start", name, "--service", "frontend", "--json")
            assert status()["services"]["desktop"]["phase"] == "stopped"
            with socket.socket() as foreign:
                foreign.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                foreign.bind(("127.0.0.1", ports[2]))
                foreign.listen()
                assert (
                    invoke(
                        "start", name, "--service", "desktop", "--json", success=False
                    ).returncode
                    != 0
                )
                assert status()["ready"]
            invoke("start", name, "--service", "desktop", "--json")
            reopened = status()
            invoke("start", name, "--service", "desktop", "--json")
            assert status()["services"]["desktop"]["pid"] == reopened["services"]["desktop"]["pid"]
            assert [
                reopened["services"][name]["pid"] for name in ("backend", "frontend")
            ] == shared_pids
            binding = json.loads(invoke("show", name, "--json").stdout)["binding"]
            invoke("stop", name, "--json")
            with (Path(binding["workspace"]) / ".local/data/backend.lock").open("r+b") as lock:
                if native == "win":
                    import msvcrt

                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for port in ports:
                with socket.socket() as probe:
                    probe.settimeout(0.5)
                    assert probe.connect_ex(("127.0.0.1", port)) != 0, port
            return {
                "environment": native,
                "http_json_readiness": True,
                "close_keeps_browser": True,
                "reopen": True,
                "idempotent": True,
                "foreign_port_rejected": True,
                "shared_pids_preserved": True,
                "ports_and_data_lock_released": True,
            }
        finally:
            invoke("unregister", name, "--json")


if __name__ == "__main__":
    print(json.dumps(smoke(), indent=2))
