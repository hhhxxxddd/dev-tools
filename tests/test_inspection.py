from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from dev_tools.constraints import compatible
from dev_tools.i18n import language_scope
from dev_tools.inspection import Inspection, InspectionError, ScanPolicy
from dev_tools.projects.cli import emit
from dev_tools.projects.config import load_project, parse_project, render_toml
from dev_tools.projects.engine import ProjectEngine
from dev_tools.projects.initialization import initialize
from dev_tools.projects.models import ProjectBinding, ProjectError
from dev_tools.projects.planning import fingerprint, preparation_plan, preparation_revisions
from dev_tools.runtimes.platforms.base import Backend
from dev_tools.scanner import scan_project


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class InspectionTests(unittest.TestCase):
    def test_ancestor_workspaces_include_siblings_and_missing_literal_members_are_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.project(root, workdir="apps/frontend/web")
            write(root, "apps/frontend/package.json", '{"workspaces":["web","shared"]}')
            write(root, "apps/frontend/shared/package.json", '{"engines":{"node":"24.x"}}')
            scan = scan_project(root)
            self.assertFalse(scan.blocked)
            self.assertIn("apps/frontend/shared/package.json", scan.scope["files"])
            (root / "apps/frontend/shared/package.json").unlink()
            (root / "apps/frontend/shared").rmdir()
            self.assertTrue(scan_project(root).blocked)

    @unittest.skipIf(os.name == "nt", "POSIX file symlink fixture")
    def test_linked_root_declarations_are_rejected_without_reading_the_target(self):
        for name in ("dev-tools.toml", "mise.toml", ".mise.toml"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve() / "source"
                root.mkdir()
                external = write(root.parent, "external.toml", '[tools]\nnode="24"\n')
                (root / name).symlink_to(external)
                original = Path.read_bytes

                def read(path, external=external, linked=root / name, original=original):
                    self.assertNotEqual(path, external)
                    self.assertNotEqual(path, linked)
                    return original(path)

                with patch.object(Path, "read_bytes", read):
                    self.assertTrue(scan_project(root).blocked)
                    _, blocked = initialize(root)
                    self.assertTrue(blocked)
                    if name == "dev-tools.toml":
                        with self.assertRaises(ProjectError):
                            load_project(root, "wsl")

    def test_check_depth_warning_is_visible_in_both_languages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = self.project(root, discovery="auto", scan={"max_depth": 0})
            binding = ProjectBinding("demo", "windows", root, root, root / "state")
            with patch("dev_tools.projects.planning.shutil.which", return_value="mise"):
                plan = preparation_plan(parse_project(raw, "windows"), binding, Backend(binding))
            for language, text in (
                ("zh", "自动发现已达到深度上限"),
                ("en", "discovery depth limit reached"),
            ):
                with language_scope(language), contextlib.redirect_stdout(io.StringIO()) as output:
                    emit(plan.as_dict() | {"action": "check"}, False)
                    self.assertIn(text, output.getvalue())

    def test_workspace_additions_changes_and_removals_update_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            write(root, "dev-tools.toml", 'schema=1\nname="demo"\n')
            write(root, "package.json", '{"workspaces":["apps/*"]}')
            binding = ProjectBinding("demo", "windows", root, root, root / "state")
            before = fingerprint(binding)
            member = write(root, "apps/new/package.json", '{"engines":{"node":"24"}}')
            added = fingerprint(binding)
            self.assertNotEqual(before, added)
            member.write_text('{"engines":{"node":"22"}}')
            self.assertNotEqual(added, fingerprint(binding))
            member.unlink()
            self.assertEqual(fingerprint(binding), before)

    def test_overlapping_ranges_can_infer_a_selector_but_explicit_conflicts_remain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            write(root, "package.json", '{"engines":{"node":">=22 <25"}}')
            write(root, "frontend/package.json", '{"engines":{"node":">=24.1 <25"}}')
            scan = scan_project(root)
            self.assertFalse(scan.blocked)
            self.assertEqual(scan.tools["node"].version, "24.1.0")
            write(root, "mise.toml", '[tools]\nnode="24"\n')
            write(root, ".mise.toml", '[tools]\nnode="24.22.0"\n')
            self.assertTrue(scan_project(root).conflicts)

    def project(self, root, *, workdir="frontend", **fields):
        raw = {
            "schema": 1,
            "name": "demo",
            "services": {"web": {"command": ["node", "app.js"], "workdir": workdir}},
            **fields,
        }
        write(root, "dev-tools.toml", render_toml(raw))
        write(root, "mise.toml", '[tools]\nnode="24.22.0"\n')
        write(
            root, f"{workdir}/package.json", '{"engines":{"node":"24.x"},"scripts":{"dev":"vite"}}'
        )
        return raw

    def test_manual_workflow_ignores_unrelated_conflicts_and_invalid_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = self.project(root)
            write(root, "experiments/mise.toml", '[tools]\nnode="22"\n')
            unrelated = write(root, "experiments/package.json", "{broken")
            for platform in ("windows", "wsl"):
                spec = parse_project(raw, platform)
                binding = ProjectBinding("demo", platform, root, root, root / "state")
                with patch("dev_tools.projects.planning.shutil.which", return_value="mise"):
                    plan = preparation_plan(spec, binding, Backend(binding))
                self.assertFalse(plan.unresolved)
                before = fingerprint(binding)
                unrelated.write_text("changed and still invalid")
                self.assertEqual(fingerprint(binding), before)
            scan = scan_project(root)
            self.assertFalse(scan.blocked)
            self.assertNotIn("experiments/package.json", scan.scope["files"])

    def test_gitignored_local_source_is_scanned_without_reading_gitignore(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.project(root, workdir=".local/api")
            write(root, ".gitignore", ".local/\nmise.toml\n")
            original = Path.read_bytes

            def read(path):
                self.assertNotEqual(path.name, ".gitignore")
                return original(path)

            with patch.object(Path, "read_bytes", read):
                result = scan_project(root)
            self.assertFalse(result.blocked)
            self.assertIn(".local/api/package.json", result.scope["files"])
            self.assertIn({"path": ".local/api", "reasons": ["workflow"]}, result.scope["scopes"])

    def test_workspaces_bypass_fallback_depth_and_respect_workspace_exclusions(self):
        declarations = (
            ("package.json", '{"workspaces":["a/b/c/d/e/*"],"engines":{"node":"24.x"}}'),
            ("pnpm-workspace.yaml", "packages:\n  - 'a/b/c/d/e/*'\n  - '!a/b/c/d/e/fixture'\n"),
            ("pnpm-workspace.yaml", 'packages: ["a/b/c/d/e/*", "!a/b/c/d/e/fixture"]\n'),
            (
                "pyproject.toml",
                '[tool.uv.workspace]\nmembers=["a/b/c/d/e/*"]\nexclude=["a/b/c/d/e/fixture"]\n',
            ),
            ("pom.xml", "<project><modules><module>a/b/c/d/e/app</module></modules></project>"),
        )
        for filename, content in declarations:
            with (
                self.subTest(filename=filename, content=content),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve()
                write(root, "dev-tools.toml", 'schema=1\nname="demo"\n[scan]\nmax_depth=0\n')
                write(
                    root,
                    "mise.toml",
                    '[tools]\nnode="24.22.0"\npython="3.14.8"\njava="temurin-21"\nmaven="3.9.9"\n',
                )
                write(root, filename, content)
                write(root, "a/b/c/d/e/app/package.json", '{"engines":{"node":"24.x"}}')
                write(root, "a/b/c/d/e/fixture/package.json", "{}")
                scan = scan_project(root)
                self.assertFalse(scan.blocked, scan.as_dict())
                self.assertIn("a/b/c/d/e/app/package.json", scan.scope["files"])
                if filename != "package.json":
                    self.assertNotIn("a/b/c/d/e/fixture/package.json", scan.scope["files"])

    def test_explicit_roots_override_default_ignore_but_do_not_change_sync_or_watch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            write(
                root,
                "dev-tools.toml",
                'schema=1\nname="demo"\n[scan]\nroots=[".local/source"]\nexclude=["**/fixtures/**"]\nmax_depth=1\n',
            )
            write(root, ".local/source/package.json", '{"engines":{"node":"24"}}')
            write(root, ".local/source/fixtures/package.json", "{broken")
            write(root, "unrelated/package.json", "{broken")
            scan = scan_project(root)
            self.assertFalse(scan.blocked)
            self.assertEqual(scan.tools["node"].version, "24")
            self.assertNotIn("unrelated/package.json", scan.scope["files"])
            self.assertNotIn(".local/source/fixtures/package.json", scan.scope["files"])
            self.assertFalse(
                parse_project(
                    {"schema": 1, "name": "demo", "scan": {"exclude": ["examples/**"]}}, "windows"
                ).sync_exclude
            )

    def test_excluding_workflow_or_workspace_members_is_a_configuration_error(self):
        for exclude in (["frontend/**"], ["frontend/package.json"], ["mise.toml"]):
            with self.subTest(exclude=exclude), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                self.project(root, scan={"exclude": exclude})
                scan = scan_project(root)
                self.assertTrue(scan.blocked)
                self.assertTrue(
                    any("scan.exclude conflicts" in str(item.message) for item in scan.diagnostics)
                )

    def test_fallback_depth_reports_truncation_and_nested_projects_are_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            write(root, "package.json", '{"engines":{"node":"24"}}')
            write(root, "a/b/c/d/e/package.json", '{"engines":{"node":"22"}}')
            write(root, "foreign/.git", "gitdir: elsewhere")
            write(root, "foreign/package.json", "{broken")
            write(root, "other/dev-tools.toml", 'schema=1\nname="other"')
            write(root, "other/package.json", "{broken")
            scan = scan_project(root)
            self.assertFalse(scan.blocked)
            self.assertIn("a/b/c/d/e", scan.scope["truncated"])
            self.assertTrue(any("depth limit" in value for value in scan.warnings))
            self.assertNotIn("foreign/package.json", scan.scope["files"])
            self.assertNotIn("other/package.json", scan.scope["files"])

    def test_only_related_incompatible_or_unverifiable_requirements_block(self):
        for requirement, blocked in (
            ("24.x", False),
            (">=24 <25", False),
            ("<24", True),
            (">=24.30.0", True),
            ("some-tag", True),
        ):
            with self.subTest(requirement=requirement), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                raw = self.project(root)
                write(root, "frontend/package.json", json.dumps({"engines": {"node": requirement}}))
                scan = scan_project(root)
                self.assertEqual(scan.blocked, blocked)
                self.assertEqual(scan.tools["node"].version, "24.22.0")
                binding = ProjectBinding("demo", "windows", root, root, root / "state")
                with patch("dev_tools.projects.planning.shutil.which", return_value="mise"):
                    plan = preparation_plan(
                        parse_project(raw, "windows"), binding, Backend(binding)
                    )
                self.assertEqual(bool(plan.unresolved), blocked)

    def test_uv_required_version_is_checked_against_the_root_selector(self):
        for requirement, blocked in ((">=0.12", False), (">=0.14", True)):
            with self.subTest(requirement=requirement), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                write(root, "mise.toml", '[tools]\npython="3.14.8"\nuv="0.13.1"\n')
                write(
                    root,
                    "pyproject.toml",
                    f'[project]\nrequires-python=">=3.12"\n[tool.uv]\nrequired-version="{requirement}"\n',
                )
                self.assertEqual(scan_project(root).blocked, blocked)

    def test_nested_mise_selectors_are_checked_without_becoming_install_sources(self):
        for version, blocked in (("24", False), ("22", True)):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                self.project(root)
                write(root, "frontend/mise.toml", f'[tools]\nnode="{version}"\n')
                scan = scan_project(root)
                self.assertEqual(scan.blocked, blocked)
                self.assertEqual(scan.tools["node"].source, "mise.toml")

    def test_plan_reuses_metadata_reads_and_a_later_operation_observes_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = self.project(root, discovery="auto")
            binding = ProjectBinding("demo", "windows", root, root, root / "state")
            engine = ProjectEngine(parse_project(raw, "windows"), binding, Backend(binding))
            reads = Counter()
            original = Path.read_bytes

            def read(path):
                reads[path] += 1
                return original(path)

            with (
                patch.object(Path, "read_bytes", read),
                patch("dev_tools.projects.planning.shutil.which", return_value="mise"),
            ):
                before = engine.plan().revision
            for name in ("dev-tools.toml", "mise.toml", "frontend/package.json"):
                self.assertEqual(reads[root / name], 1, (name, reads))
            write(
                root, "frontend/package.json", '{"engines":{"node":"24.x"},"description":"updated"}'
            )
            self.assertNotEqual(fingerprint(binding), before)
            self.assertFalse(binding.state.exists())

    def test_native_platform_overrides_select_only_the_target_workdirs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = self.project(root)
            raw["services"]["web"]["platforms"] = {"wsl": {"workdir": "linux-app"}}
            write(root, "dev-tools.toml", render_toml(raw))
            write(root, "linux-app/package.json", '{"engines":{"node":"22"}}')
            for platform, blocked in (("windows", False), ("wsl", True)):
                binding = ProjectBinding("demo", platform, root, root, root / "state")
                with patch("dev_tools.projects.planning.shutil.which", return_value="mise"):
                    plan = ProjectEngine(
                        parse_project(raw, platform), binding, Backend(binding)
                    ).plan()
                self.assertEqual(bool(plan.unresolved), blocked)

    def test_platform_python_roots_are_included_in_scan_and_only_the_target_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = self.project(root)
            raw["services"]["web"]["platforms"] = {"wsl": {"python": {"root": "linux-python"}}}
            write(root, "dev-tools.toml", render_toml(raw))
            write(root, "linux-python/pyproject.toml", '[project]\nrequires-python=">=3.14"\n')
            self.assertIn("linux-python/pyproject.toml", scan_project(root).scope["files"])
            for platform in ("windows", "wsl"):
                with Inspection(root, platform=platform).activate() as inspection:
                    self.assertEqual(
                        root / "linux-python/pyproject.toml" in inspection.files, platform == "wsl"
                    )

    def test_cargo_lock_changes_installation_state_without_changing_declarations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = {
                "schema": 1,
                "name": "demo",
                "tasks": {"compile": {"command": ["cargo", "build"]}},
            }
            write(root, "dev-tools.toml", render_toml(raw))
            write(root, "Cargo.toml", '[package]\nname="demo"\nrust-version="1.80"\n')
            lock = write(root, "Cargo.lock", "version=4\n")
            for platform in ("windows", "wsl"):
                binding = ProjectBinding("demo", platform, root, root, root / "state")
                spec = parse_project(raw, platform)
                declaration = fingerprint(binding, include_locks=False)
                complete = fingerprint(binding)
                revisions = preparation_revisions(spec, binding, ("compile",))
                lock.write_text(lock.read_text() + "# changed\n")
                self.assertEqual(fingerprint(binding, include_locks=False), declaration)
                self.assertNotEqual(fingerprint(binding), complete)
                updated = preparation_revisions(spec, binding, ("compile",))
                self.assertEqual(updated[0], revisions[0])
                self.assertNotEqual(updated[1], revisions[1])

    @unittest.skipIf(os.name == "nt", "POSIX FIFO fixture")
    def test_discovery_rejects_special_framework_files_before_opening_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            write(root, "mise.toml", '[tools]\nnode="24"\n')
            write(root, "package.json", '{"devDependencies":{"vite":"*"},"scripts":{"dev":"vite"}}')
            special = root / "vite.config.js"
            os.mkfifo(special)
            original = Path.read_bytes

            def read(path):
                self.assertNotEqual(path, special)
                return original(path)

            with patch.object(Path, "read_bytes", read), self.assertRaises(InspectionError):
                initialize(root)

    def test_scan_policy_validation_and_platform_independent_globs(self):
        invalid = (
            {"roots": "frontend"},
            {"roots": ["../escape"]},
            {"roots": ["C:\\escape"]},
            {"exclude": ["../escape/**"]},
            {"exclude": ["!frontend"]},
            {"max_depth": True},
            {"max_depth": -1},
            {"max_depth": 33},
            {"unknown": []},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(InspectionError):
                ScanPolicy.parse(value)
            for platform in ("windows", "wsl"):
                with self.assertRaises(ProjectError):
                    parse_project({"schema": 1, "name": "demo", "scan": value}, platform)
        policy = ScanPolicy.parse(
            {"roots": ["apps\\frontend"], "exclude": ["**/fixtures/**", "scratch/"]}
        )
        self.assertEqual(policy.roots, ("apps/frontend",))
        self.assertTrue(policy.excluded("apps/fixtures/demo/package.json"))
        self.assertTrue(policy.excluded("fixtures/package.json"))
        self.assertTrue(policy.excluded("scratch/package.json"))
        self.assertFalse(policy.excluded("Fixtures/package.json"))


class ConstraintTests(unittest.TestCase):
    def test_constraints_prove_containment_or_disjointness_and_preserve_uncertainty(self):
        cases = (
            ("node", "24.22.0", "24", True),
            ("node", "24.22.0", "24.x", True),
            ("node", "24.22.0", "*", True),
            ("pnpm", "10.15.1", "10.15.1+sha512.deadbeef", True),
            ("node", "24.22.0", ">=24 <25", True),
            ("node", "24.22.0", "^22 || >=24", True),
            ("node", "24.22.0", "<24", False),
            ("node", "24", ">=24.22.0", None),
            ("node", "24.22.0", "^24.23.0", False),
            ("node", "0.2.9", "^0.2.3", True),
            ("node", "0.3.0", "^0.2.3", False),
            ("node", "24.22.0", "~24.21.0", False),
            ("python", "3.14.8", ">=3.12,<3.15", True),
            ("python", "3.14.8", "~=3.12", True),
            ("python", "3.14.8", "~=3.12.0", False),
            ("python", "3.14.8", "==3.14.*", True),
            ("node", "lts", ">=24", None),
            ("node", "24", "latest", None),
            ("python", "3.14.8", ">=3.12,!=3.14.8", None),
        )
        for tool, selected, requirement, expected in cases:
            with self.subTest(selected=selected, requirement=requirement):
                self.assertIs(compatible(tool, selected, requirement), expected)
        self.assertTrue(compatible("java", "temurin-21", "17", minimum=True))
        self.assertFalse(compatible("java", "temurin-17", "21", minimum=True))
        self.assertFalse(compatible("java", "temurin-21", "corretto-21"))


if __name__ == "__main__":
    unittest.main()
