"""Run in WSL as root: real Windows queries/actions with a sudo-cleaned PATH."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def smoke() -> dict:
    if os.name == "nt" or os.geteuid() != 0:
        raise RuntimeError("run with native WSL Python as root")
    repository = Path(__file__).resolve().parents[2]
    windows_source = subprocess.check_output(["wslpath", "-w", str(repository)], text=True).strip()
    if windows_source.startswith("\\\\"):
        raise RuntimeError("test checkout must be on a mounted Windows drive")
    output = repository / "dist"
    output.mkdir(exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="dev-tools-root-interop-") as native,
        tempfile.TemporaryDirectory(prefix="root-interop-", dir=output) as fixture,
    ):
        deployment, fixture = Path(native), Path(fixture)
        shutil.copytree(
            repository / "src/dev_tools",
            deployment / "src/dev_tools",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (deployment / "scripts").mkdir()
        shutil.copyfile(repository / "scripts/dev-tools", deployment / "scripts/dev-tools")
        (deployment / ".host-python").write_text(sys.executable, encoding="utf-8")
        (deployment / ".windows-source").write_text(windows_source, encoding="utf-8")
        preferences = fixture / "caller.toml"
        preferences.write_text('language="en"\n', encoding="utf-8")
        project = fixture / "项目 with spaces"
        project.mkdir()
        (project / "dev-tools.toml").write_text(
            'schema=1\nname="root-interop-fixture"\ntoolchain="system"\n', encoding="utf-8"
        )
        variables = {
            "DEV_TOOLS_REGISTRY_ROOT": str(fixture / "registry"),
            "DEV_TOOLS_STATE_ROOT": str(fixture / "state"),
            "DEV_TOOLS_CACHE_ROOT": str(fixture / "cache"),
        }
        wslenv = ":".join(name + "/p" for name in variables)
        # sudo deliberately drops Windows PATH, WSL_INTEROP, and caller preferences.
        entry = [
            "sudo",
            "-n",
            "env",
            "WSLENV=" + wslenv,
            *(f"{key}={value}" for key, value in variables.items()),
            "bash",
            str(deployment / "scripts/dev-tools"),
            "--config",
            str(preferences),
        ]

        def invoke(*args, json_result=True):
            result = subprocess.run(
                [*entry, *args],
                cwd=project,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=60,
            )
            if result.returncode:
                raise AssertionError(
                    f"{args}: {result.returncode}\n{result.stdout}\n{result.stderr}"
                )
            return json.loads(result.stdout) if json_result else result.stdout

        version = invoke("-e", "win", "--version", json_result=False)
        if not version.startswith("dev-tools "):
            raise AssertionError(version)
        for command in ("ssh", "hosts"):
            value = invoke(command, "list", "-e", "win", "--json")
            if value["environment"] != "windows":
                raise AssertionError(value)
        names = ["root-interop-fixture", "root-interop-renamed"]
        registered = None
        try:
            invoke("register", ".", "-e", "win", "--json")
            registered = names[0]
            listed = invoke("list", "-e", "win", "--json")
            if [item["name"] for item in listed["projects"]] != [registered]:
                raise AssertionError(listed)
            invoke("rename", registered, names[1], "-e", "win", "--json")
            registered = names[1]
            value = invoke("show", registered, "-e", "win", "--json")
            if value["binding"]["name"] != registered:
                raise AssertionError(value)
        finally:
            if registered:
                invoke("unregister", registered, "-e", "win", "--json")
        if invoke("list", "-e", "win", "--json")["projects"]:
            raise AssertionError("test registration was not removed")
    return {
        "root_sudo": True,
        "native_queries": True,
        "register_rename_unregister": True,
        "isolated_state": True,
    }


if __name__ == "__main__":
    print(json.dumps(smoke(), indent=2))
