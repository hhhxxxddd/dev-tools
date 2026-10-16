from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.frameworks import RelatedScope
from dev_tools.projects.config import render_toml
from dev_tools.scanner import scan_project


class FrameworkScopeTests(unittest.TestCase):
    def project(self, root, *, exclude=()):
        (root / "dev-tools.toml").write_text(
            render_toml(
                {
                    "schema": 1,
                    "name": "demo",
                    "scan": {"max_depth": 0, "exclude": list(exclude)},
                    "services": {"app": {"command": ["fixture"], "workdir": "frontend"}},
                }
            )
        )
        frontend = root / "frontend"
        frontend.mkdir(exist_ok=True)
        return frontend

    def test_framework_scope_requires_both_parent_and_related_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            frontend = self.project(root)
            related = frontend / "src-tauri"
            related.mkdir()
            cargo = related / "Cargo.toml"
            cargo.write_text('[package]\nname="demo"\n')
            self.assertNotIn("frontend/src-tauri/Cargo.toml", scan_project(root).scope["files"])
            (frontend / "package.json").write_text("{}")
            scan = scan_project(root)
            scope = next(
                item for item in scan.scope["scopes"] if item["path"] == "frontend/src-tauri"
            )
            self.assertEqual(scope["reasons"], ["tauri"])
            self.assertIn("frontend/src-tauri/Cargo.toml", scan.scope["files"])
            cargo.unlink()
            self.assertNotIn(
                "frontend/src-tauri", [item["path"] for item in scan_project(root).scope["scopes"]]
            )

    def test_inventory_applies_generic_rules_and_enforces_scan_exclusions(self):
        for excluded in ((), ("frontend/native/**",), ("frontend/native/client/Cargo.toml",)):
            with self.subTest(excluded=excluded), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                frontend = self.project(root, exclude=excluded)
                (frontend / "package.json").write_text("{}")
                related = frontend / "native/client"
                related.mkdir(parents=True)
                (related / "Cargo.toml").write_text('[package]\nname="demo"\n')
                with (
                    patch(
                        "dev_tools.inspection.related_scopes",
                        return_value=(RelatedScope("native/client", ("Cargo.toml",), "fixture"),),
                    ),
                    patch("subprocess.run", side_effect=AssertionError("project code executed")),
                ):
                    scan = scan_project(root)
                self.assertEqual(bool(scan.diagnostics), bool(excluded))
                if not excluded:
                    self.assertIn("frontend/native/client/Cargo.toml", scan.scope["files"])

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_framework_scope_never_reads_linked_external_metadata(self):
        for linked_directory in (True, False):
            with (
                self.subTest(directory=linked_directory),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve() / "source"
                root.mkdir()
                frontend = self.project(root)
                (frontend / "package.json").write_text("{}")
                external = root.parent / "external"
                external.mkdir()
                (external / "Cargo.toml").write_text('[package]\nrust-version="99"\n')
                related = frontend / "src-tauri"
                if linked_directory:
                    related.symlink_to(external, target_is_directory=True)
                else:
                    related.mkdir()
                    (related / "Cargo.toml").symlink_to(external / "Cargo.toml")
                original = Path.read_bytes

                def read(path, original=original):
                    self.assertNotEqual(path.name, "Cargo.toml")
                    return original(path)

                with patch.object(Path, "read_bytes", read):
                    scan = scan_project(root)
                self.assertNotIn("frontend/src-tauri/Cargo.toml", scan.scope["files"])
                self.assertNotIn("rust", scan.tools)
