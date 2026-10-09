from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.cli import parser
from dev_tools.projects.cli import engine_for, run
from dev_tools.projects.config import render_toml
from dev_tools.projects.models import ProjectError
from dev_tools.projects.registry import Registry, write_json
from tests.test_project_engine import FakeBackend


class RegistryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        override = patch.dict(
            os.environ,
            {
                "DEV_TOOLS_REGISTRY_ROOT": str(self.root / "registry"),
                "DEV_TOOLS_STATE_ROOT": str(self.root / "state"),
            },
        )
        override.start()
        self.addCleanup(override.stop)
        self.registry = Registry("windows")

    def source(self, name="demo"):
        source = self.root / name
        source.mkdir()
        (source / "dev-tools.toml").write_text(
            render_toml(
                {
                    "schema": 1,
                    "name": name,
                    "toolchain": "system",
                    "rebuild_on_branch": False,
                    "services": {"api": {"command": ["fixture"]}},
                }
            ),
            encoding="utf-8",
        )
        return source

    def test_native_binding_keeps_machine_values_out_of_project_declaration(self):
        source = self.source()
        before = (source / "dev-tools.toml").read_bytes()
        binding = self.registry.register(source)
        self.assertEqual(binding.workspace, source)
        self.assertTrue(self.registry.path("demo").is_file())
        self.assertEqual(self.registry.load("demo"), binding)
        self.assertEqual((source / "dev-tools.toml").read_bytes(), before)
        self.assertFalse(binding.state.exists())

    def test_alias_reuse_never_reuses_another_bindings_state(self):
        binding = self.registry.register(self.source())
        renamed = self.registry.rename("demo", "renamed")
        self.assertEqual(renamed.state, binding.state)
        self.assertEqual(renamed.workspace, binding.workspace)
        other = self.registry.register(self.source("other"), name="demo")
        self.assertNotEqual(other.state, renamed.state)
        with self.assertRaises(ProjectError):
            self.registry.register(binding.source, name="duplicate")

    def test_force_adds_an_alias_with_independent_state_and_preserves_existing_bindings(self):
        binding = self.registry.register(self.source())
        original = self.registry.path("demo").read_bytes()
        duplicate = self.registry.register(binding.source, name="duplicate", force=True)
        self.assertEqual(duplicate.source, binding.source)
        self.assertNotEqual(duplicate.state, binding.state)
        self.assertEqual(self.registry.path("demo").read_bytes(), original)
        self.assertEqual(self.registry.register(binding.source, force=True), binding)
        self.assertEqual(
            self.registry.register(binding.source, name="duplicate", force=True), duplicate
        )
        with self.assertRaises(ProjectError):
            self.registry.resolve_name(None, cwd=binding.source)
        before = self.registry.path("duplicate").read_bytes()
        with self.assertRaises(ProjectError):
            self.registry.register(self.source("other"), name="duplicate", force=True)
        self.assertEqual(self.registry.path("duplicate").read_bytes(), before)

    def test_wsl_duplicate_detection_uses_resolved_running_user(self):
        source = self.source()
        registry = Registry("wsl")

        def native_workspace(environment, source, identity, user):
            user = user or "default-user"
            cache = self.root / "cache" / user
            return cache / identity, user, cache

        with patch("dev_tools.projects.registry.workspace", side_effect=native_workspace):
            original = registry.register(source)
            other_user = registry.register(source, name="other-user", run_user="other-user")
            self.assertNotEqual(original.workspace, other_user.workspace)
            with self.assertRaises(ProjectError):
                registry.register(source, name="duplicate", run_user="default-user")
            duplicate = registry.register(
                source, name="duplicate", run_user="default-user", force=True
            )
            self.assertNotEqual(duplicate.workspace, original.workspace)
            self.assertNotEqual(duplicate.state, original.state)
            self.assertEqual(registry.register(source, force=True), original)
            with self.assertRaises(ProjectError):
                registry.register(source, run_user="other-user", force=True)

    def test_malformed_binding_cannot_point_state_outside_native_storage(self):
        binding = self.registry.register(self.source())
        for state in (str(self.root / "outside"), "relative"):
            write_json(
                self.registry.path("demo"),
                {"schema_version": 3, **binding.as_dict(), "state": state},
            )
            with self.assertRaises(ProjectError):
                self.registry.load("demo")

    def test_stop_and_unregister_survive_a_broken_declaration(self):
        binding = self.registry.register(self.source())
        engine = engine_for("windows", "demo")
        write_json(binding.state / "project.json", {"last_project": engine.spec.raw})
        (binding.source / "dev-tools.toml").write_text("[broken")
        backend = FakeBackend(binding)
        backend.running.add("api")
        with (
            patch("dev_tools.projects.cli.backend_for", return_value=backend),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            args = parser().parse_args(["--env", "win", "unregister", "demo"])
            self.assertEqual(run(args), 0)
        self.assertFalse(backend.running)
        self.assertFalse(self.registry.path("demo").exists())
        self.assertTrue(binding.source.exists())
        self.assertTrue(binding.state.exists())

    def test_windows_purge_is_rejected_before_stopping_or_deleting_source(self):
        binding = self.registry.register(self.source())
        backend = FakeBackend(binding)
        backend.running.add("api")
        with (
            patch("dev_tools.projects.cli.backend_for", return_value=backend),
            self.assertRaises(ProjectError),
        ):
            run(parser().parse_args(["--env", "win", "unregister", "demo", "--purge"]))
        self.assertEqual(backend.running, {"api"})
        self.assertTrue(binding.source.exists())
        self.assertTrue(self.registry.path("demo").exists())

    def test_implicit_name_matches_source_and_descendants_only(self):
        binding = self.registry.register(self.source())
        child = binding.source / "nested"
        child.mkdir()
        for current in (binding.source, child):
            self.assertEqual(self.registry.resolve_name(None, cwd=current), "demo")
        with self.assertRaisesRegex(ProjectError, "请显式指定项目名"):
            self.registry.resolve_name(None, cwd=self.root)
        self.assertEqual(self.registry.resolve_name("demo", cwd=self.root), "demo")

    def test_implicit_name_rejects_overlapping_registered_roots(self):
        outer = self.registry.register(self.source())
        inner = outer.source / "nested"
        inner.mkdir()
        (inner / "dev-tools.toml").write_text(
            render_toml({"schema": 1, "name": "nested"}), encoding="utf-8"
        )
        self.registry.register(inner)
        with self.assertRaisesRegex(ProjectError, "demo, nested；请显式指定项目名"):
            self.registry.resolve_name(None, cwd=inner)
        before = {path: path.read_bytes() for path in self.registry.root.glob("*.json")}
        with patch("pathlib.Path.cwd", return_value=inner), self.assertRaises(ProjectError):
            run(parser().parse_args(["-e", "win", "unregister"]))
        self.assertEqual(
            before, {path: path.read_bytes() for path in self.registry.root.glob("*.json")}
        )

    def test_implicit_name_also_matches_native_wsl_workspace(self):
        binding = self.registry.register(self.source())
        cache = self.root / "cache"
        workspace = cache / "demo"
        registry = Registry("wsl")
        write_json(
            registry.path("demo"),
            {
                "schema_version": 3,
                **binding.as_dict(),
                "environment": "wsl",
                "workspace": str(workspace),
                "cache_root": str(cache),
            },
        )
        self.assertEqual(registry.resolve_name(None, cwd=workspace / "nested"), "demo")

    def test_implicit_unregister_stops_workers_and_preserves_source_and_state(self):
        binding = self.registry.register(self.source())
        write_json(binding.state / "marker.json", {"keep": True})
        backend = FakeBackend(binding)
        backend.running.add("api")
        with (
            patch("pathlib.Path.cwd", return_value=binding.source),
            patch("dev_tools.projects.cli.backend_for", return_value=backend),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run(parser().parse_args(["unregister", "-e", "win"])), 0)
        self.assertFalse(backend.running)
        self.assertEqual(self.registry.names(), ())
        self.assertTrue((binding.source / "dev-tools.toml").is_file())
        self.assertTrue((binding.state / "marker.json").is_file())
