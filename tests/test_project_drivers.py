from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.projects.config import parse_project
from dev_tools.projects.drivers import (
    artifact_jar,
    compose_command,
    install_spring_artifact,
    maven_runtime,
    remove_owned_tree,
    spring_classpath,
)
from dev_tools.projects.execution import Executor
from dev_tools.projects.models import CommandSpec, ProjectBinding, ProjectError
from dev_tools.runtimes.platforms.base import Backend
from dev_tools.workflows import PROJECT_ISOLATION_CONFIG


class DriverTests(unittest.TestCase):
    def test_compose_health_accepts_json_lists_and_lines_and_honors_tcp_probe(self):
        from dev_tools.projects.engine import _probe

        for content, expected in (
            ('[{"State":"running","Health":"healthy"}]', "healthy"),
            ('{"State":"running"}\n{"State":"running"}', "healthy"),
            ('{"State":"running","Health":"starting"}', "unhealthy"),
            ("[]", "unhealthy"),
        ):
            service = parse_project(
                {"schema": 1, "name": "demo", "services": {"stack": {"driver": "compose"}}},
                "windows",
            ).services["stack"]
            with patch(
                "dev_tools.projects.engine.run_compose",
                return_value=subprocess.CompletedProcess([], 0, content),
            ):
                self.assertEqual(_probe(service, None), expected)
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            port = server.getsockname()[1]
        service = parse_project(
            {
                "schema": 1,
                "name": "demo",
                "services": {"stack": {"driver": "compose", "health": {"tcp": port}}},
            },
            "windows",
        ).services["stack"]
        with patch(
            "dev_tools.projects.engine.run_compose",
            return_value=subprocess.CompletedProcess([], 0, '{"State":"running"}'),
        ):
            self.assertEqual(_probe(service, None), "unhealthy")

    def fixture(self, root):
        source = root / "source"
        source.mkdir()
        workspace = root / "native"
        workspace.mkdir()
        binding = ProjectBinding("demo", "windows", source, workspace, root / "state")
        backend = Backend(binding)
        backend.home = lambda: root / "native-home"
        return binding, backend

    def test_wrapper_and_repository_use_native_workspace_and_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, backend = self.fixture(root)
            (binding.source / "mvnw.cmd").write_text("must never execute during resolution")
            (binding.source / "app").mkdir()
            for mode in ("user", "project"):
                command, env = maven_runtime(
                    binding, backend, "app", {"java": {"maven": {"repository": mode}}}
                )
                self.assertEqual(command, str(binding.workspace / "mvnw.cmd"))
                self.assertTrue(str(root / "native-home") in env["DEV_TOOLS_MAVEN_REPOSITORY"])
                self.assertNotIn(str(binding.source), env["DEV_TOOLS_MAVEN_REPOSITORY"])

    def test_artifact_coordinate_rejects_escape(self):
        for coordinate in (
            "../g:a:1",
            "g:../a:1",
            "g:a:../1",
            "g:a",
            "g:a:1:other",
            "...:a:1",
            ".g:a:1",
            "g..x:a:1",
        ):
            with self.assertRaises(ProjectError):
                artifact_jar(Path("native"), coordinate)

    def test_spring_start_never_downloads_and_prepare_downloads_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, backend = self.fixture(root)
            spec = parse_project(
                {
                    "schema": 1,
                    "name": "demo",
                    "toolchain": "system",
                    "services": {
                        "api": {
                            "command": ["fixture"],
                            "java": {"spring": {"devtools": "org.test:devtools:1.0"}},
                        }
                    },
                },
                "windows",
            )
            service = spec.services["api"]
            executor = Executor(spec, binding, backend)
            with patch.object(executor, "run") as run:
                with self.assertRaises(ProjectError):
                    spring_classpath(binding, backend, service)
                run.assert_not_called()
                artifact = artifact_jar(
                    root / "native-home/.m2/repository", "org.test:devtools:1.0"
                )

                def download(*_, **__):
                    artifact.parent.mkdir(parents=True)
                    artifact.write_bytes(b"fixture artifact")
                    return subprocess.CompletedProcess([], 0)

                run.side_effect = download
                install_spring_artifact(executor, service)
                install_spring_artifact(executor, service)
                self.assertEqual(run.call_count, 1)
                self.assertTrue(
                    spring_classpath(binding, backend, service).startswith(str(artifact) + ",")
                )

    def test_overlay_updates_classes_atomically_removes_stale_and_excludes_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, backend = self.fixture(root)
            classes = binding.workspace / "module/target/classes"
            (classes / "org/test").mkdir(parents=True)
            (classes / "org/test/A.class").write_bytes(b"old")
            (classes / "application.yml").write_text("must stay outside the overlay")
            service = parse_project(
                {
                    "schema": 1,
                    "name": "demo",
                    "services": {
                        "api": {
                            "command": ["fixture"],
                            "java": {"spring": {"classpath_modules": ["module/target/classes"]}},
                        }
                    },
                },
                "windows",
            ).services["api"]
            parts = spring_classpath(binding, backend, service).split(",")
            trigger, overlay = map(Path, parts)
            self.assertEqual((overlay / "org/test/A.class").read_bytes(), b"old")
            self.assertFalse((overlay / "application.yml").exists())
            old_marker = (trigger / "dev-tools-reload.trigger").read_text()
            (classes / "org/test/A.class").unlink()
            (classes / "org/test/B.class").write_bytes(b"new")
            spring_classpath(binding, backend, service, reload=True)
            self.assertFalse((overlay / "org/test/A.class").exists())
            self.assertEqual((overlay / "org/test/B.class").read_bytes(), b"new")
            self.assertNotEqual((trigger / "dev-tools-reload.trigger").read_text(), old_marker)
            self.assertFalse(list(overlay.rglob("*.tmp")))

    def test_compose_paths_profiles_and_namespace_survive_alias_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, _ = self.fixture(root)
            service = parse_project(
                {
                    "schema": 1,
                    "name": "demo",
                    "services": {
                        "stack": {
                            "driver": "compose",
                            "workdir": "infra",
                            "compose": {"files": ["compose.yml"], "profiles": ["dev"]},
                        }
                    },
                },
                "windows",
            ).services["stack"]
            command = compose_command(binding, service, "up").argv
            self.assertIn(str(binding.workspace / "infra/compose.yml"), command)
            self.assertIn("dev", command)
            from dataclasses import replace

            self.assertEqual(
                command, compose_command(replace(binding, name="renamed"), service, "up").argv
            )

    def test_mise_environment_prevents_global_defaults_and_implicit_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, backend = self.fixture(root)
            spec = parse_project({"schema": 1, "name": "demo"}, "windows")
            executor = Executor(spec, binding, backend)
            with patch("dev_tools.projects.execution.shutil.which", return_value="mise"):
                command, _, env = executor.resolve(CommandSpec(argv=("node", "app.js")))
            self.assertEqual(command[:3], ["mise", "exec", "--"])
            self.assertEqual(env["MISE_GLOBAL_CONFIG_FILE"], str(PROJECT_ISOLATION_CONFIG))
            self.assertEqual(env["MISE_EXEC_AUTO_INSTALL"], "false")
            self.assertEqual(env["MISE_NOT_FOUND_SYSTEM_FALLBACK"], "false")

    def test_execution_selects_root_versions_even_in_nested_workdir(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding, backend = self.fixture(root)
            (binding.source / "mise.toml").write_text('[tools]\nnode="22"\n')
            (binding.source / "nested").mkdir()
            (binding.source / "nested/mise.toml").write_text('[tools]\nnode="20"\n')
            (binding.workspace / "nested").mkdir()
            spec = parse_project({"schema": 1, "name": "demo"}, "windows")
            with patch("dev_tools.projects.execution.shutil.which", return_value="mise"):
                command, _, _ = Executor(spec, binding, backend).resolve(
                    CommandSpec(argv=("node", "app.js")), workdir="nested"
                )
            self.assertEqual(command[:4], ["mise", "exec", "node@22", "--"])

    def test_recursive_purge_must_stay_strictly_inside_owned_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ProjectError):
                remove_owned_tree(root, root)
            owned = root / "owned"
            owned.mkdir()
            remove_owned_tree(owned, root)
            self.assertFalse(owned.exists())
            if os.name != "nt":
                outside = root / "outside"
                outside.mkdir()
                link = root / "link"
                link.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ProjectError):
                    remove_owned_tree(link, root)
