"""Real native workers, isolated registrations and deployment, with update recovery."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from dev_tools.controller import clear_controller_bytecode
from dev_tools.controller_update import UpdateSession, active_projects, update_with_recovery
from dev_tools.projects.config import render_toml
from dev_tools.release import REPOSITORY, build


def port():
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return stream.getsockname()[1]


def smoke():
    native = "win" if os.name == "nt" else "wsl"
    if native == "wsl" and os.geteuid() != 0:
        raise SystemExit("Run as root for native WSL worker lifecycle checks")
    with tempfile.TemporaryDirectory(prefix="dev-tools-update-smoke-") as temporary:
        root = Path(temporary).resolve()
        package = root / "package"
        deployed = root / "controller"
        output = build(REPOSITORY, root / "dist")
        with zipfile.ZipFile(output["archive"]) as archive:
            archive.extractall(package)
        shutil.copytree(package, deployed)
        # Exercise genuine version replacement while keeping this fixture's controller API.
        version_file = deployed / "src/dev_tools/__init__.py"
        version_file.write_text(version_file.read_text().replace('"0.5.2"', '"0.5.1"'))
        original_stat = (package / "src/dev_tools/__init__.py").stat()
        os.utime(version_file, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        (deployed / (".host-python.win" if native == "win" else ".host-python")).write_text(
            str(Path(sys.executable).resolve()) + "\n"
        )
        config = root / "preferences.toml"
        config.write_text('language="en"\n')
        name = "update-" + root.name.rsplit("-", 1)[-1].lower()
        names = (name, name + "-idle")
        environment = os.environ.copy()
        environment.pop("NO_COLOR", None)
        environment["TERM"] = "xterm-256color"
        environment.update(
            PYTHONPATH=str(REPOSITORY / "src"),
            PYTHONUTF8="1",
            DEV_TOOLS_CONFIG=str(config),
            DEV_TOOLS_REGISTRY_ROOT=str(root / "registry"),
            DEV_TOOLS_STATE_ROOT=str(root / "state"),
            DEV_TOOLS_CACHE_ROOT=str(root / "cache"),
            DEV_TOOLS_INSTALL_ROOT=str(deployed),
            DEV_TOOLS_UNIT_DIR=str(root / "units"),
            DEV_TOOLS_COMMAND_ROOT=str(root / "bin"),
            DEV_TOOLS_CONFIG_ROOT=str(root / "config"),
            DEV_TOOLS_PYTHON=str(Path(sys.executable).resolve()),
            DEV_TOOLS_TERMINAL="1",
        )
        if native == "wsl":
            environment["DEV_TOOLS_TEST_TRANSIENT"] = "1"
            fake = root / "fake"
            fake.mkdir()
            systemctl = shutil.which("systemctl")
            wrapper = fake / "systemctl"
            # Scope the installer's active-unit check to these isolated registrations.
            wrapper.write_text(
                f'#!/bin/bash\nif [[ "$1" == list-units ]]; then\n  "{systemctl}" "$@" | awk \'/dev-tools-worker@{name}/\'\nelse\n  exec "{systemctl}" "$@"\nfi\n'
            )
            wrapper.chmod(0o755)
            environment["PATH"] = str(fake) + ":" + environment["PATH"]
            (root / "units").mkdir()
            (root / "bin").mkdir()
        entry = (
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(deployed / "scripts/dev-tools.ps1"),
            ]
            if native == "win"
            else ["bash", str(deployed / "scripts/dev-tools")]
        )

        def invoke(*arguments, answer=None, expected=0):
            result = subprocess.run(
                [*entry, "-e", native, *arguments],
                input=answer,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
                check=False,
            )
            if result.returncode != expected:
                raise AssertionError(
                    f"{arguments}: {result.returncode}\n{result.stdout}\n{result.stderr}"
                )
            return result

        def status(project):
            return json.loads(invoke("status", project, "--json").stdout)

        registered = []
        with patch.dict(os.environ, environment, clear=True):
            try:
                for index, project in enumerate(names):
                    source = root / project
                    source.mkdir()
                    services = {}
                    for service in ("api", "extra"):
                        tcp = port()
                        services[service] = {
                            "command": [
                                sys.executable,
                                "-m",
                                "http.server",
                                str(tcp),
                                "--bind",
                                "127.0.0.1",
                            ],
                            "health": {"tcp": tcp, "timeout": 30},
                            "restart": "never",
                        }
                    raw = {
                        "schema": 1,
                        "name": project,
                        "toolchain": "system",
                        "rebuild_on_branch": False,
                        "dependency_mode": "locked",
                        "tasks": {
                            "install": {
                                "command": [sys.executable, "-c", "import time; time.sleep(5.5)"],
                            }
                        },
                        "services": services,
                    }
                    (source / "dev-tools.toml").write_text(render_toml(raw), encoding="utf-8")
                    (source / "mise.toml").write_text('[tools]\npython="3.14.8"\n')
                    invoke("register", str(source), "--json")
                    registered.append(project)
                started = invoke("start", name, "--service", "api")
                assert "Analyze declarations" in started.stderr
                assert "Run dependency/build task: install" in started.stderr
                assert "still working" in started.stderr
                assert "Health check: api" in started.stderr
                assert "Start file and dependency monitors" in started.stderr
                assert "\x1b[33m" in started.stderr and "\x1b[32m" in started.stderr
                assert "Health check" not in started.stdout
                previous = status(name)
                assert previous["services"]["api"]["phase"] == "running"
                assert previous["services"]["extra"]["phase"] == "stopped"
                if native == "wsl":
                    invoke("self", "update", "--source", str(package), answer="N\n", expected=1)
                    assert (
                        status(name)["services"]["api"]["pid"] == previous["services"]["api"]["pid"]
                    )
                    result = invoke("self", "update", "--source", str(package), answer="Y\n")
                    assert "Restored" in result.stdout
                else:
                    # Windows uses the same transaction around Scoop/Git deployment.
                    # Existing installer tests exercise profile/Scoop hooks separately.
                    def install():
                        assert not active_projects(), (
                            "workers still active before replacing controller files"
                        )
                        for folder in ("src", "scripts", "config", "docs", "examples", "systemd"):
                            shutil.copytree(package / folder, deployed / folder, dirs_exist_ok=True)
                        clear_controller_bytecode(deployed)
                        return 0

                    with patch("builtins.input", return_value="Y"):
                        assert (
                            update_with_recovery(
                                UpdateSession(entry=deployed / "scripts/dev-tools.ps1"), install
                            )
                            == 0
                        )
                assert "0.5.2" in invoke("--version").stdout
                restored = status(name)
                assert restored["services"]["api"]["phase"] == "running"
                assert restored["services"]["extra"]["phase"] == "stopped"
                assert all(
                    item["phase"] == "stopped" for item in status(names[1])["services"].values()
                )
                listing = json.loads(invoke("list", "--json").stdout)
                assert listing["projects"][0]["ports"] and listing["projects"][0]["types"] == [
                    "python"
                ]
                batch = json.loads(invoke("start", "--all", "--json").stdout)
                assert {item["name"]: item["status"] for item in batch["projects"]} == {
                    name: "skipped",
                    names[1]: "started",
                }
                assert status(name)["services"]["api"]["pid"] == restored["services"]["api"]["pid"]
                repeated = json.loads(invoke("start", "--all", "--json").stdout)
                assert all(item["status"] == "skipped" for item in repeated["projects"])
                table = invoke("list").stdout
                assert table.splitlines()[1].endswith("State")
                assert "\x1b[32mRunning\x1b[0m" in table
                assert "\x1b" not in invoke("list", "--json").stdout
                print(
                    f"{native}: native progress/heartbeat and colored last state column verified; Y update restoration and batch start passed"
                )
            finally:
                for project in registered:
                    invoke("stop", project)
                    invoke("unregister", project)


if __name__ == "__main__":
    smoke()
