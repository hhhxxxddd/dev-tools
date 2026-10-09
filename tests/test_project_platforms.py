from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.projects.config import parse_project
from dev_tools.projects.models import ProjectBinding
from dev_tools.projects.registry import read_json, write_json
from dev_tools.runtimes.router import (
    _windows_entry,
    forward_remote,
    source_argument,
    split_environment,
)


class RouterTests(unittest.TestCase):
    def test_wsl_deployment_uses_recorded_native_windows_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".windows-source").write_text("Z:/controller path\n", encoding="utf-8")
            with (
                patch("dev_tools.runtimes.router.REPOSITORY", root),
                patch("dev_tools.runtimes.router._windows_path") as translate,
            ):
                command = _windows_entry(["list", "--json"])
                self.assertEqual(
                    command,
                    [
                        "-File",
                        "Z:/controller path/scripts/dev-tools.ps1",
                        "-e",
                        "win",
                        "list",
                        "--json",
                    ],
                )
                translate.assert_not_called()
            (root / ".windows-source").write_text("/opt/dev-tools", encoding="utf-8")
            with patch("dev_tools.runtimes.router.REPOSITORY", root), self.assertRaises(ValueError):
                _windows_entry(["list", "--json"])

    def test_linux_deployment_queries_native_entrypoint_without_unc_script(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("dev_tools.runtimes.router.REPOSITORY", Path(temporary)),
            patch(
                "dev_tools.runtimes.router._windows_path",
                return_value="\\\\wsl.localhost\\Ubuntu\\opt\\dev-tools\\scripts\\dev-tools.ps1",
            ),
        ):
            command = _windows_entry(["show", "O'Brien $(literal)"])
        self.assertEqual(command[0], "-Command")
        self.assertIn("Get-Command dev-tools -CommandType Application", command[1])
        self.assertIn("'O''Brien $(literal)'", command[1])
        self.assertNotIn("wsl.localhost", command[1])

    def test_remote_capture_returns_utf8_json_with_bounded_timeout(self):
        target = "wsl" if os.name == "nt" else "win"
        result = subprocess.CompletedProcess([], 0, '{"name": "项目"}', "")
        with (
            patch("dev_tools.runtimes.router.shutil.which", return_value="available"),
            patch("dev_tools.runtimes.router._windows_path", return_value="C:/fixture"),
            patch("dev_tools.runtimes.router.subprocess.run", return_value=result) as execute,
        ):
            self.assertIs(
                forward_remote(
                    target,
                    ["list", "--json"],
                    settings=SimpleNamespace(distro="Configured"),
                    capture=True,
                ),
                result,
            )
        self.assertTrue(execute.call_args.kwargs["capture_output"])
        self.assertEqual(execute.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(execute.call_args.kwargs["timeout"], 30)
        self.assertIn("--json", execute.call_args.args[0])

    def test_environment_syntax_is_consistent_before_or_after_command(self):
        for environment in ("win", "wsl"):
            for tokens in (
                ["--env", environment, "list"],
                ["list", "--env=" + environment],
                ["list", "--env", environment],
                ["-e", environment, "list"],
                ["list", "-e" + environment],
                ["list", "-e=" + environment],
            ):
                self.assertEqual(split_environment(tokens), (environment, ["list"]))
        self.assertEqual(
            split_environment(["list"]), ("win" if os.name == "nt" else "wsl", ["list"])
        )
        self.assertEqual(
            split_environment(["-e", "wsl", "scan", "--", "--env"]),
            ("wsl", ["scan", "--", "--env"]),
        )
        for environment in ("windows", "local", ""):
            with self.assertRaises(ValueError):
                split_environment(["list", "--env=" + environment])
        self.assertEqual(
            source_argument(["init", "--name", "demo", "--toolchain=system", "--json", "source"]), 5
        )

    @unittest.skipIf(os.name == "nt", "WSL to Windows transport")
    def test_remote_windows_uses_common_entrypoint_and_translates_source(self):
        with (
            patch("dev_tools.runtimes.router.shutil.which", return_value="pwsh.exe"),
            patch(
                "dev_tools.runtimes.router._windows_path",
                side_effect=lambda value: "WINDOWS:" + str(value),
            ),
            patch(
                "dev_tools.runtimes.router.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run,
        ):
            self.assertEqual(forward_remote("win", ["init", "--name", "demo", "/project"]), 0)
        command = run.call_args.args[0]
        self.assertIn("WINDOWS:/project", command)
        self.assertTrue(any("dev-tools.ps1" in item for item in command))
        self.assertNotIn("project", command)
        self.assertEqual(command[command.index("-e") + 1], "win")
        self.assertEqual(
            command[command.index("-WorkingDirectory") + 1], "WINDOWS:" + str(Path.cwd())
        )


class WindowsAdapterTests(unittest.TestCase):
    def test_status_batches_process_queries_and_keeps_dead_worker_diagnostics(self):
        from dev_tools.runtimes.platforms.windows import WindowsBackend

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = ProjectBinding("demo", "windows", root, root, root / "state")
            backend = WindowsBackend(binding)
            (binding.state / "native").mkdir(parents=True)
            write_json(binding.state / "workers/api.json", {"phase": "running", "pid": 100})
            write_json(binding.state / "workers/web.json", {"phase": "running", "pid": 200})
            with patch.object(
                backend, "_native", return_value={"api": True, "web": False}
            ) as query:
                phases = backend.phases(("api", "web", "__watch"))
            query.assert_called_once_with("query", binding.state / "native")
            self.assertEqual(phases["api"]["phase"], "running")
            self.assertEqual(phases["web"]["phase"], "failed")
            self.assertIn("unexpectedly", phases["web"]["error"])
            self.assertEqual(phases["__watch"]["phase"], "stopped")

    def test_detached_worker_uses_resolved_controller_storage(self):
        from dev_tools.runtimes.platforms.windows import WindowsBackend

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = ProjectBinding("demo", "windows", root, root, root / "native-state/demo")
            backend = WindowsBackend(binding)

            def launch(action, request):
                self.assertEqual(action, "launch")
                environment = read_json(request)["environment"]
                self.assertEqual(environment["DEV_TOOLS_REGISTRY_ROOT"], str(root / "registry"))
                self.assertEqual(environment["DEV_TOOLS_STATE_ROOT"], str(root / "native-state"))
                return {"pid": 123, "start_ticks": 456}

            with (
                patch.object(backend, "active", return_value=False),
                patch.object(backend, "_native", side_effect=launch),
                patch(
                    "dev_tools.runtimes.platforms.layout.storage",
                    return_value=(root / "registry", root / "native-state"),
                ),
                patch.dict(os.environ, {"DEV_TOOLS_REGISTRY_ROOT": str(root / "other-context")}),
            ):
                backend.start("api")
            self.assertFalse((binding.state / "native/api.launch.json").exists())


@unittest.skipUnless(os.name != "nt", "native Linux adapter")
class WslAdapterTests(unittest.TestCase):
    def fixture(self, root):
        import pwd

        from dev_tools.runtimes.platforms.wsl import WslBackend

        source, cache = root / "source", root / "cache"
        source.mkdir()
        cache.mkdir()
        binding = ProjectBinding(
            "demo",
            "wsl",
            source,
            cache / "demo",
            root / "state",
            pwd.getpwuid(os.geteuid()).pw_name,
            cache,
        )
        return binding, WslBackend(binding)

    @unittest.skipUnless(shutil.which("rsync"), "rsync required")
    def test_sync_updates_and_deletes_only_native_sources_and_preserves_native_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            binding, backend = self.fixture(Path(temporary))
            spec = parse_project(
                {
                    "schema": 1,
                    "name": "demo",
                    "toolchain": "system",
                    "sync_exclude": ["build.txt"],
                    "sync_include": ["/frontend/build/***"],
                },
                "wsl",
            )
            (binding.source / "file.txt").write_text("first")
            (binding.source / "node_modules").mkdir()
            (binding.source / "node_modules/windows.exe").write_text("never copy")
            (binding.source / "frontend/build").mkdir(parents=True)
            (binding.source / "frontend/build/vite.ts").write_text("source configuration")
            (binding.source / "build").mkdir()
            (binding.source / "build/windows.exe").write_text("never copy")
            backend.sync(spec)
            self.assertFalse((binding.workspace / "node_modules").exists())
            self.assertFalse((binding.workspace / "build").exists())
            self.assertEqual(
                (binding.workspace / "frontend/build/vite.ts").read_text(),
                "source configuration",
            )
            (binding.workspace / "node_modules").mkdir()
            (binding.workspace / "node_modules/native").write_text("keep")
            (binding.workspace / "build.txt").write_text("native build output")
            (binding.source / "file.txt").unlink()
            (binding.source / "new.txt").write_text("second")
            backend.sync(spec)
            self.assertFalse((binding.workspace / "file.txt").exists())
            self.assertEqual((binding.workspace / "new.txt").read_text(), "second")
            self.assertEqual((binding.workspace / "node_modules/native").read_text(), "keep")
            self.assertEqual((binding.workspace / "build.txt").read_text(), "native build output")

    def test_native_dependencies_never_include_language_packages_or_databases(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, backend = self.fixture(Path(temporary))
            spec = parse_project(
                {"schema": 1, "name": "demo", "services": {"stack": {"driver": "compose"}}}, "wsl"
            )
            with patch("dev_tools.runtimes.platforms.wsl.shutil.which", return_value=None):
                requirements = backend.requirements(spec)
            self.assertIn("rsync", requirements.packages)
            self.assertIn("docker.io", requirements.packages)
            self.assertFalse(
                any(
                    any(
                        tool in package
                        for tool in ("mysql", "redis", "nodejs", "openjdk", "python3")
                    )
                    for package in requirements.packages
                )
            )

    def test_user_runtime_environment_discards_controller_private_mise_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, backend = self.fixture(Path(temporary))
            with patch.dict(
                os.environ,
                {"MISE_DATA_DIR": "/controller/private", "XDG_CACHE_HOME": "/controller/cache"},
            ):
                environment = backend.environment()
            self.assertNotIn("MISE_DATA_DIR", environment)
            self.assertNotIn("XDG_CACHE_HOME", environment)
            self.assertEqual(environment["HOME"], str(backend.home()))
