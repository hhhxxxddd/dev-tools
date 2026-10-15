"""Real environment contract on both entrypoints, with isolated native preferences/state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from transport_smoke import TransportDirectory


def smoke():
    if os.name != "nt":
        raise RuntimeError("run on Windows with both entrypoints installed")
    repository = Path(__file__).resolve().parents[2]
    distro = os.environ.get("DEV_TOOLS_DISTRO", "Ubuntu")

    def wsl(*arguments):
        return subprocess.check_output(
            ["wsl.exe", "-d", distro, "--exec", *arguments],
            env={**os.environ, "WSL_UTF8": "1"},
            text=True,
            encoding="utf-8",
            timeout=30,
        ).strip()

    def linux(path):
        return wsl("wslpath", "-u", str(path).replace("\\", "/"))

    with TransportDirectory(prefix="dev-tools-environment-") as temporary:
        root = Path(temporary)
        root.mkdir(exist_ok=True)
        editor = root / "fixture_editor.py"
        editor.write_text(
            "import sys\nfrom pathlib import Path\np=Path(sys.argv[1])\n"
            'p.write_text(p.read_text(encoding="utf-8")+"\\n# edited by target\\n", encoding="utf-8")\n',
            encoding="utf-8",
        )
        native_python = wsl("cat", "/opt/dev-tools/.host-python")
        roots = {"win": root / "win-preferences", "wsl": root / "wsl-preferences"}
        for target in ("win", "wsl"):
            config = roots[target] / "dev-tools/config.toml"
            config.parent.mkdir(parents=True)
            argv = (
                [sys.executable, str(editor)] if target == "win" else [native_python, linux(editor)]
            )
            tool = "git" if target == "win" else "python3"
            config.write_text(
                'language="en"\neditor='
                + json.dumps(argv)
                + '\n[sysinfo]\nsections=["tools"]\ntools=[{command="'
                + tool
                + '"}]\n[report]\ncollectors=[]\n',
                encoding="utf-8",
            )
        caller = root / "caller.toml"
        caller.write_text('language="en"\n[sysinfo]\nsections=[]\n', encoding="utf-8")
        windows_entry = [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(repository / "scripts/dev-tools.ps1"),
        ]
        wsl_entry = [
            "wsl.exe",
            "-d",
            distro,
            "-u",
            "root",
            "--cd",
            linux(root),
            "--exec",
            "bash",
            linux(repository / "scripts/dev-tools"),
        ]
        environment = os.environ.copy()
        environment.update(
            LOCALAPPDATA=str(roots["win"]),
            XDG_CONFIG_HOME=str(roots["wsl"]),
            DEV_TOOLS_REGISTRY_ROOT=str(root / "registry"),
            DEV_TOOLS_STATE_ROOT=str(root / "state"),
            DEV_TOOLS_CACHE_ROOT=str(root / "cache"),
            WSL_UTF8="1",
        )
        transferred = {
            "LOCALAPPDATA",
            "XDG_CONFIG_HOME",
            "DEV_TOOLS_REGISTRY_ROOT",
            "DEV_TOOLS_STATE_ROOT",
            "DEV_TOOLS_CACHE_ROOT",
        }
        wslenv = [
            item
            for item in environment.get("WSLENV", "").split(":")
            if item and item.split("/")[0] not in transferred
        ]
        environment["WSLENV"] = ":".join([*wslenv, *(name + "/p" for name in sorted(transferred))])

        def invoke(entry, *arguments, expected=0, explicit_caller=False, as_json=True):
            flags = (
                ["--config", str(caller) if entry is windows_entry else linux(caller)]
                if explicit_caller
                else []
            )
            result = subprocess.run(
                [*entry, *flags, *arguments],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
            if result.returncode != expected:
                raise AssertionError((arguments, result.returncode, result.stdout, result.stderr))
            return json.loads(result.stdout) if as_json else result.stdout + result.stderr

        for entry, native in ((windows_entry, "win"), (wsl_entry, "wsl")):
            value = invoke(entry, "list", "--json")
            assert value["environment"] == ("windows" if native == "win" else "wsl"), value
            assert invoke(entry, "list", "--all", "--json")["environment"] == "all"
            for target in ("win", "wsl"):
                config = invoke(entry, "config", "--json", "-e", target)
                assert config["state"] == "loaded", config
                assert "caller.toml" not in config["path"], config
                # An explicit caller config must not become the target config.
                if native != target:
                    remote_config = invoke(
                        entry, "-e", target, "config", "--json", explicit_caller=True
                    )
                    assert remote_config["path"] == config["path"], remote_config
                invoke(entry, "config", "check", "-e", target, as_json=False)
                invoke(entry, "config", "edit", "-e", target, as_json=False)
                assert "# edited by target" in (roots[target] / "dev-tools/config.toml").read_text(
                    encoding="utf-8"
                )
                information = invoke(entry, "sysinfo", "-e", target, "--json")
                assert [item["command"] for item in information["commands"]] == [
                    "git" if target == "win" else "python3"
                ], information
                filename = f"报告-{native}-{target}.json"
                report = invoke(
                    entry, "report", "-e", target, "--json", "--no-refresh", "--output=" + filename
                )
                assert not report["collectors"], report
                assert json.loads((root / filename).read_text(encoding="utf-8")) == report
                status = invoke(entry, "self", "status", "--json", "-e", target)
                assert status["schema_version"] == 1 and status["environment"] == (
                    "windows" if target == "win" else "wsl"
                ), status
                assert status["deployment"]["state"] == "installed", status
                assert invoke(entry, "ssh", "list", "-e", target, "--json")["complete"]
                # Only isolated empty recovery records are ever touched.
                recovery = root / "state/.controller-update/recovery.json"
                recovery.parent.mkdir(parents=True, exist_ok=True)
                recovery.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "environment": "windows" if target == "win" else "wsl",
                            "projects": [],
                            "phase": "restored",
                        }
                    ),
                    encoding="utf-8",
                )
                invoke(entry, "self", "recover", "-e", target, as_json=False)
                for command in (["self", "update"], ["upgrade"], ["self", "upgrade"]):
                    result = invoke(
                        entry,
                        *command,
                        "-e",
                        target,
                        "--source",
                        str(root) if native == "win" and target == "win" else linux(root),
                        "--yes",
                        expected=1,
                        as_json=False,
                    )
                    expected = (
                        "unverified controller checkout"
                        if command == ["self", "update"]
                        else "Source upgrade requires a verified Git checkout"
                    )
                    assert expected in result, result
            assert invoke(entry, "hosts", "list", "-e", "win", "--json")["environment"] == "windows"
            error = invoke(entry, "hosts", "list", "-e", "wsl", "--json", expected=1)
            assert "error" in error
    return {
        "native_defaults": True,
        "explicit_all": True,
        "bidirectional_helpers": True,
        "target_config_editor": True,
        "unicode_report_paths": True,
        "target_self_status": True,
        "bidirectional_recover": True,
        "bidirectional_update_source_validation": True,
    }


if __name__ == "__main__":
    print(json.dumps(smoke(), indent=2))
