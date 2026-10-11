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
        self,
        root: Path,
        *,
        runtime_available: bool,
        language: str | None = None,
        without_home: bool = False,
        host_binding: str | None = None,
        unreadable_binding: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        commands = root / "bin"
        commands.mkdir()
        mise = commands / "mise"
        mise.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$TEST_MISE_LOG"\n'
            'if [ "$1" = --no-config ]; then shift; fi\n'
            'if [ "$1" = -C ]; then shift 2; fi\n'
            'test "$1" = where && test "$2" = python@3.14.8 || exit 2\n'
            + ('printf "%s\\n" "$TEST_PYTHON_ROOT"\n' if runtime_available else "exit 1\n"),
            encoding="utf-8",
        )
        mise.chmod(0o755)
        wrong_python = commands / "python3"
        wrong_python.write_text("#!/bin/sh\necho wrong-project-python >&2\nexit 99\n")
        wrong_python.chmod(0o755)
        for name in ("socket.py", "subprocess.py", "sitecustomize.py"):
            (root / name).write_text(
                'raise RuntimeError("project Python code must not execute")\n', encoding="utf-8"
            )
        (root / "mise.toml").write_text('[tools]\npython = "3.11"\n', encoding="utf-8")
        env = os.environ.copy()
        if without_home:
            for key in ("HOME", "XDG_CONFIG_HOME", "SUDO_USER"):
                env.pop(key, None)
        env["PATH"] = str(commands) + os.pathsep + env.get("PATH", "")
        env["TEST_PYTHON_ROOT"] = sys.prefix
        env["TEST_MISE_LOG"] = str(root / "mise.log")
        env["DEV_TOOLS_CONFIG"] = str(root / "config.toml")
        if language:
            (root / "config.toml").write_text(f'language = "{language}"\n', encoding="utf-8")
        repository = Path(__file__).parents[1]
        entrypoint = repository / "scripts/dev-tools"
        if host_binding is not None or unreadable_binding:
            scripts = root / "scripts"
            scripts.mkdir()
            entrypoint = scripts / "dev-tools"
            shutil.copyfile(repository / "scripts/dev-tools", entrypoint)
            (root / "src").symlink_to(repository / "src", target_is_directory=True)
            if unreadable_binding:
                (root / ".host-python").mkdir()
            else:
                (root / ".host-python").write_text(host_binding + "\n", encoding="utf-8")
        return subprocess.run(
            ["bash", str(entrypoint), "sysinfo", "--json"],
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

    def test_systemd_entrypoint_works_without_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_entrypoint(Path(temporary), runtime_available=True, without_home=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3.14.8", json.loads(result.stdout)["system"]["python"])

    def test_missing_host_reports_installer_without_falling_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_entrypoint(Path(temporary), runtime_available=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scripts/install.sh", result.stderr)
        self.assertNotIn("wrong-project-python", result.stderr)

    def test_missing_host_uses_configured_language_without_python(self):
        for language, expected in (("zh", "缺失"), ("en", "is missing")):
            with tempfile.TemporaryDirectory() as temporary:
                result = self.run_entrypoint(
                    Path(temporary), runtime_available=False, language=language
                )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)

    def test_external_binding_works_without_mise_or_project_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.run_entrypoint(root, runtime_available=False, host_binding=sys.executable)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("3.14.8", json.loads(result.stdout)["system"]["python"])
            self.assertFalse((root / "mise.log").exists())
            self.assertNotIn("wrong-project-python", result.stderr)

    def test_invalid_binding_never_falls_back_to_mise(self):
        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = self.run_entrypoint(
                    root,
                    runtime_available=True,
                    host_binding=str(root / "missing-python"),
                    unreadable_binding=unreadable,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("scripts/install.sh", result.stderr)
                self.assertFalse((root / "mise.log").exists())


if __name__ == "__main__":
    unittest.main()
