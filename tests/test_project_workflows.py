from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from dev_tools.cli import cmd_init, main
from dev_tools.projects.config import parse_project
from dev_tools.projects.models import ProjectBinding
from dev_tools.projects.planning import preparation_plan
from dev_tools.runtimes.platforms.base import Backend
from dev_tools.scanner import scan_project
from dev_tools.workflows import initialization_plan, write_initialization


class ProjectWorkflowTests(unittest.TestCase):
    def test_malformed_metadata_is_not_hidden_by_a_valid_version_file(self):
        for name, content, code in (
            ("package.json", "{broken", "parse-error"),
            ("package.json", "[]", "invalid-metadata"),
            ("pyproject.toml", "[broken", "parse-error"),
            ("pom.xml", "<project>", "parse-error"),
            (".python-version", "", "invalid-metadata"),
        ):
            with (
                self.subTest(name=name, content=content),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                (root / ".nvmrc").write_text("22")
                (root / name).write_text(content)
                result = scan_project(root)
                self.assertTrue(result.blocked)
                self.assertEqual(result.diagnostics[0].source, name)
                self.assertEqual(result.diagnostics[0].code, code)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        cmd_init(argparse.Namespace(path=root, dry_run=False, json=True)), 2
                    )
                self.assertFalse((root / "mise.toml").exists())

    def test_invalid_encoding_has_a_file_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_bytes(b"\xff\xfe")
            self.assertEqual(scan_project(root).diagnostics[0].code, "read-error")

    def test_native_state_and_private_hosts_do_not_declare_project_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".nvmrc").write_text("22")
            for directory in (".dev-tools/windows", "host-mise/installs/python"):
                cache = root / directory
                cache.mkdir(parents=True)
                (cache / "mise.toml").write_text('[tools]\nnode="24"\n')
                (cache / "package.json").write_text("{broken")
            result = scan_project(root)
            self.assertFalse(result.blocked)
            self.assertEqual(result.tools["node"].version, "22")

    def test_existing_invalid_config_is_preserved_and_cannot_be_prepared(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "mise.toml"
            config.write_text("[broken")
            plan = write_initialization(initialization_plan(root))
            self.assertEqual(plan.action, "preserved")
            self.assertEqual(config.read_text(), "[broken")
            spec = parse_project({"schema": 1, "name": "demo"}, "windows")
            binding = ProjectBinding("demo", "windows", root, root, root / "state")
            result = preparation_plan(spec, binding, Backend(binding))
            self.assertTrue(result.unresolved)
            self.assertFalse(binding.state.exists())

    def test_invalid_mise_tool_structure_cannot_fall_back_to_other_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mise.toml").write_text("[tools]\nnode=22\n")
            (root / ".nvmrc").write_text("22")
            result = scan_project(root)
            self.assertTrue(result.blocked)
            self.assertEqual(result.diagnostics[0].source, "mise.toml")
            self.assertEqual(result.diagnostics[0].code, "invalid-metadata")

    def test_init_preview_never_executes_mise_or_project_scripts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mise.toml").write_text('[tools]\nnode="22"\n')
            (root / "package.json").write_text('{"scripts":{"postinstall":"touch executed"}}')
            output = io.StringIO()
            with patch("subprocess.run") as execute, contextlib.redirect_stdout(output):
                code = cmd_init(argparse.Namespace(path=root, dry_run=True, json=True))
            execute.assert_not_called()
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["schema_version"], 3)
            self.assertEqual(payload["project_config"]["action"], "preview")
            self.assertFalse((root / "executed").exists())

    def test_init_does_not_overwrite_a_config_created_after_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".nvmrc").write_text("22")
            plan = initialization_plan(root)
            config = root / ".mise.toml"
            config.write_text('[tools]\nnode="24"\n')
            result = write_initialization(plan)
            self.assertEqual(result.action, "preserved")
            self.assertIn('node="24"', config.read_text())
            self.assertFalse((root / "mise.toml").exists())

    def test_explicit_environment_routes_without_importing_unix_modules(self):
        with (
            patch("dev_tools.runtimes.router.forward_remote", return_value=0) as route,
            self.assertRaises(SystemExit) as stopped,
        ):
            main(["--env", "win", "status", "demo", "--json"])
        self.assertEqual(stopped.exception.code, 0)
        route.assert_called_once_with("win", ["status", "demo", "--json"], settings=ANY)
        if sys.platform == "win32":
            self.assertNotIn("dev_tools.runtimes.platforms.wsl", sys.modules)


if __name__ == "__main__":
    unittest.main()
