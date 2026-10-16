from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.projects.config import parse_project
from dev_tools.projects.detection import _find
from dev_tools.projects.discovery import discover_project
from dev_tools.projects.models import BuildSpec, ProjectBinding
from dev_tools.projects.monitoring import file_snapshot
from dev_tools.projects.planning import fingerprint, preparation_plan
from dev_tools.runtimes.platforms.base import Backend
from dev_tools.scanner import scan_project


class ProjectDiscoveryPathsTests(unittest.TestCase):
    def test_local_review_copies_do_not_affect_project_preparation_or_monitoring(self):
        for platform in ("windows", "wsl"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                original = '[tools]\nnode = "24.22.0"\n'
                (root / "mise.toml").write_text(original)
                (root / "frontend").mkdir()
                (root / "frontend/package.json").write_text(
                    '{"engines":{"node":"24.x"},"scripts":{"dev":"vite"}}'
                )
                (root / "A.java").write_text("source")
                binding = ProjectBinding("demo", platform, root, root, root / "state")
                before = fingerprint(binding)
                review = root / ".local/qa/review"
                review.mkdir(parents=True)
                (review / "mise.toml").write_text('[tools]\nnode = "24"\n')
                (review / "package.json").write_text('{"scripts":{"dev":"vite"}}')
                (review / "B.java").write_text("review copy")
                cache = root / ".local/cache"
                cache.mkdir()
                (cache / "pyproject.toml").write_text("[broken")

                scan = scan_project(root)
                self.assertFalse(scan.blocked)
                self.assertEqual(scan.tools["node"].version, "24.22.0")
                self.assertEqual(scan.tools["node"].source, "mise.toml")
                self.assertEqual(fingerprint(binding), before)
                services = discover_project(root)["services"]
                self.assertEqual(
                    [service["workdir"] for service in services.values()], ["frontend"]
                )
                self.assertEqual(
                    set(file_snapshot(root, BuildSpec("web", (".",), "", "", ""))), {"A.java"}
                )
                spec = parse_project(
                    {
                        "schema": 1,
                        "name": "demo",
                        "services": {"web": {"command": ["node", "app.js"]}},
                    },
                    platform,
                )
                with patch("dev_tools.projects.planning.shutil.which", return_value="mise"):
                    plan = preparation_plan(spec, binding, Backend(binding))
                self.assertEqual(plan.unresolved, ())
                self.assertEqual(plan.runtime_versions, ("node@24.22.0",))
                self.assertEqual((root / "mise.toml").read_text(), original)
                self.assertFalse(binding.state.exists())

    def test_one_damaged_project_does_not_abort_the_native_list(self):
        from dev_tools.projects.cli import list_projects

        with (
            patch("dev_tools.projects.cli.Registry") as registry,
            patch("dev_tools.projects.cli.engine_for") as engine,
            patch("dev_tools.projects.cli.project_columns", return_value={}),
        ):
            registry.return_value.names.return_value = ("damaged", "valid")
            engine.side_effect = [
                json.JSONDecodeError("Invalid JSON", "", 0),
                SimpleNamespace(status=lambda: {"name": "valid", "ready": True}),
            ]
            rows = list_projects("windows" if os.name == "nt" else "wsl")
        self.assertEqual([row["name"] for row in rows], ["damaged", "valid"])
        self.assertIn("error", rows[0])
        self.assertTrue(rows[1]["ready"])

    def test_discovery_walks_the_metadata_tree_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text('{"scripts":{"dev":"vite"}}')
            (root / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi"]\n')
            with patch("dev_tools.projects.discovery._find", wraps=_find) as find:
                project = discover_project(root)
            self.assertEqual(find.call_count, 1)
            self.assertEqual(set(project["services"]), {"web", "python"})

    def test_invalid_metadata_shapes_raise_readable_validation_errors(self):
        for filename, content in (
            ("package.json", "[]"),
            ("package.json", '{"scripts":[]}'),
            ("pyproject.toml", 'project = "invalid"'),
            ("pyproject.toml", "[project]\ndependencies = 1"),
        ):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / filename).write_text(content)
                with self.assertRaises(ValueError):
                    discover_project(root)

    def test_scanners_and_monitor_do_not_follow_external_directory_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, outside = root / "source", root / "outside"
            source.mkdir()
            outside.mkdir()
            (source / "package.json").write_text('{"scripts":{"dev":"vite"}}')
            (source / ".node-version").write_text("22")
            (source / "A.java").write_text("source")
            (outside / "package.json").write_text('{"scripts":{"dev":"vite"}}')
            (outside / ".node-version").write_text("99")
            (outside / "B.java").write_text("external")
            link = source / "linked"
            if os.name == "nt":
                subprocess.run(
                    [
                        "pwsh",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "New-Item -ItemType Junction -Path $env:DEV_TOOLS_FIXTURE_LINK -Target $env:DEV_TOOLS_FIXTURE_TARGET | Out-Null",
                    ],
                    env={
                        **os.environ,
                        "DEV_TOOLS_FIXTURE_LINK": str(link),
                        "DEV_TOOLS_FIXTURE_TARGET": str(outside),
                    },
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
            else:
                link.symlink_to(outside, target_is_directory=True)
                os.mkfifo(source / "pyproject.toml")
            self.assertFalse(scan_project(source).conflicts)
            self.assertEqual(scan_project(source).tools["node"].version, "22")
            self.assertEqual(set(discover_project(source)["services"]), {"web"})
            self.assertEqual(
                set(file_snapshot(source, BuildSpec("web", (".",), "", "", ""))), {"A.java"}
            )
            (source / "dev-tools.toml").write_text(
                'schema=1\nname="demo"\n[scan]\nroots=["linked"]\n'
            )
            self.assertTrue(scan_project(source).diagnostics)

    @unittest.skipIf(os.name == "nt", "POSIX symlink permissions")
    def test_metadata_file_links_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            target = root / "external.json"
            target.write_text(json.dumps({"engines": {"node": "99"}}))
            (source / "package.json").symlink_to(target)
            self.assertFalse(scan_project(source).tools)
            self.assertFalse(discover_project(source)["services"])
