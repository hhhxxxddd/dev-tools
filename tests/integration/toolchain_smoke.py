"""Use already-installed runtimes only; exercise native Maven/npm wrappers and Java/Node/Python."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from dev_tools.projects.config import render_toml


def smoke() -> dict:
    inventory = json.loads(
        subprocess.check_output(
            ["mise", "ls", "--installed", "--json"], text=True, encoding="utf-8"
        )
    )
    tools = {}
    for name in ("java", "maven", "node", "python"):
        versions = [item["version"] for item in inventory.get(name, []) if item.get("installed")]
        if not versions:
            raise RuntimeError(
                f"native smoke requires an already-installed {name}; nothing was installed"
            )
        tools[name] = versions[-1]
    repository = Path(__file__).resolve().parents[2]
    native = "windows" if os.name == "nt" else "wsl"
    with tempfile.TemporaryDirectory(prefix="dev-tools-tools-") as temporary:
        root = Path(temporary)
        source = root / "运行环境 & spaces"
        source.mkdir()
        ports = []
        for _ in range(3):
            with socket.socket() as server:
                server.bind(("127.0.0.1", 0))
                ports.append(server.getsockname()[1])
        (source / "mise.toml").write_text(render_toml({"tools": tools}), encoding="utf-8")
        (source / "package.json").write_text(
            '{"name":"fixture","version":"1.0.0"}', encoding="utf-8"
        )
        (source / "package-lock.json").write_text(
            '{"name":"fixture","version":"1.0.0","lockfileVersion":3,"packages":{"":{"name":"fixture","version":"1.0.0"}}}',
            encoding="utf-8",
        )
        wrapper = source / ("mvnw.cmd" if native == "windows" else "mvnw")
        wrapper.write_text(
            "@echo off\nmvn %*\n" if native == "windows" else '#!/bin/sh\nexec mvn "$@"\n',
            encoding="utf-8",
        )
        # Intentionally leave mvnw non-executable to test the native shell adapter.
        (source / "Hello.java").write_text(
            """import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
public class Hello { public static void main(String[] args) throws Exception {
  var server = HttpServer.create(new InetSocketAddress("127.0.0.1", Integer.parseInt(args[0])), 0);
  server.createContext("/", exchange -> { byte[] value = "ready".getBytes(); exchange.sendResponseHeaders(200, value.length); exchange.getResponseBody().write(value); exchange.close(); });
  server.start();
} }
""",
            encoding="utf-8",
        )
        raw = {
            "schema": 1,
            "name": "tools-" + root.name[-12:].lower(),
            "toolchain": "mise",
            "rebuild_on_branch": False,
            "tasks": {
                "node": {
                    "command": ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                    "manager": "npm",
                },
                "maven": {
                    "command": ["{maven}", "--version"],
                    "tools": ["java"],
                    "java": {"maven": {"repository": "project"}},
                },
                "java": {"command": ["javac", "-d", "target", "Hello.java"], "tools": ["java"]},
                "python": {"command": ["{python}", "-m", "venv", ".venv"], "tools": ["python"]},
            },
            "services": {
                "java": {
                    "command": ["java", "-cp", "target", "Hello", str(ports[0])],
                    "tools": ["java"],
                    "health": {"tcp": ports[0], "timeout": 30},
                    "restart": "never",
                },
                "node": {
                    "command": [
                        "node",
                        "-e",
                        f"require('http').createServer((q,s)=>s.end('ready')).listen({ports[1]},'127.0.0.1')",
                    ],
                    "tools": ["node"],
                    "health": {"tcp": ports[1], "timeout": 30},
                    "restart": "never",
                },
                "python": {
                    "command": [
                        "{venv_python}",
                        "-m",
                        "http.server",
                        str(ports[2]),
                        "--bind",
                        "127.0.0.1",
                    ],
                    "tools": ["python"],
                    "python": {"root": "."},
                    "health": {"tcp": ports[2], "timeout": 30},
                    "restart": "never",
                },
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
        if native == "wsl":
            environment["DEV_TOOLS_TEST_TRANSIENT"] = "1"

        def invoke(*arguments):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dev_tools",
                    "--env",
                    "win" if os.name == "nt" else "wsl",
                    *arguments,
                ],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
                check=False,
            )
            if result.returncode:
                raise AssertionError(f"{arguments}\n{result.stdout}\n{result.stderr}")
            return result

        name = raw["name"]
        invoke("register", str(source))
        try:
            preview = json.loads(invoke("prepare", name, "--dry-run", "--json").stdout)
            if set(preview["runtime_versions"]) != {
                f"{tool}@{version}" for tool, version in tools.items()
            }:
                raise AssertionError(preview)
            invoke("prepare", name)
            invoke("start", name)
            status = json.loads(invoke("status", name, "--json").stdout)
            if not status["ready"] or not status["healthy"]:
                raise AssertionError(status)
            binding = preview["binding"]
            logs = Path(binding["state"]) / "logs"
            if "Apache Maven" not in (logs / "tasks/maven.log").read_text(
                encoding="utf-8", errors="replace"
            ):
                raise AssertionError("native Maven wrapper did not execute")
            return {
                "environment": native,
                "runtimes": tools,
                "services": list(status["services"]),
                "wrapper": wrapper.name,
                "downloads": False,
            }
        finally:
            invoke("unregister", name)
            for port in ports:
                with socket.socket() as probe:
                    probe.settimeout(1)
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        raise AssertionError(f"runtime process leaked: {port}")


if __name__ == "__main__":
    print(json.dumps(smoke(), ensure_ascii=False, indent=2))
