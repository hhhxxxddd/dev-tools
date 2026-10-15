"""Real bidirectional SSH CLI transport with isolated configs, editors and clients."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def deploy(repository: Path, root: Path):
    for name in ("src", "scripts", "config"):
        shutil.copytree(
            repository / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    native = "win" if os.name == "nt" else "wsl"
    (root / (".host-python.win" if native == "win" else ".host-python")).write_text(
        sys.executable, encoding="utf-8"
    )
    home = root / "fixture-home"
    config = home / ".ssh/config"
    included = home / ".ssh/conf.d/servers.conf"
    included.parent.mkdir(parents=True)
    config.write_bytes(b'# preserve root\r\nInclude "conf.d/*.conf"\r\n')
    included.write_bytes(
        b"# preserve included\r\nHost fixture\r\n HostName localhost\r\n User old\r\n"
    )
    source = root / "src/dev_tools/ssh.py"
    content = source.read_text(encoding="utf-8")
    selection = 'client = shutil.which("ssh.exe" if os.name == "nt" else "ssh")'
    if content.count(selection) != 1:
        raise RuntimeError("SSH client fixture selector no longer matches")
    # Keep the production connection code and native process handling; replace
    # discovery only, so a tiny native Python client can exercise stdin/status.
    content = content.replace(selection, "client = sys.executable")
    content += f"\n\ndef native_identity():\n    return Path({str(home)!r}), 'fixture-user', Path({str(root / 'system/ssh_config')!r})\n"
    source.write_text(content, encoding="utf-8")
    return {"root": str(root), "config": str(config), "included": str(included)}


def smoke():
    if os.name != "nt":
        raise RuntimeError("run from Windows with both native controller interpreters installed")
    from transport_smoke import TransportDirectory

    repository = Path(__file__).resolve().parents[2]
    distro = os.environ.get("DEV_TOOLS_DISTRO", "Ubuntu")
    environment = os.environ.copy()
    environment["WSL_UTF8"] = "1"

    def wsl(*arguments):
        return subprocess.check_output(
            ["wsl.exe", "-d", distro, "--exec", *arguments],
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=60,
        ).strip()

    def linux(path):
        return wsl("wslpath", "-u", str(path).replace("\\", "/"))

    native_python = wsl("cat", "/opt/dev-tools/.host-python")
    native_root = None
    with TransportDirectory(prefix="dev-tools-ssh-smoke-") as temporary:
        shared = Path(temporary)
        windows = deploy(repository, shared / "windows-controller")
        try:
            native = json.loads(
                wsl(native_python, linux(Path(__file__)), "--setup", str(windows["root"]))
            )
            native_root = native["root"]
            relay = Path(windows["root"]) / "scripts/dev-tools-wsl.ps1"
            source = relay.read_text(encoding="utf-8")
            invocation = "dev-tools -e wsl @Arguments"
            if source.count(invocation) != 2:
                raise RuntimeError("WSL relay fixture selector no longer matches")
            source = source.replace(
                invocation,
                "bash '" + native_root.replace("'", "''") + "/scripts/dev-tools' -e wsl @Arguments",
            )
            relay.write_text(source, encoding="utf-8")
            editor = shared / "fixture_editor.py"
            editor.write_text(
                "import sys\nfrom pathlib import Path\np=Path(sys.argv[1])\n"
                'p.write_bytes(p.read_bytes().replace(b"User old", b"User updated"))\n',
                encoding="utf-8",
            )
            client = shared / "fixture_client.py"
            client.write_text(
                'import json,sys\nprint(json.dumps({"platform":sys.platform,"answer":input()}),flush=True)\nsys.exit(7)\n',
                encoding="utf-8",
            )
            windows_preferences, wsl_preferences = (
                shared / "win-preferences",
                shared / "wsl-preferences",
            )
            for directory, argv in (
                (windows_preferences, [sys.executable, str(editor)]),
                (wsl_preferences, [native_python, linux(editor)]),
            ):
                config = directory / "dev-tools/config.toml"
                config.parent.mkdir(parents=True)
                config.write_text(
                    'language="en"\neditor='
                    + json.dumps(argv)
                    + "\n[wsl]\ndistro="
                    + json.dumps(distro)
                    + "\n",
                    encoding="utf-8",
                )
            environment.update(
                LOCALAPPDATA=str(windows_preferences),
                XDG_CONFIG_HOME=str(wsl_preferences),
                DEV_TOOLS_CONFIG="",
            )
            variables = {"LOCALAPPDATA", "XDG_CONFIG_HOME", "DEV_TOOLS_CONFIG"}
            wslenv = [
                entry
                for entry in environment.get("WSLENV", "").split(":")
                if entry and entry.split("/")[0] not in variables
            ]
            environment["WSLENV"] = ":".join(
                [*wslenv, "LOCALAPPDATA/p", "XDG_CONFIG_HOME/p", "DEV_TOOLS_CONFIG"]
            )
            win_entry = [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(Path(windows["root"]) / "scripts/dev-tools.ps1"),
            ]
            wsl_entry = [
                "wsl.exe",
                "-d",
                distro,
                "--cd",
                linux(shared),
                "--exec",
                "bash",
                native_root + "/scripts/dev-tools",
            ]

            def invoke(entry, *arguments, status=0, json_result=True):
                result = subprocess.run(
                    [*entry, *arguments],
                    input=b"Y\n",
                    cwd=shared,
                    env=environment,
                    capture_output=True,
                    check=False,
                    timeout=90,
                )
                output = result.stdout.decode("utf-8", errors="replace")
                errors = result.stderr.decode("utf-8", errors="replace")
                if result.returncode != status:
                    raise AssertionError((arguments, result.returncode, output, errors))
                return json.loads(output) if json_result else output

            original_windows = Path(windows["config"]).read_bytes()
            for entry in (win_entry, wsl_entry):
                for target in ("win", "wsl"):
                    listed = invoke(entry, "ssh", "list", "-e", target, "--json")
                    assert [host["name"] for host in listed["hosts"]] == ["fixture"], listed
                    shown = invoke(entry, "ssh", "show", "fixture", "-e", target, "--json")
                    assert shown["operation"] == "show" and shown["host"]["line"] == 2, shown
                    assert shown["host"]["source"] == (
                        str(Path(windows["included"]).resolve())
                        if target == "win"
                        else native["included"]
                    ), shown
                    invoke(entry, "ssh", "edit", "fixture", "-e", target, json_result=False)
                    updated = invoke(entry, "ssh", "show", "fixture", "-e", target, "--json")
                    assert updated["host"]["user"] == "updated", updated
                    connection = invoke(
                        entry, "ssh", "connect", client.name, "-e", target, status=7
                    )
                    assert connection == {
                        "platform": "win32" if target == "win" else "linux",
                        "answer": "Y",
                    }, connection
            assert Path(windows["config"]).read_bytes() == original_windows
            assert (
                wsl(
                    native_python,
                    "-c",
                    "from pathlib import Path;import sys;assert Path(sys.argv[1]).read_bytes()==b'# preserve root\\r\\nInclude \"conf.d/*.conf\"\\r\\n'",
                    native["config"],
                )
                == ""
            )
        finally:
            if native_root is not None:
                wsl(native_python, linux(Path(__file__)), "--cleanup", native_root)
    return {
        "win_to_wsl": True,
        "wsl_to_win": True,
        "show_sources": True,
        "native_editors": True,
        "include_preserved": True,
        "interactive_stdin": True,
        "native_exit_status": True,
        "isolated_configs_clients": True,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        repository = Path(__file__).resolve().parents[2]
        native = deploy(repository, Path(tempfile.mkdtemp(prefix="dev-tools-ssh-smoke-")))
        (Path(native["root"]) / ".windows-source").write_text(sys.argv[2], encoding="utf-8")
        print(json.dumps(native))
    elif len(sys.argv) > 1 and sys.argv[1] == "--cleanup":
        target = Path(sys.argv[2])
        if (
            target.is_symlink()
            or target.resolve() != target
            or target.parent != Path(tempfile.gettempdir())
            or not target.name.startswith("dev-tools-ssh-smoke-")
        ):
            raise RuntimeError("invalid isolated SSH controller cleanup path")
        shutil.rmtree(target)
    else:
        print(json.dumps(smoke(), indent=2))
