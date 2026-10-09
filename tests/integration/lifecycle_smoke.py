"""Isolated, real native supervision smoke test; never uses existing registrations."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dev_tools.projects.config import render_toml


def free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def smoke() -> dict:
    repository = Path(__file__).resolve().parents[2]
    environment_name = "windows" if os.name == "nt" else "wsl"
    with tempfile.TemporaryDirectory(prefix="dev-tools-native-") as temporary:
        root = Path(temporary)
        source = root / "项目 & spaces"
        source.mkdir()
        (source / "source").mkdir()
        (source / "source/Fixture.java").write_text("class Fixture {}", encoding="utf-8")
        (source / "source/config.yaml").write_text("initial", encoding="utf-8")
        prepare = 'from pathlib import Path; import sys; sys.exit(7) if Path("fail.flag").exists() else Path("prepared.txt").write_text("ready")'
        build = 'from pathlib import Path; import time; Path("build.txt").write_text(str(time.time_ns()))'
        ports = [free_port(), free_port()]
        raw = {
            "schema": 1,
            "name": "fixture-" + root.name[-12:].lower(),
            "toolchain": "system",
            "rebuild_on_branch": True,
            "sync_exclude": ["prepared.txt", "build.txt"],
            "tasks": {
                "install": {"command": [sys.executable, "-I", "-c", prepare]},
                "build": {"command": [sys.executable, "-I", "-c", build], "role": "build"},
            },
            "services": {
                "api": {
                    "command": [
                        sys.executable,
                        "-I",
                        "-m",
                        "http.server",
                        str(ports[0]),
                        "--bind",
                        "127.0.0.1",
                    ],
                    "health": {"tcp": ports[0], "timeout": 30},
                    "restart": "never",
                },
                "web": {
                    "command": [
                        sys.executable,
                        "-I",
                        "-m",
                        "http.server",
                        str(ports[1]),
                        "--bind",
                        "127.0.0.1",
                    ],
                    "health": {"http": f"http://127.0.0.1:{ports[1]}/", "timeout": 30},
                    "depends_on": ["api"],
                    "restart": "never",
                },
            },
            "builds": {
                "api": {
                    "watch": ["source"],
                    "source_task": "build",
                    "resource_task": "build",
                    "structural_task": "build",
                }
            },
        }
        (source / "dev-tools.toml").write_text(render_toml(raw), encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            PYTHONPATH=str(repository / "src"),
            PYTHONUTF8="1",
            DEV_TOOLS_REGISTRY_ROOT=str(root / "registry"),
            DEV_TOOLS_STATE_ROOT=str(root / "state"),
            DEV_TOOLS_CACHE_ROOT=str(root / "cache"),
        )
        if environment_name == "wsl":
            environment["DEV_TOOLS_TEST_TRANSIENT"] = "1"
        name = raw["name"]

        entry = (
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(repository / "scripts/dev-tools.ps1"),
            ]
            if environment_name == "windows"
            else ["bash", str(repository / "scripts/dev-tools")]
        )

        def invoke(*arguments, expected=0, cwd=source):
            result = subprocess.run(
                [*entry, "--env", "win" if os.name == "nt" else "wsl", *arguments],
                env=environment,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
                check=False,
            )
            if result.returncode != expected:
                raise AssertionError(
                    f"{arguments}: {result.returncode}\n{result.stdout}\n{result.stderr}"
                )
            return result

        def status():
            return json.loads(invoke("status", "--json", cwd=source / "source").stdout)

        def await_file(path, previous=None):
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if path.is_file() and path.read_text() != previous:
                    return path.read_text()
                time.sleep(0.5)
            logs = {
                log.name: log.read_text(encoding="utf-8", errors="replace")[-2000:]
                for log in (Path(binding["state"]) / "logs").glob("*.log")
            }
            raise AssertionError(f"monitor did not rebuild: {path}; status={status()}; logs={logs}")

        binding = json.loads(invoke("register", "--json").stdout)["binding"]
        try:
            listing = json.loads(invoke("list", "--json").stdout)
            if [item["name"] for item in listing["projects"]] != [name]:
                raise AssertionError(listing)
            plan = json.loads(invoke("prepare", "--dry-run", "--json").stdout)
            if not plan["ready"] or (source / "prepared.txt").exists():
                raise AssertionError(plan)
            binding = plan["binding"]
            workspace = Path(binding["workspace"])
            invoke("start", name, expected=1)
            invoke("prepare")
            if not (workspace / "prepared.txt").is_file():
                raise AssertionError("preparation task did not execute in native workspace")
            invoke("start")
            first = status()
            if not first["ready"] or first["healthy"] is not True:
                raise AssertionError(first)
            invoke("build", "--service", "api", "--kind", "source")
            manual_build = (workspace / "build.txt").read_text()
            # Each supervisor launched the same worker and watches the same declaration.
            time.sleep(3)
            (source / "source/Fixture.java").write_text(
                "class Fixture { int value; }", encoding="utf-8"
            )
            stamp = await_file(workspace / "build.txt", manual_build)
            hot = status()
            if hot["services"]["api"]["pid"] != first["services"]["api"]["pid"]:
                raise AssertionError("source build restarted the process")
            (source / "source/config.yaml").write_text("updated", encoding="utf-8")
            await_file(workspace / "build.txt", stamp)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                rebuilt = status()
                if rebuilt["ready"]:
                    break
                time.sleep(0.5)
            if (
                not rebuilt["ready"]
                or rebuilt["services"]["api"]["pid"] == first["services"]["api"]["pid"]
            ):
                raise AssertionError(rebuilt)
            # A failed prepare leaves services stopped and remembers the active set.
            (source / "fail.flag").write_text("fail")
            failure = invoke("prepare", name, expected=1)
            failed = status()
            if not failed["recovery"] or any(item["ready"] for item in failed["services"].values()):
                raise AssertionError(f"{failed}; prepare={failure.stdout} {failure.stderr}")
            (source / "fail.flag").unlink()
            invoke("prepare", name)
            if not status()["ready"]:
                raise AssertionError(status())
            invoke("restart", name)
            renamed = name + "-renamed"
            invoke("rename", renamed)
            name = renamed
            if not status()["ready"]:
                raise AssertionError(status())
            invoke("stop", name)
            if status()["ready"]:
                raise AssertionError("stop did not stop services")
            return {
                "environment": environment_name,
                "schema_version": plan["schema_version"],
                "services": list(first["services"]),
                "prepare_recovery": True,
                "hot_build": True,
                "resource_restart": True,
                "rename": True,
                "implicit_name": True,
                "list": True,
                "build": True,
                "native_workspace": workspace != source,
            }
        finally:
            invoke("unregister")
            if json.loads(invoke("list", "--json").stdout)["projects"]:
                raise AssertionError("unregister left the native registration")
            if not source.is_dir() or not (Path(binding["state"]) / "project.json").is_file():
                raise AssertionError("default unregister deleted source or state")
            for port in ports:
                with socket.socket() as probe:
                    probe.settimeout(1)
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        raise AssertionError(f"native worker leaked its HTTP process: {port}")


if __name__ == "__main__":
    print(json.dumps(smoke(), ensure_ascii=False, indent=2))
