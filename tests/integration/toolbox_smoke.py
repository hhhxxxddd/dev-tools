"""Real ports/which/net queries through isolated Win/WSL controllers and local servers."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


def deploy(repository: Path, root: Path) -> Path:
    for name in ("src", "scripts", "config"):
        shutil.copytree(
            repository / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    (root / (".host-python.win" if os.name == "nt" else ".host-python")).write_text(
        sys.executable, encoding="utf-8"
    )
    return root


def serve():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *arguments):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        print(json.dumps({"port": server.server_port, "pid": os.getpid()}), flush=True)
        server.serve_forever()


def smoke():
    if os.name != "nt":
        raise RuntimeError("run from Windows with both native Python hosts installed")
    from transport_smoke import TransportDirectory

    repository = Path(__file__).resolve().parents[2]
    distro = os.environ.get("DEV_TOOLS_DISTRO", "Ubuntu")
    environment = {**os.environ, "WSL_UTF8": "1"}

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
    children = []
    with TransportDirectory(prefix="dev-tools-toolbox-smoke-") as temporary:
        shared = Path(temporary)
        windows = deploy(repository, shared / "windows-controller")
        try:
            native_root = wsl(native_python, linux(Path(__file__)), "--setup", str(windows))
            relay = windows / "scripts/dev-tools-wsl.ps1"
            source = relay.read_text(encoding="utf-8")
            invocation = "dev-tools -e wsl @Arguments"
            if source.count(invocation) != 2:
                raise RuntimeError("isolated WSL relay fixture no longer matches")
            relay.write_text(
                source.replace(
                    invocation,
                    "bash '"
                    + native_root.replace("'", "''")
                    + "/scripts/dev-tools' -e wsl @Arguments",
                ),
                encoding="utf-8",
            )
            preferences = shared / "preferences"
            config = preferences / "dev-tools/config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                'language="zh"\n[wsl]\ndistro=' + json.dumps(distro) + "\n", encoding="utf-8"
            )
            environment.update(
                LOCALAPPDATA=str(preferences),
                XDG_CONFIG_HOME=str(preferences),
                DEV_TOOLS_REGISTRY_ROOT=str(shared / "registry"),
                DEV_TOOLS_STATE_ROOT=str(shared / "state"),
            )
            variables = {
                "LOCALAPPDATA",
                "XDG_CONFIG_HOME",
                "DEV_TOOLS_REGISTRY_ROOT",
                "DEV_TOOLS_STATE_ROOT",
                "DEV_TOOLS_CONFIG",
            }
            inherited = [
                item
                for item in environment.get("WSLENV", "").split(":")
                if item and item.split("/")[0] not in variables
            ]
            environment["WSLENV"] = ":".join(
                [
                    *inherited,
                    *[name + "/p" for name in variables if name != "DEV_TOOLS_CONFIG"],
                    "DEV_TOOLS_CONFIG",
                ]
            )
            environment["DEV_TOOLS_CONFIG"] = ""
            win_entry = [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(windows / "scripts/dev-tools.ps1"),
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

            def invoke(entry, *arguments):
                result = subprocess.run(
                    [*entry, *arguments],
                    env=environment,
                    cwd=shared,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=90,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )
                if result.returncode:
                    raise AssertionError(
                        (arguments, result.returncode, result.stdout, result.stderr)
                    )
                return json.loads(result.stdout) if "--json" in arguments else result.stdout

            for target, entry in (
                ("win", [sys.executable, str(Path(__file__)), "--server"]),
                (
                    "wsl",
                    [
                        "wsl.exe",
                        "-d",
                        distro,
                        "--exec",
                        native_python,
                        linux(Path(__file__)),
                        "--server",
                    ],
                ),
            ):
                process = subprocess.Popen(
                    entry,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=environment,
                )
                children.append((target, process, None))
                facts = json.loads(process.stdout.readline())
                children[-1] = (target, process, facts["pid"])
                for caller in (win_entry, wsl_entry):
                    assert invoke(caller, "which", "-e", target) == invoke(
                        caller, "which", "-h", "-e", target
                    )
                    found = invoke(
                        caller,
                        "which",
                        "pwsh" if target == "win" else "bash",
                        "-e",
                        target,
                        "--json",
                    )
                    assert found["found"] and found["environment"] == (
                        "windows" if target == "win" else "wsl"
                    ), found
                    sockets = invoke(
                        caller,
                        "ports",
                        "show",
                        str(facts["port"]),
                        "--protocol",
                        "tcp",
                        "-e",
                        target,
                        "--json",
                    )
                    assert any(row["pid"] == facts["pid"] for row in sockets["entries"]), sockets
                    check = invoke(
                        caller,
                        "net",
                        "check",
                        f"http://127.0.0.1:{facts['port']}/health?token=SECRET",
                        "--timeout",
                        "3",
                        "-e",
                        target,
                        "--json",
                    )
                    assert check["healthy"] and check["stages"][-1]["http_status"] == 204, check
                    assert "SECRET" not in json.dumps(check)
                    info = invoke(caller, "net", "info", "-e", target, "--json")
                    assert (
                        isinstance(info["dns_servers"], list)
                        and info["proxy_scope"] == "environment"
                    ), info
        finally:
            for target, process, pid in children:
                if target == "wsl" and pid:
                    wsl(native_python, linux(Path(__file__)), "--stop-server", str(pid))
                elif process.poll() is None:
                    process.terminate()
                process.communicate(timeout=10)
            if native_root:
                wsl(native_python, linux(Path(__file__)), "--cleanup", native_root)
    return {
        "win_native": True,
        "wsl_native": True,
        "win_to_wsl": True,
        "wsl_to_win": True,
        "real_socket_pids": True,
        "local_http_head": True,
        "query_redaction": True,
        "isolated_controllers": True,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        root = deploy(
            Path(__file__).resolve().parents[2],
            Path(tempfile.mkdtemp(prefix="dev-tools-toolbox-smoke-")),
        )
        (root / ".windows-source").write_text(sys.argv[2], encoding="utf-8")
        print(root)
    elif len(sys.argv) > 1 and sys.argv[1] == "--cleanup":
        root = Path(sys.argv[2])
        if (
            root.is_symlink()
            or root.resolve() != root
            or root.parent != Path(tempfile.gettempdir())
            or not root.name.startswith("dev-tools-toolbox-smoke-")
        ):
            raise RuntimeError("invalid isolated toolbox controller cleanup path")
        shutil.rmtree(root)
    elif len(sys.argv) > 1 and sys.argv[1] == "--stop-server":
        pid = int(sys.argv[2])
        command = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        if os.fsencode(str(Path(__file__))) not in command or b"--server" not in command:
            raise RuntimeError("server process identity changed")
        os.kill(pid, signal.SIGTERM)
    elif len(sys.argv) > 1 and sys.argv[1] == "--server":
        serve()
    else:
        print(json.dumps(smoke(), indent=2))
