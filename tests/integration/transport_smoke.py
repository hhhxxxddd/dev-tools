"""Real bidirectional CLI transport using isolated registrations and Unicode paths."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from dev_tools.projects.config import render_toml


class TransportDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        deadline = time.monotonic() + 10
        while True:
            try:
                super().cleanup()
                break
            except PermissionError:
                # WSL relay handles can close slightly after wsl.exe returns.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        cache = getattr(self, "native_cache", None)
        if cache:
            subprocess.run(
                [
                    "wsl.exe",
                    "-d",
                    self.distro,
                    "-u",
                    "root",
                    "--exec",
                    "bash",
                    "-c",
                    'cache="$1"; [[ "$cache" =~ ^/tmp/dev-tools-transport-[A-Za-z0-9]+$ && ! -L "$cache" && $(readlink -f -- "$cache") == "$cache" ]] || exit 1; rm -rf -- "$cache"',
                    "bash",
                    cache,
                ],
                check=True,
                timeout=30,
            )
            self.native_cache = None


def smoke() -> dict:
    if os.name != "nt":
        raise RuntimeError("run from Windows with WSL and PowerShell 7 available")
    repository = Path(__file__).resolve().parents[2]
    distro = os.environ.get("DEV_TOOLS_DISTRO", "Ubuntu")
    windows_entry = [
        "pwsh",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(repository / "scripts/dev-tools.ps1"),
    ]

    def linux_path(path: Path) -> str:
        return subprocess.check_output(
            ["wsl.exe", "-d", distro, "--exec", "wslpath", "-u", str(path).replace("\\", "/")],
            text=True,
            encoding="utf-8",
        ).strip()

    directory = TransportDirectory(prefix="dev-tools-transport-")
    with directory as temporary:
        directory.distro = distro
        native_cache = subprocess.check_output(
            [
                "wsl.exe",
                "-d",
                distro,
                "--exec",
                "mktemp",
                "-d",
                "/tmp/dev-tools-transport-XXXXXXXX",
            ],
            text=True,
            encoding="utf-8",
            timeout=30,
        ).strip()
        if not re.fullmatch(r"/tmp/dev-tools-transport-[A-Za-z0-9]+", native_cache):
            raise RuntimeError("Unexpected native test cache path")
        directory.native_cache = native_cache
        root = Path(temporary).resolve()
        preferences = root / "settings.toml"
        preferences.write_text(
            'language = "en"\n[wsl]\ndistro = '
            + json.dumps(distro)
            + '\n[sysinfo]\nsections = ["tools"]\ntools = [{command = "git"}]\n[report]\ncollectors = []\n',
            encoding="utf-8",
        )
        windows_entry.extend(["--config", str(preferences)])
        source = root / "项目 & spaces"
        source.mkdir()
        child = source / "nested"
        child.mkdir()
        (source / "dev-tools.toml").write_text(
            render_toml({"schema": 1, "name": "transport-fixture", "toolchain": "system"}),
            encoding="utf-8",
        )
        (source / ".nvmrc").write_text("22\n", encoding="utf-8")
        linux_child = linux_path(child)
        wsl_entry = [
            "wsl.exe",
            "-d",
            distro,
            "--cd",
            linux_child,
            "--exec",
            "bash",
            linux_path(repository / "scripts/dev-tools"),
            "--config",
            linux_path(preferences),
        ]
        environment = os.environ.copy()
        environment["DEV_TOOLS_CACHE_ROOT"] = native_cache
        variables = {"DEV_TOOLS_REGISTRY_ROOT", "DEV_TOOLS_STATE_ROOT", "DEV_TOOLS_CACHE_ROOT"}
        wslenv = [
            entry
            for entry in environment.get("WSLENV", "").split(":")
            if entry and entry.split("/")[0] not in variables
        ]
        environment["WSLENV"] = ":".join(
            [
                *wslenv,
                "DEV_TOOLS_CACHE_ROOT/u",
                *(name + "/p" for name in sorted(variables - {"DEV_TOOLS_CACHE_ROOT"})),
            ]
        )

        def invoke(entry: list[str], *arguments: str, cwd: Path = child) -> dict:
            result = subprocess.run(
                [*entry, *arguments],
                cwd=repository if entry is wsl_entry else cwd,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",  # wsl.exe can mix UTF-16 diagnostics with native UTF-8 stderr.
                timeout=60,
                check=False,
            )
            if result.returncode:
                raise AssertionError(
                    f"{arguments}: {result.returncode}\n{result.stdout}\n{result.stderr}"
                )
            return json.loads(result.stdout)

        for target in ("win", "wsl"):
            environment["DEV_TOOLS_REGISTRY_ROOT"] = str(root / (target + "-registry"))
            environment["DEV_TOOLS_STATE_ROOT"] = str(root / (target + "-state"))
            registered = False
            try:
                invoke(windows_entry, "-e", target, "register", "..", "--json")
                registered = True
                windows_view = invoke(windows_entry, "show", "-e", target, "--json")
                wsl_view = invoke(wsl_entry, "-e", target, "show", "--json")
                if windows_view["binding"] != wsl_view["binding"]:
                    raise AssertionError((windows_view, wsl_view))
                if windows_view["binding"]["environment"] != (
                    "windows" if target == "win" else "wsl"
                ):
                    raise AssertionError(windows_view)
                listed = invoke(windows_entry, "list", "--env=" + target, "--json")
                if [project["name"] for project in listed["projects"]] != ["transport-fixture"]:
                    raise AssertionError(listed)
                for entry in (windows_entry, wsl_entry):
                    plan = invoke(entry, "check", "-e", target, "--json")
                    if plan["binding"] != windows_view["binding"]:
                        raise AssertionError(plan)
                    error = subprocess.run(
                        [*entry, "show", "missing-transport-fixture", "-e", target],
                        cwd=repository if entry is wsl_entry else child,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                        check=False,
                    )
                    if error.returncode != 1 or "project is not registered" not in error.stderr:
                        raise AssertionError(
                            "caller configuration language was lost: " + error.stderr
                        )
                # Read-only scanning and checks neither create declarations nor prepare tasks.
                scanned = invoke(windows_entry, "scan", "..", "-e", target, "--json")
                if scanned["action"] != "scan" or (source / "mise.toml").exists():
                    raise AssertionError(scanned)
            finally:
                if registered:
                    invoke(windows_entry, "unregister", "-e", target, "--json")
                    if invoke(windows_entry, "list", "-e", target, "--json")["projects"]:
                        raise AssertionError("transport left a native registration")
        if not (source / "dev-tools.toml").is_file():
            raise AssertionError("unregister deleted source")
        windows_help = subprocess.check_output(
            [*windows_entry, "help"], text=True, encoding="utf-8"
        )
        wsl_help = subprocess.check_output([*wsl_entry, "help"], text=True, encoding="utf-8")
        if windows_help != wsl_help:
            raise AssertionError("native help differs")
        for entry in (windows_entry, wsl_entry):
            information = invoke(entry, "sysinfo", "--json")
            if information["system"] or [item["command"] for item in information["commands"]] != [
                "git"
            ]:
                raise AssertionError("sysinfo ignored preferences")
            if invoke(entry, "report", "--json")["collectors"]:
                raise AssertionError("disabled report collectors ran")
        return {
            "win_to_wsl": True,
            "wsl_to_win": True,
            "implicit_name": True,
            "list_unregister": True,
            "shared_help": True,
            "readonly_check": True,
            "unified_config": True,
            "forwarded_language": True,
        }


if __name__ == "__main__":
    print(json.dumps(smoke(), ensure_ascii=False, indent=2))
