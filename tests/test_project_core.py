from __future__ import annotations

import copy
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from dev_tools.projects.config import parse_project, render_toml
from dev_tools.projects.discovery import discover_project
from dev_tools.projects.initialization import initialize
from dev_tools.projects.models import ProjectBinding, ProjectError, within
from dev_tools.projects.monitoring import change_kind, file_snapshot
from dev_tools.projects.planning import fingerprint, preparation_plan
from dev_tools.runtimes.platforms.base import Backend
from dev_tools.runtimes.platforms.locking import operation_lock


class ProjectCoreTests(unittest.TestCase):
    def test_platform_overrides_share_model_and_merge_environment(self):
        raw = {
            "schema": 1,
            "name": "demo",
            "tasks": {"install": {"command": ["npm", "ci"]}},
            "services": {
                "api": {
                    "command": ["{python}", "app.py"],
                    "prepare": ["install"],
                    "env": {"MODE": "dev"},
                    "platforms": {"windows": {"env": {"NATIVE": "yes"}}},
                }
            },
        }
        self.assertEqual(tomllib.loads(render_toml(raw)), raw)
        windows, wsl = [parse_project(raw, platform) for platform in ("windows", "wsl")]
        self.assertEqual(windows.services["api"].command, wsl.services["api"].command)
        self.assertEqual(windows.services["api"].env, {"MODE": "dev", "NATIVE": "yes"})
        self.assertEqual(wsl.services["api"].env, {"MODE": "dev"})

    def test_invalid_declarations_fail_before_execution(self):
        base = {"schema": 1, "name": "demo", "services": {"a": {"command": ["fixture"]}}}
        mutations = (
            lambda raw: raw.update(source="/machine/source"),
            lambda raw: raw["services"]["a"].update(workdir="../outside"),
            lambda raw: raw["services"]["a"].update(workdir="C:\\source"),
            lambda raw: raw["services"]["a"].update(depends_on=["missing"]),
            lambda raw: raw["services"]["a"].update(depends_on=["a"]),
            lambda raw: raw["services"]["a"].update(health={"tcp": True}),
            lambda raw: raw["services"]["a"].update(command="echo unsafe"),
            lambda raw: raw["services"]["a"].update(java={"spring": []}),
            lambda raw: raw["services"]["a"].update(java={"maven": {"repository": "/shared"}}),
        )
        for mutation in mutations:
            raw = copy.deepcopy(base)
            mutation(raw)
            with self.subTest(raw=raw), self.assertRaises(ProjectError):
                parse_project(raw, "windows")

    def test_dependency_closure_and_cycles(self):
        spec = parse_project(
            {
                "schema": 1,
                "name": "demo",
                "tasks": {
                    "install": {"command": ["fixture"]},
                    "build": {"command": ["fixture"], "depends_on": ["install"]},
                },
                "services": {
                    "web": {"command": ["fixture"], "depends_on": ["api"]},
                    "api": {"command": ["fixture"]},
                },
            },
            "windows",
        )
        self.assertEqual(spec.service_order(), ("api", "web"))
        self.assertEqual(spec.task_order(("build",)), ("install", "build"))

    def test_discovery_supports_java_python_and_node_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ("api", "web", "python"):
                (root / directory).mkdir()
            (root / "api/pom.xml").write_text("""<project><modelVersion>4.0.0</modelVersion>
                <parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>3.4.1</version></parent>
                <artifactId>api</artifactId><build><plugins><plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin></plugins></build></project>""")
            (root / "web/package.json").write_text(
                json.dumps(
                    {
                        "packageManager": "pnpm@10.0.0",
                        "scripts": {"dev": "vite"},
                        "devDependencies": {"vite": "6"},
                    }
                )
            )
            (root / "python/pyproject.toml").write_text(
                '[project]\nname="api"\nversion="1"\ndependencies=["fastapi", "uvicorn"]\n'
            )
            raw = discover_project(root)
            for platform in ("windows", "wsl"):
                spec = parse_project(raw, platform)
                self.assertEqual(set(spec.services), {"java-api", "web-web", "python-python"})
                self.assertEqual(
                    spec.services["java-api"].options["java"]["spring"]["devtools"],
                    "org.springframework.boot:spring-boot-devtools:3.4.1",
                )
                self.assertEqual(
                    spec.tasks["install-node-web"].command.argv,
                    ("pnpm", "install", "--frozen-lockfile"),
                )
                self.assertEqual(len(spec.builds), 1)
                self.assertNotIn("redis", raw)
                self.assertNotIn("mysql", raw)

    def test_node_monorepo_deduplicates_preparation_but_keeps_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                '{"packageManager":"pnpm@10.0.0","workspaces":["apps/*"]}'
            )
            (root / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n")
            for name in ("a", "b"):
                (root / "apps" / name).mkdir(parents=True)
                (root / "apps" / name / "package.json").write_text(
                    '{"scripts":{"dev":"next dev"},"dependencies":{"next":"15"}}'
                )
            spec = parse_project(discover_project(root), "windows")
            self.assertEqual(len(spec.tasks), 1)
            self.assertEqual(len(spec.services), 2)
            self.assertEqual({item.health["tcp"] for item in spec.services.values()}, {3000, 3001})
            self.assertTrue(
                all(item.prepare == ("install-node",) for item in spec.services.values())
            )

    def test_uv_workspace_uses_root_venv_and_never_syncs_on_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers=["apps/*"]\n')
            (root / "uv.lock").write_text("version=1\n")
            app = root / "apps/api"
            app.mkdir(parents=True)
            (app / "pyproject.toml").write_text(
                '[project]\nname="api"\nversion="1"\ndependencies=["fastapi"]\n'
            )
            spec = parse_project(discover_project(root), "wsl")
            self.assertEqual(len(spec.tasks), 1)
            self.assertEqual(spec.tasks["install-python"].workdir, ".")
            service = spec.services["python-apps-api"]
            self.assertEqual(service.options["python"]["root"], ".")
            self.assertNotIn("sync", service.command.argv)

    def test_multimodule_spring_uses_transitive_modules_parent_version_and_declared_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("app", "library"):
                (root / "backend" / name / "src/main/resources").mkdir(parents=True)
            (root / "backend/pom.xml").write_text(
                "<project><groupId>test</groupId><artifactId>backend</artifactId><packaging>pom</packaging><properties><spring.boot.version>3.5.0</spring.boot.version></properties></project>"
            )
            (root / "backend/library/pom.xml").write_text(
                "<project><groupId>test</groupId><artifactId>library</artifactId></project>"
            )
            (root / "backend/app/pom.xml").write_text(
                "<project><groupId>test</groupId><artifactId>app</artifactId><dependencies><dependency><groupId>test</groupId><artifactId>library</artifactId></dependency></dependencies><build><plugins><plugin><artifactId>spring-boot-maven-plugin</artifactId></plugin></plugins></build></project>"
            )
            (root / "backend/app/src/main/resources/application-local.yaml").write_text(
                "server:\n  port: 48090\n"
            )
            spec = parse_project(discover_project(root), "windows")
            service = spec.services["java-backend-app"]
            self.assertEqual(service.workdir, "backend")
            self.assertEqual(service.health["tcp"], 48090)
            self.assertIn("app", service.command.argv)
            self.assertIn("-Dspring-boot.run.profiles=local", service.command.argv)
            self.assertIn(
                "backend/library/target/classes",
                service.options["java"]["spring"]["classpath_modules"],
            )
            self.assertIn("backend/library/src/main", spec.builds[0].watch)
            self.assertEqual(
                service.options["java"]["spring"]["devtools"],
                "org.springframework.boot:spring-boot-devtools:3.5.0",
            )

    def test_compose_is_explicitly_separate_from_host_discovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "compose.yaml").write_text("services: {}\n")
            (root / "package.json").write_text('{"scripts":{"dev":"node app.js"}}')
            self.assertEqual(discover_project(root)["services"]["compose"]["driver"], "compose")
            self.assertIn("web", discover_project(root, runtime="host")["services"])

    def test_discovery_reports_ambiguous_generated_names_and_compose_bases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("apps/a", "apps-a"):
                directory = root / relative
                directory.mkdir(parents=True)
                (directory / "package.json").write_text('{"scripts":{"dev":"node app.js"}}')
            with self.assertRaisesRegex(ProjectError, "ambiguous generated name"):
                discover_project(root)
            (root / "compose.yaml").write_text("services: {}\n")
            (root / "compose.yml").write_text("services: {}\n")
            with self.assertRaisesRegex(ProjectError, "multiple Compose base files"):
                discover_project(root)

    def test_init_is_static_and_preserves_both_existing_declarations(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("subprocess.run", side_effect=AssertionError("execution during init")),
        ):
            root = Path(temporary)
            (root / ".nvmrc").write_text("22\n")
            preview, blocked = initialize(root, dry_run=True, name="demo")
            self.assertFalse(blocked)
            self.assertFalse((root / "dev-tools.toml").exists())
            self.assertFalse((root / "mise.toml").exists())
            self.assertEqual(preview["schema_version"], 3)
            initialize(root, name="demo")
            before = {name: (root / name).read_bytes() for name in ("mise.toml", "dev-tools.toml")}
            initialize(root)
            self.assertEqual(before, {name: (root / name).read_bytes() for name in before})

    def test_plan_requires_root_declarations_and_lock_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "web").mkdir()
            (root / "web/.nvmrc").write_text("22")
            (root / "mise.toml").write_text("[tools]\n")
            spec = parse_project(
                {
                    "schema": 1,
                    "name": "demo",
                    "tasks": {
                        "node": {"command": ["npm", "ci"], "manager": "npm", "workdir": "web"}
                    },
                },
                "windows",
            )
            binding = ProjectBinding("demo", "windows", root, root, root / "state")
            with patch("dev_tools.projects.planning.shutil.which", return_value="mise"):
                plan = preparation_plan(spec, binding, Backend(binding))
            self.assertTrue(any("lockfile" in message for message in plan.unresolved))
            self.assertTrue(
                any("no declaration for: node" in message for message in plan.unresolved)
            )
            self.assertFalse(binding.state.exists())

    def test_build_tasks_are_not_preparation_tasks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = parse_project(
                {
                    "schema": 1,
                    "name": "demo",
                    "toolchain": "system",
                    "tasks": {
                        "install": {"command": ["fixture"]},
                        "compile": {"command": ["fixture"], "role": "build"},
                    },
                },
                "windows",
            )
            binding = ProjectBinding("demo", "windows", root, root, root / "state")
            plan = preparation_plan(spec, binding, Backend(binding))
            self.assertEqual(plan.tasks, ("install",))
            self.assertEqual(plan.as_dict()["schema_version"], 3)

    def test_manifest_and_lock_changes_invalidate_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = ProjectBinding("demo", "windows", root, root, root / "state")
            initial = fingerprint(binding)
            (root / "package-lock.json").write_text("{}")
            self.assertNotEqual(initial, fingerprint(binding))
            initial = fingerprint(binding)
            (root / "node_modules").mkdir()
            (root / "node_modules/package.json").write_text("{}")
            self.assertEqual(initial, fingerprint(binding))

    def test_monitor_classifies_edits_deletes_resources_and_poms(self):
        spec = parse_project(
            {
                "schema": 1,
                "name": "demo",
                "toolchain": "system",
                "tasks": {"build": {"command": ["fixture"], "role": "build"}},
                "services": {"api": {"command": ["fixture"]}},
                "builds": {
                    "api": {
                        "watch": ["."],
                        "source_task": "build",
                        "resource_task": "build",
                        "structural_task": "build",
                    }
                },
            },
            "windows",
        )
        policy = spec.builds[0]
        for name, after, expected in (
            ("src/A.java", {"src/A.java": (2, 1)}, "source"),
            ("src/A.java", {}, "structural"),
            ("src/resources/a.yml", {"src/resources/a.yml": (2, 1)}, "resource"),
            ("pom.xml", {"pom.xml": (2, 1)}, "structural"),
        ):
            self.assertEqual(change_kind({name: (1, 1)}, after, policy), expected)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "A.java").write_text("source")
            (root / "target").mkdir()
            (root / "target/A.java").write_text("output")
            self.assertEqual(set(file_snapshot(root, policy)), {"A.java"})

    def test_concurrent_operations_cannot_share_a_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with operation_lock(state), self.assertRaises(ProjectError), operation_lock(state):
                pass

    def test_control_waits_for_a_short_operation_without_overlapping_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            locked, release, exited = Event(), Event(), Event()

            def background():
                with operation_lock(state):
                    locked.set()
                    release.wait(5)
                exited.set()

            thread = Thread(target=background)
            thread.start()
            try:
                self.assertTrue(locked.wait(2))
                with self.assertRaises(ProjectError), operation_lock(state, timeout=0.1):
                    pass
                release.set()
                with operation_lock(state, timeout=2):
                    self.assertTrue(exited.wait(2))
            finally:
                release.set()
                thread.join(5)

    @unittest.skipIf(os.name == "nt", "Windows symlinks may require elevation")
    def test_symlink_workdir_cannot_escape_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source").mkdir()
            (root / "outside").mkdir()
            (root / "source/link").symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaises(ProjectError):
                within(root / "source", "link")
