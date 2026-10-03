from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "WSL/Linux bash required")
class WslEntrypointTests(unittest.TestCase):
    def run_entrypoint(
        self, root: Path, *, runtime_available: bool
    ) -> subprocess.CompletedProcess[str]:
        commands = root / "bin"
        commands.mkdir()
        mise = commands / "mise"
        mise.write_text(
            "#!/bin/sh\n"
            'test "$1" = where && test "$2" = python@3.14.8 || exit 2\n'
            + ('printf "%s\\n" "$TEST_PYTHON_ROOT"\n' if runtime_available else "exit 1\n"),
            encoding="utf-8",
        )
        mise.chmod(0o755)
        wrong_python = commands / "python3"
        wrong_python.write_text("#!/bin/sh\necho wrong-project-python >&2\nexit 99\n")
        wrong_python.chmod(0o755)
        (root / "mise.toml").write_text('[tools]\npython = "3.11"\n', encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = str(commands) + os.pathsep + env.get("PATH", "")
        env["TEST_PYTHON_ROOT"] = sys.prefix
        return subprocess.run(
            ["bash", str(Path(__file__).parents[1] / "scripts/dev-tools"), "sysinfo", "--json"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_scanner_host_ignores_project_python_and_path_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_entrypoint(Path(temporary), runtime_available=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("3.14.8", payload["system"]["python"])
        self.assertNotIn("wrong-project-python", result.stderr)

    def test_missing_host_reports_installer_without_falling_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_entrypoint(Path(temporary), runtime_available=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rerun scripts/install.sh", result.stderr)
        self.assertNotIn("wrong-project-python", result.stderr)


if __name__ == "__main__":
    unittest.main()
