"""Real native failure/summary paths using only disposable registrations and workers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dev_tools.projects.config import render_toml


def smoke():
    repository = Path(__file__).resolve().parents[2]
    native = "win" if os.name == "nt" else "wsl"
    if native == "wsl":
        assert os.geteuid() == 0
        assert all(shutil.which(command) for command in ("git", "rsync", "runuser"))
    with tempfile.TemporaryDirectory(prefix="dev-tools-diagnostic-") as temporary:
        root = Path(temporary).resolve()
        controller = root / "controller"
        shutil.copytree(repository / "scripts", controller / "scripts")
        shutil.copytree(repository / "src/dev_tools", controller / "src/dev_tools")
        binding = ".host-python.win" if native == "win" else ".host-python"
        (controller / binding).write_text(str(Path(sys.executable).resolve()), encoding="utf-8")
        config = root / "preferences.toml"
        config.write_text('language="en"\n', encoding="utf-8")
        environment = dict(
            os.environ,
            PYTHONPATH=str(controller / "src"),
            PYTHONUTF8="1",
            DEV_TOOLS_CONFIG=str(config),
            DEV_TOOLS_REGISTRY_ROOT=str(root / "registry"),
            DEV_TOOLS_STATE_ROOT=str(root / "state"),
            DEV_TOOLS_CACHE_ROOT=str(root / "cache"),
            DEV_TOOLS_TEST_TRANSIENT="1",
        )
        source = root / "source"
        source.mkdir()
        name = "diagnostic-" + root.name[-10:].lower()
        (source / "dev-tools.toml").write_text(
            render_toml(
                {
                    "schema": 1,
                    "name": name,
                    "toolchain": "system",
                    "rebuild_on_branch": False,
                    "services": {
                        "api": {
                            "command": [
                                sys.executable,
                                "-I",
                                "-c",
                                "print('RuntimeError: native runtime dependency could not be loaded; token=SECRET', flush=True); raise SystemExit(7)",
                            ],
                            "restart": "never",
                            "health": {"timeout": 3},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        entry = (
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(controller / "scripts/dev-tools.ps1"),
            ]
            if native == "win"
            else ["bash", str(controller / "scripts/dev-tools")]
        )

        def invoke(*args, expected=0):
            result = subprocess.run(
                [*entry, "-e", native, *args],
                env=environment,
                cwd=source,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                check=False,
            )
            assert result.returncode == expected, (args, result.stdout, result.stderr)
            return result

        invoke("register", str(source), "--name", name)
        try:
            # Without a health probe, an exited process can briefly count as running.
            # Make readiness deterministic through an HTTP endpoint that is unreachable.
            import socket

            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            declaration = source / "dev-tools.toml"
            declaration.write_text(
                declaration.read_text(encoding="utf-8") + f'http = "http://127.0.0.1:{port}/"\n',
                encoding="utf-8",
            )
            failed = invoke("start", name, "--json", expected=1)
            document = json.loads(failed.stdout)
            assert document["summary"] and document["diagnostics"]
            assert "native runtime dependency" in failed.stdout
            assert "SECRET" not in failed.stdout
            status = json.loads(invoke("status", name, "--json").stdout)
            assert not status["ready"] and status["summary"]
            assert any(
                fact["code"] == "process.exited" and fact["evidence"]["exit_code"] == 7
                for fact in status["diagnostics"]
            )
            listing = json.loads(invoke("list", "--json").stdout)
            assert (
                len(listing["projects"]) == 1
                and listing["projects"][0]["summary"] == status["summary"]
            )
            assert "Reason summary" in invoke("status", name).stdout
            assert status["summary"] in invoke("list").stdout
            config.write_text('language="zh"\n', encoding="utf-8")
            chinese = json.loads(invoke("status", name, "--json").stdout)
            assert "进程退出" in chinese["summary"]
            assert chinese["diagnostics"] == status["diagnostics"]
            assert "原因摘要：" in invoke("list").stdout
            assert "原因摘要：" in invoke("status", name).stdout
            failed_chinese = invoke("start", name, expected=1)
            assert "原因摘要：" in failed_chinese.stderr and "SECRET" not in failed_chinese.stderr
            config.write_text('language="en"\n', encoding="utf-8")
            failed_text = invoke("start", name, expected=1)
            assert "Reason summary" in failed_text.stderr and "Inspect status" in failed_text.stderr
            invoke("stop", name)
            assert json.loads(invoke("status", name, "--json").stdout)["summary"] is None
            return {
                "environment": native,
                "native_entrypoint": True,
                "failure_json": True,
                "failure_text": True,
                "list_status_summary": True,
                "stop_clears_summary": True,
                "language_switching": True,
            }
        finally:
            invoke("unregister", name)


if __name__ == "__main__":
    print(json.dumps(smoke(), indent=2))
