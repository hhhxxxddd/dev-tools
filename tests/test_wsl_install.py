from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name != "nt" and shutil.which("rsync"), "Linux rsync required")
class WslInstallerTests(unittest.TestCase):
    def test_isolated_deployment_uses_a_private_host_and_preserves_registrations(self):
        if os.geteuid() != 0:
            self.skipTest("installer validates root privileges")
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="dev-tools-install-") as temporary:
            root = Path(temporary)
            commands = root / "fake"
            commands.mkdir()
            mise = commands / "mise"
            mise.write_text(
                '#!/bin/sh\ntest "$1" = --no-config || exit 1\nshift\ncase "$1" in where) printf "%s\\n" "$TEST_PYTHON_ROOT" ;; --yes) exit 0 ;; *) exit 1 ;; esac\n'
            )
            mise.chmod(0o755)
            systemctl = commands / "systemctl"
            systemctl.write_text(
                '#!/bin/sh\ncase "$1" in daemon-reload) exit 0 ;; list-units) printf "%s" "${TEST_ACTIVE_UNITS:-}" ;; *) exit 1 ;; esac\n'
            )
            systemctl.chmod(0o755)
            config = root / "config"
            (config / "projects.d").mkdir(parents=True)
            registration = config / "projects.d/existing.json"
            registration.write_text("keep-registration")
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": str(commands) + os.pathsep + environment["PATH"],
                    "TEST_PYTHON_ROOT": sys.prefix,
                    "DEV_TOOLS_INSTALL_ROOT": str(root / "deployment"),
                    "DEV_TOOLS_COMMAND_ROOT": str(root / "bin"),
                    "DEV_TOOLS_UNIT_DIR": str(root / "units"),
                    "DEV_TOOLS_CONFIG_ROOT": str(config),
                    "DEV_TOOLS_STATE_ROOT": str(root / "state"),
                }
            )
            result = subprocess.run(
                ["bash", str(repository / "scripts/install.sh")],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(registration.read_text(), "keep-registration")
            self.assertEqual(len(list((root / "units").glob("dev-tools-*.service"))), 1)
            for relative in (
                "docs/project-config.md",
                "docs/project-config.en.md",
                "docs/usage.md",
                "docs/usage.en.md",
                "docs/development.md",
                "docs/development.en.md",
                "CHANGELOG.md",
                "examples/README.md",
                "config/settings.example.toml",
                "AGENTS.md",
            ):
                self.assertTrue((root / "deployment" / relative).is_file(), relative)
            self.assertIn(str(root / "deployment/README.md"), (config / "README.md").read_text())
            host = root / "deployment/.host-python"
            self.assertEqual(host.read_text().strip(), str(Path(sys.prefix) / "bin/python3"))
            # A repeated deployment preserves native registrations and rejects active workers.
            environment["TEST_ACTIVE_UNITS"] = (
                "dev-tools-worker@demo:web.service loaded active running"
            )
            blocked = subprocess.run(
                ["bash", str(repository / "scripts/install.sh")],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("dev-tools -e wsl stop", blocked.stderr)
            environment.pop("TEST_ACTIVE_UNITS")
            repeated = subprocess.run(
                ["bash", str(repository / "scripts/install.sh")],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(registration.read_text(), "keep-registration")
            # A deployed host works even after mise disappears from the command path.
            mise.unlink()
            version = subprocess.run(
                ["bash", str(root / "bin/dev-tools"), "--version"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("0.4.0", version.stdout)


if __name__ == "__main__":
    unittest.main()
