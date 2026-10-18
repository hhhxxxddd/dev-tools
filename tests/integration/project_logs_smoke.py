"""Native and bidirectional project logs using isolated controllers and bindings."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def deploy(repository, root, windows_source=None):
    native = "win" if os.name == "nt" else "wsl"
    for name in ("src", "scripts", "config"):
        shutil.copytree(
            repository / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    (root / (".host-python.win" if native == "win" else ".host-python")).write_text(
        sys.executable, encoding="utf-8"
    )
    if windows_source:
        (root / ".windows-source").write_text(windows_source, encoding="utf-8")
    preferences = root / "preferences.toml"
    preferences.write_text(
        'language="en"\n[wsl]\ndistro='
        + json.dumps(os.environ.get("DEV_TOOLS_DISTRO", "Ubuntu"))
        + "\n",
        encoding="utf-8",
    )
    source, registry, state = root / "project", root / "registry", root / "state"
    source.mkdir()
    registry.mkdir()
    declaration = source / "dev-tools.toml"
    declaration.write_text(
        'schema=1\nname="logs-fixture"\ntoolchain="system"\n[services.api]\ncommand=["never-run"]\n[services.web]\ncommand=["never-run"]\n[tasks.install]\ncommand=["never-run"]\n',
        encoding="utf-8",
    )
    binding = {
        "schema_version": 3,
        "name": "logs-fixture",
        "environment": "windows" if native == "win" else "wsl",
        "source": str(source),
        "workspace": str(source if native == "win" else root / "cache/workspace"),
        "state": str(state / "fixture"),
        "run_user": "",
        "cache_root": None if native == "win" else str(root / "cache"),
    }
    binding_file = registry / "logs-fixture.json"
    binding_file.write_text(json.dumps(binding), encoding="utf-8")
    logs = state / "fixture/logs"
    (logs / "tasks").mkdir(parents=True)
    for name in ("api", "web"):
        (logs / (name + ".log")).write_text(f"older\n{native}-{name}\n", encoding="utf-8")
    (logs / "tasks/install.log").write_text(f"{native}-install\n", encoding="utf-8")
    controller = root / "src/dev_tools/projects/cli.py"
    with controller.open("a", encoding="utf-8") as stream:
        stream.write(
            f"\nos.environ.update(DEV_TOOLS_REGISTRY_ROOT={str(registry)!r}, DEV_TOOLS_STATE_ROOT={str(state)!r}, DEV_TOOLS_CONFIG={str(preferences)!r})\n"
        )
    # Only the isolated follower ends itself after emitting new data. Exercise the
    # actual polling, decoding, rendering and native/remote Ctrl+C status paths.
    reader = root / "src/dev_tools/projects/logfiles.py"
    with reader.open("a", encoding="utf-8") as stream:
        stream.write(f"""
from types import SimpleNamespace as _FixtureNamespace
_fixture_polls = 0
def _fixture_pause(seconds):
    global _fixture_polls
    _fixture_polls += 1
    if _fixture_polls == 1:
        with Path({str(logs / "api.log")!r}).open('a', encoding='utf-8') as stream:
            stream.write({(native + "-api-live\n")!r})
        Path({str(logs / "__watch.log")!r}).write_text({(native + "-watch-live\n")!r}, encoding='utf-8')
    else:
        raise KeyboardInterrupt
time = _FixtureNamespace(sleep=_fixture_pause)
""")
    return {"root": str(root), "declaration": str(declaration), "binding": str(binding_file)}


def smoke():
    if os.name != "nt":
        raise RuntimeError("run from Windows with both native controller interpreters installed")
    from transport_smoke import TransportDirectory

    repository = Path(__file__).resolve().parents[2]
    distro = os.environ.get("DEV_TOOLS_DISTRO", "Ubuntu")
    environment = os.environ.copy()
    environment["WSL_UTF8"] = "1"

    def wsl(*args):
        return subprocess.check_output(
            ["wsl.exe", "-d", distro, "--exec", *args],
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=60,
        ).strip()

    def linux(path):
        return wsl("wslpath", "-u", str(path).replace("\\", "/"))

    native_python = wsl("cat", "/opt/dev-tools/.host-python")
    native_root = None
    with TransportDirectory(prefix="dev-tools-logs-smoke-") as directory:
        shared = Path(directory)
        windows = deploy(repository, shared / "windows-controller")
        try:
            native = json.loads(
                wsl(native_python, linux(Path(__file__)), "--setup", windows["root"])
            )
            native_root = native["root"]
            relay = Path(windows["root"]) / "scripts/dev-tools-wsl.ps1"
            source = relay.read_text(encoding="utf-8")
            assert source.count("dev-tools -e wsl @Arguments") == 2
            relay.write_text(
                source.replace(
                    "dev-tools -e wsl @Arguments",
                    "bash '"
                    + native_root.replace("'", "''")
                    + "/scripts/dev-tools' -e wsl @Arguments",
                ),
                encoding="utf-8",
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

            def invoke(entry, *args, status=0):
                result = subprocess.run(
                    [*entry, *args],
                    cwd=shared,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=60,
                    check=False,
                )
                assert result.returncode == status, (
                    args,
                    result.returncode,
                    result.stdout,
                    result.stderr,
                )
                return result.stdout

            for entry in (win_entry, wsl_entry):
                for target in ("win", "wsl"):
                    result = invoke(entry, "logs", "logs-fixture", "-e", target, "--lines", "1")
                    assert f"[api] {target}-api" in result and f"[web] {target}-web" in result, (
                        result
                    )
                    result = invoke(
                        entry, "logs", "logs-fixture", "api", "-e", target, "--lines", "1"
                    )
                    assert f"{target}-api" in result and "[api]" not in result, result
                    result = invoke(entry, "logs", "logs-fixture", "--task", "-e", target)
                    assert f"[install] {target}-install" in result, result
                    result = invoke(
                        entry,
                        "logs",
                        "logs-fixture",
                        "--follow",
                        "-e",
                        target,
                        "--lines",
                        "1",
                        status=130,
                    )
                    assert f"[api] {target}-api-live" in result, result
                    assert f"[__watch] {target}-watch-live" in result, result
            assert not (Path(windows["declaration"]).parent / "mise.toml").exists()
        finally:
            if native_root:
                wsl(native_python, linux(Path(__file__)), "--cleanup", native_root)
    return {
        "native_win_wsl": True,
        "bidirectional_aggregate": True,
        "single_service_raw": True,
        "task_logs": True,
        "follow_missing_and_new_output": True,
        "interrupt_130": True,
        "isolated_bindings": True,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        root = Path(tempfile.mkdtemp(prefix="dev-tools-logs-smoke-"))
        print(json.dumps(deploy(Path(__file__).resolve().parents[2], root, sys.argv[2])))
    elif len(sys.argv) > 1 and sys.argv[1] == "--cleanup":
        target = Path(sys.argv[2])
        if (
            target.is_symlink()
            or target.resolve() != target
            or target.parent != Path(tempfile.gettempdir())
            or not target.name.startswith("dev-tools-logs-smoke-")
        ):
            raise RuntimeError("invalid isolated log controller cleanup path")
        shutil.rmtree(target)
    else:
        print(json.dumps(smoke(), indent=2))
