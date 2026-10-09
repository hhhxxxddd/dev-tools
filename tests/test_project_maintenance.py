from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.projects.config import parse_project, render_toml
from dev_tools.projects.discovery import discover_project
from dev_tools.projects.maintenance import atomic_write, discovery_plan, resolution_task
from dev_tools.projects.models import CommandSpec, ProjectError, TaskSpec
from tests import test_project_engine as engine_fixture


class MaintenanceTests(unittest.TestCase):
    def test_existing_file_mode_survives_atomic_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dev-tools.toml"
            path.write_bytes(b"before")
            path.chmod(0o660)
            before = path.stat()
            atomic_write(path, b"after")
            after = path.stat()
            self.assertEqual(path.read_bytes(), b"after")
            self.assertEqual(stat.S_IMODE(before.st_mode), stat.S_IMODE(after.st_mode))
            self.assertEqual((before.st_uid, before.st_gid), (after.st_uid, after.st_gid))

    @unittest.skipUnless(os.name == "nt", "Windows read-only file semantics required")
    def test_read_only_destination_does_not_leave_an_undeletable_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "dev-tools.toml"
            path.write_bytes(b"before")
            path.chmod(0o444)
            try:
                with self.assertRaises(PermissionError):
                    atomic_write(path, b"after")
                self.assertEqual(path.read_bytes(), b"before")
                self.assertEqual(tuple(root.iterdir()), (path,))
            finally:
                path.chmod(0o600)

    @unittest.skipUnless(os.name == "posix", "Native Unix ownership required")
    def test_root_writeback_keeps_existing_owner_and_inherits_new_lock_owner(self):
        if os.geteuid() != 0:
            self.skipTest("root required")
        import pwd

        accounts = [item for item in pwd.getpwall() if item.pw_uid and Path(item.pw_dir).is_dir()]
        if not accounts:
            self.skipTest("non-root user required")
        account = accounts[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chown(root, account.pw_uid, account.pw_gid)
            path = root / "dev-tools.toml"
            path.write_bytes(b"before")
            os.chown(path, account.pw_uid, account.pw_gid)
            path.chmod(0o660)
            atomic_write(path, b"after")
            lock = root / "package-lock.json"
            atomic_write(lock, b"{}")
            for target in (path, lock):
                metadata = target.stat()
                self.assertEqual(
                    (metadata.st_uid, metadata.st_gid), (account.pw_uid, account.pw_gid)
                )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o660)

    def test_package_changes_only_prepare_the_affected_module(self):
        fixture = engine_fixture.EngineContractTests()
        for platform in ("windows", "wsl"):
            with tempfile.TemporaryDirectory() as temporary:
                engine = fixture.fixture(Path(temporary), platform)
                raw = copy.deepcopy(engine.spec.raw)
                raw["tasks"] = {}
                raw["builds"] = {}
                for module in ("frontend", "backend"):
                    directory = engine.binding.source / module
                    directory.mkdir()
                    (directory / "package.json").write_text('{"name":"fixture","version":"1.0.0"}')
                    (directory / "package-lock.json").write_text('{"lockfileVersion":3}')
                    raw["tasks"][module] = {
                        "command": ["npm", "ci"],
                        "manager": "npm",
                        "workdir": module,
                    }
                path = engine.binding.source / "dev-tools.toml"
                path.write_text(render_toml(raw), encoding="utf-8")
                with patch("dev_tools.projects.planning.shutil.which", return_value="npm"):
                    engine.start()
                    raw["services"]["api"]["command"] = ["fixture", "--verbose"]
                    path.write_text(render_toml(raw), encoding="utf-8")
                    self.assertFalse(engine.plan().resolution_tasks)
                    self.assertFalse(engine.plan().pending_tasks)
                    (engine.binding.source / "backend/package.json").write_text(
                        '{"name":"fixture","version":"1.0.1"}'
                    )
                    plan = engine.plan()
                    self.assertEqual(
                        [task.name for task in plan.resolution_tasks], ["resolve-backend"]
                    )
                    self.assertEqual(plan.pending_tasks, ("backend",))
                    engine.backend.events.clear()
                    engine.maintain()
                    tasks = [event for event in engine.backend.events if event.startswith("task:")]
                    self.assertEqual(tasks, ["task:resolve-backend", "task:backend"])

    def test_nested_module_tracks_its_ancestor_maven_wrapper(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), "windows")
            raw = copy.deepcopy(engine.spec.raw)
            (engine.binding.source / "module").mkdir()
            raw["tasks"]["install"]["workdir"] = "module"
            raw["tasks"]["install"]["manager"] = "maven"
            (engine.binding.source / "dev-tools.toml").write_text(
                render_toml(raw), encoding="utf-8"
            )
            wrapper = engine.binding.source / ".mvn/wrapper/maven-wrapper.properties"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text(
                "distributionUrl=https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.10/apache-maven-3.9.10-bin.zip"
            )
            engine.prepare()
            self.assertFalse(engine.plan().pending_tasks)
            wrapper.write_text(wrapper.read_text().replace("3.9.10", "3.9.12"))
            self.assertEqual(engine.plan().pending_tasks, ("install",))

    def test_uv_configuration_change_triggers_monitor_fingerprint_and_resolution(self):
        from dev_tools.projects.planning import fingerprint

        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), "windows")
            raw = copy.deepcopy(engine.spec.raw)
            raw["tasks"]["install"] = {"command": ["uv", "sync", "--locked"], "manager": "uv"}
            (engine.binding.source / "dev-tools.toml").write_text(
                render_toml(raw), encoding="utf-8"
            )
            (engine.binding.source / "uv.lock").write_text("version = 1\n")
            with patch("dev_tools.projects.planning.shutil.which", return_value="uv"):
                engine.prepare()
                before = engine.state()["prepared_revision"]
                (engine.binding.source / "uv.toml").write_text("offline = true\n")
                self.assertNotEqual(fingerprint(engine.binding), before)
                plan = engine.plan()
                self.assertEqual([task.name for task in plan.resolution_tasks], ["resolve-install"])
                self.assertEqual(plan.pending_tasks, ("install",))

    def test_generated_modules_are_added_removed_and_custom_commands_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = {"name": "fixture", "scripts": {"dev": "fixture"}}
            (root / "package.json").write_text(json.dumps(package))
            baseline = discover_project(root, name="demo", toolchain="system")
            current = copy.deepcopy(baseline)
            service = next(iter(current["services"]))
            current["services"][service]["command"] = ["custom", "dev"]
            (root / "module").mkdir()
            (root / "module/package.json").write_text(json.dumps(package))
            spec, incoming = discovery_plan(
                parse_project(current, "wsl"), root, {"discovery_baseline": baseline}, "wsl"
            )
            self.assertEqual(spec.services[service].command.argv, ("custom", "dev"))
            self.assertEqual(len(spec.services), 2)
            (root / "module/package.json").unlink()
            updated, _ = discovery_plan(spec, root, {"discovery_baseline": incoming}, "wsl")
            self.assertEqual(tuple(updated.services), (service,))
            self.assertEqual(updated.services[service].command.argv, ("custom", "dev"))

    def test_dry_run_rediscovery_never_writes_or_executes_and_keeps_platform_override(self):
        fixture = engine_fixture.EngineContractTests()
        with tempfile.TemporaryDirectory() as temporary:
            engine = fixture.fixture(Path(temporary), "windows")
            raw = copy.deepcopy(engine.spec.raw)
            raw["discovery"] = "auto"
            raw["services"]["api"]["platforms"] = {"windows": {"command": ["windows-only"]}}
            path = engine.binding.source / "dev-tools.toml"
            path.write_text(render_toml(raw))
            before = path.read_bytes()
            with patch("subprocess.run") as execute:
                plan = engine.plan()
            execute.assert_not_called()
            self.assertEqual(plan.spec.services["api"].command.argv, ("windows-only",))
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(engine.binding.state.exists())

    def test_custom_install_arguments_are_not_replaced_by_a_resolver(self):
        task = TaskSpec(
            "install", CommandSpec(argv=("npm", "install", "specific-package")), manager="npm"
        )
        self.assertIsNone(resolution_task(task))

    def test_locked_mode_requires_a_lock_without_resolving_it(self):
        fixture = engine_fixture.EngineContractTests()
        with tempfile.TemporaryDirectory() as temporary:
            engine = fixture.fixture(Path(temporary), "windows")
            raw = copy.deepcopy(engine.spec.raw)
            raw["dependency_mode"] = "locked"
            raw["tasks"]["install"] = {"command": ["npm", "ci"], "manager": "npm"}
            (engine.binding.source / "dev-tools.toml").write_text(render_toml(raw))
            plan = engine.plan()
            self.assertFalse(plan.resolution_tasks)
            self.assertTrue(any("lockfile" in str(item) for item in plan.unresolved))

    def test_invalid_modes_are_configuration_errors(self):
        for field, value in (
            ("dependency_mode", "latest"),
            ("dependency_mode", []),
            ("discovery", {}),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ProjectError):
                parse_project({"schema": 1, "name": "demo", field: value}, "windows")
