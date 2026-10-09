from __future__ import annotations

import unittest
from pathlib import Path


class ExampleTests(unittest.TestCase):
    def test_examples_use_shared_schema_without_native_binding_values(self):
        import tomllib

        from dev_tools.projects.config import parse_project

        examples = Path(__file__).resolve().parents[1] / "examples"
        paths = list(examples.glob("*.toml"))
        self.assertEqual(len(paths), 5)
        for path in paths:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
            for platform in ("windows", "wsl"):
                with self.subTest(example=path.name, platform=platform):
                    spec = parse_project(raw, platform)
                    self.assertTrue(spec.services)
                    self.assertFalse({"source", "run_user", "cache"} & raw.keys())
