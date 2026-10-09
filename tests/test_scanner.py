from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dev_tools.scanner import render_mise, scan_project


class ScannerTests(unittest.TestCase):
    def test_unsafe_ranges_are_reported_without_selecting_a_version(self) -> None:
        for tool, raw in (
            ("node", "<20"),
            ("node", "^18 || >=22"),
            ("node", ">=22,<20"),
            ("python", "<3.12"),
        ):
            with self.subTest(tool=tool, raw=raw), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                if tool == "node":
                    (root / "package.json").write_text(json.dumps({"engines": {tool: raw}}))
                else:
                    (root / "pyproject.toml").write_text(f'[project]\nrequires-python="{raw}"\n')
                result = scan_project(root)
                self.assertNotIn(tool, result.tools)
                self.assertEqual(result.unresolved[0].raw, raw)

    def test_lower_bound_and_caret_do_not_select_versions_below_the_minimum(self) -> None:
        for raw, expected in (
            (">=22.1.5 <23", "22.1.5"),
            ("^22.1.0", "22.1"),
            ("^0.0.0", "0.0.0"),
            (">=22,<22.5", "22.0.0"),
        ):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "package.json").write_text(json.dumps({"engines": {"node": raw}}))
                self.assertEqual(scan_project(root).tools["node"].version, expected)

    def test_required_tools_without_versions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text('{"scripts":{"dev":"vite"}}')
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
            (root / "uv.lock").write_text('version=1\nrequires-python=">=3.12"\n')
            result = scan_project(root)
            self.assertEqual({item.tool for item in result.unresolved}, {"node", "pnpm", "uv"})
            self.assertEqual(result.as_dict()["schema_version"], 3)

    def test_explicit_version_supersedes_an_unsupported_lower_priority_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".nvmrc").write_text("18.20.0\n")
            (root / "package.json").write_text('{"engines":{"node":"^18 || >=22"}}')
            result = scan_project(root)
            self.assertEqual(result.tools["node"].version, "18.20.0")
            self.assertFalse(result.unresolved)
            self.assertTrue(result.warnings)

    def test_bun_package_manager_version_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                '{"packageManager":"bun@1.2.3","engines":{"node":"22"}}'
            )
            self.assertEqual(scan_project(root).tools["bun"].version, "1.2.3")

    def test_spring_wrapper_and_frontend_generate_project_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".mvn/wrapper").mkdir(parents=True)
            (root / ".mvn/wrapper/maven-wrapper.properties").write_text(
                "distributionUrl=https://repo.example/apache-maven-3.9.9-bin.zip\n",
                encoding="utf-8",
            )
            (root / "pom.xml").write_text(
                """<project>
  <properties><maven.compiler.release>21</maven.compiler.release></properties>
</project>
""",
                encoding="utf-8",
            )
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "engines": {"node": ">=22"},
                        "packageManager": "pnpm@10.15.1+sha512.deadbeef",
                    }
                ),
                encoding="utf-8",
            )

            result = scan_project(root)

            self.assertEqual(result.tools["java"].version, "temurin-21")
            self.assertEqual(result.tools["node"].version, "22")
            self.assertEqual(result.tools["pnpm"].version, "10.15.1")
            self.assertNotIn("maven", result.tools)
            self.assertEqual(result.wrappers["maven"].version, "3.9.9")
            self.assertFalse(result.conflicts)
            content = render_mise(result)
            self.assertIn('java = "temurin-21"', content)
            self.assertNotIn("maven =", content)

    def test_python_version_file_beats_project_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".python-version").write_text("3.12.8\n", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[project]\nrequires-python = ">=3.11,<3.13"\n',
                encoding="utf-8",
            )

            result = scan_project(root)

            self.assertEqual(result.tools["python"].version, "3.12.8")
            self.assertTrue(any("lower-priority" in warning for warning in result.warnings))

    def test_maven_compiler_property_reference_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text(
                """<project><properties>
  <java.version>25</java.version>
  <maven.compiler.release>${java.version}</maven.compiler.release>
</properties></project>
""",
                encoding="utf-8",
            )

            result = scan_project(root)

            self.assertEqual(result.tools["java"].version, "temurin-25")

    def test_equal_priority_monorepo_conflict_blocks_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "apps/a").mkdir(parents=True)
            (root / "apps/b").mkdir(parents=True)
            (root / "apps/a/.nvmrc").write_text("20\n", encoding="utf-8")
            (root / "apps/b/.nvmrc").write_text("22\n", encoding="utf-8")

            result = scan_project(root)

            self.assertNotIn("node", result.tools)
            self.assertEqual(result.conflicts[0].tool, "node")

    def test_existing_mise_file_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = '[tools]\nnode = "20" # keep this comment\n'
            config = root / "mise.toml"
            config.write_text(original, encoding="utf-8")
            (root / ".nvmrc").write_text("22\n", encoding="utf-8")

            result = scan_project(root)

            self.assertEqual(result.existing_config, config.resolve())
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_uv_required_version_and_python_range_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                """[project]
requires-python = ">=3.12"

[tool.uv]
required-version = ">=0.8.0"
""",
                encoding="utf-8",
            )

            result = scan_project(root)

            self.assertEqual(result.tools["python"].version, "3.12")
            self.assertEqual(result.tools["uv"].version, "0.8")


if __name__ == "__main__":
    unittest.main()
