"""Real automatic package add/upgrade/remove, using local npm packages and installed Node."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from dev_tools.projects.config import render_toml


def smoke() -> dict:
    repository = Path(__file__).resolve().parents[2]
    native = "windows" if os.name == "nt" else "wsl"
    inventory = json.loads(
        subprocess.check_output(["mise", "ls", "--installed", "--json"], text=True)
    )
    node = next(item["version"] for item in reversed(inventory["node"]) if item["installed"])
    with tempfile.TemporaryDirectory(prefix="dev-tools-dependencies-") as temporary:
        root = Path(temporary).resolve()
        source = root / "dependencies & spaces"
        source.mkdir()
        for version in ("1.0.0", "2.0.0"):
            package = source / "vendor" / version
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                json.dumps({"name": "fixture-library", "version": version, "main": "index.js"})
            )
            (package / "index.js").write_text(f"module.exports = '{version}';\n")
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            port = server.getsockname()[1]
        name = "deps-" + root.name[-12:].lower()
        manifest = {"name": "fixture", "version": "1.0.0"}
        (source / "package.json").write_text(json.dumps(manifest))
        (source / "mise.toml").write_text(render_toml({"tools": {"node": node}}))
        script = (
            "require('http').createServer((q,s)=>{let v='none';"
            "try{v=require('fixture-library')}catch(e){}s.end(v)})"
            f".listen({port},'127.0.0.1')"
        )
        raw = {
            "schema": 1,
            "name": name,
            "rebuild_on_branch": False,
            "tasks": {
                "install": {
                    "command": ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                    "manager": "npm",
                }
            },
            "services": {
                "web": {
                    "command": ["node", "-e", script],
                    "health": {"tcp": port, "timeout": 30},
                    "restart": "never",
                }
            },
        }
        (source / "dev-tools.toml").write_text(render_toml(raw))
        environment = os.environ.copy()
        environment.update(
            PYTHONPATH=str(repository / "src"),
            PYTHONUTF8="1",
            DEV_TOOLS_REGISTRY_ROOT=str(root / "registry"),
            DEV_TOOLS_STATE_ROOT=str(root / "state"),
            DEV_TOOLS_CACHE_ROOT=str(root / "cache"),
        )
        if native == "wsl":
            environment["DEV_TOOLS_TEST_TRANSIENT"] = "1"

        def invoke(*arguments):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dev_tools",
                    "-e",
                    "win" if native == "windows" else "wsl",
                    *arguments,
                ],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
                check=False,
            )
            if result.returncode:
                raise AssertionError(f"{arguments}\n{result.stdout}\n{result.stderr}")
            return result

        def await_version(expected):
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                        version = response.read().decode()
                    status = json.loads(invoke("status", name, "--json").stdout)
                    if version == expected and status["ready"]:
                        return status
                except OSError, urllib.error.URLError:
                    pass
                time.sleep(0.5)
            logs = {
                path.name: path.read_text(errors="replace")[-2000:]
                for path in (Path(binding["state"]) / "logs").rglob("*.log")
            }
            raise AssertionError(f"{expected}: {invoke('status', name, '--json').stdout}; {logs}")

        binding = json.loads(invoke("register", str(source), "--json").stdout)["binding"]
        workspace = Path(binding["workspace"])
        try:
            invoke("start", name)
            initial = await_version("none")
            for version in ("1.0.0", "2.0.0"):
                manifest["dependencies"] = {"fixture-library": f"file:vendor/{version}"}
                (source / "package.json").write_text(json.dumps(manifest))
                status = await_version(version)
                if status["services"]["web"]["pid"] == initial["services"]["web"]["pid"]:
                    raise AssertionError("dependency change did not restart application")
                lock = json.loads((source / "package-lock.json").read_text())
                if (
                    lock["packages"][""]["dependencies"]["fixture-library"]
                    != f"file:vendor/{version}"
                ):
                    raise AssertionError(lock)
            manifest.pop("dependencies")
            (source / "package.json").write_text(json.dumps(manifest))
            await_version("none")
            if (workspace / "node_modules/fixture-library").exists():
                raise AssertionError("removed dependency remains installed")
            invoke("stop", name)
            manifest["dependencies"] = {"fixture-library": "file:vendor/1.0.0"}
            (source / "package.json").write_text(json.dumps(manifest))
            time.sleep(3)
            stopped = json.loads(invoke("status", name, "--json").stdout)
            if stopped["services"]["web"]["phase"] == "running":
                raise AssertionError("stop resurrected project")
            invoke("start", name)
            await_version("1.0.0")
            return {
                "environment": native,
                "automatic_start": True,
                "add": True,
                "upgrade": True,
                "remove": True,
                "lock_writeback": True,
                "stop_and_catch_up": True,
                "network_packages": False,
            }
        finally:
            invoke("unregister", name)


if __name__ == "__main__":
    print(json.dumps(smoke(), indent=2))
