from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools.projects.detection import _find
from dev_tools.projects.discovery import discover_project
from dev_tools.projects.models import BuildSpec
from dev_tools.projects.monitoring import file_snapshot
from dev_tools.scanner import scan_project


class ProjectDiscoveryPathsTests(unittest.TestCase):
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
