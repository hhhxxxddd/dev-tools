from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from dev_tools import __version__
from dev_tools.controller import verify_source
from dev_tools.release import build
from dev_tools.validation import validate


@unittest.skipUnless(os.name != "nt" and shutil.which("rsync"), "Linux rsync required")
class WslInstallerTests(unittest.TestCase):
    def setUp(self):
        if os.geteuid() != 0:
            self.skipTest("installer validates root privileges")

    def isolated_environment(self, root: Path, *, private_host: bool = False) -> dict[str, str]:
        commands = root / "fake"
        commands.mkdir()
        # Restrict PATH to reviewed installer commands so an external host really
        # works without mise, even on a machine where mise is otherwise installed.
        for name in (
            "bash",
            "cat",
            "chmod",
            "cut",
            "dirname",
            "getent",
            "id",
            "install",
            "ln",
            "readlink",
            "rm",
            "rsync",
        ):
            executable = shutil.which(name)
            self.assertIsNotNone(executable, name)
            (commands / name).symlink_to(executable)
        if private_host:
            mise = commands / "mise"
            mise.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_MISE_LOG"\n'
                'test "$1" = --no-config || exit 1\nshift\n'
                'case "$1" in where) printf "%s\\n" "$TEST_PYTHON_ROOT" ;; '
                "--yes) exit 0 ;; *) exit 1 ;; esac\n"
            )
            mise.chmod(0o755)
        systemctl = commands / "systemctl"
        systemctl.write_text(
            '#!/bin/sh\ncase "$1" in daemon-reload) exit 0 ;; '
            'list-units) printf "%s" "${TEST_ACTIVE_UNITS:-}" ;; *) exit 1 ;; esac\n'
        )
        systemctl.chmod(0o755)
        environment = os.environ.copy()
        environment.pop("DEV_TOOLS_PYTHON", None)
        environment.update(
            {
                "PATH": str(commands),
                "TEST_PYTHON_ROOT": sys.prefix,
                "TEST_MISE_LOG": str(root / "mise.log"),
                "DEV_TOOLS_INSTALL_ROOT": str(root / "deployment"),
                "DEV_TOOLS_COMMAND_ROOT": str(root / "bin"),
                "DEV_TOOLS_UNIT_DIR": str(root / "units"),
                "DEV_TOOLS_CONFIG_ROOT": str(root / "config"),
                "DEV_TOOLS_STATE_ROOT": str(root / "state"),
            }
        )
        return environment

    def run_installer(
        self, environment: dict[str, str], *, repository: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        repository = repository or Path(__file__).resolve().parents[1]
        return subprocess.run(
            [shutil.which("bash"), str(repository / "scripts/install.sh")],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_candidate_package_deployment_preserves_complete_verified_payload(self):
        with tempfile.TemporaryDirectory(prefix="dev-tools-release-install-") as temporary:
            root = Path(temporary)
            repository = Path(__file__).resolve().parents[1]
            built = build(repository, root / "dist")
            package = root / "package with spaces"
            with zipfile.ZipFile(built["archive"]) as archive:
                archive.extractall(package)
            verify_source(package)
            environment = self.isolated_environment(root)
            environment["DEV_TOOLS_PYTHON"] = sys.executable
            registration = root / "config/projects.d/existing.json"
            registration.parent.mkdir(parents=True)
            registration.write_text("keep-registration")
            state = root / "state/existing-state.json"
            state.parent.mkdir()
            state.write_text("keep-state")
            installed = self.run_installer(environment, repository=package)
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            deployment = root / "deployment"
            verify_source(deployment)
            self.assertEqual(validate(deployment, "dev_tools")["version"], __version__)
            self.assertTrue((deployment / "systemd/dev-tools-worker@.service").is_file())
            self.assertFalse((deployment / ".source").exists())
            self.assertEqual(registration.read_text(), "keep-registration")
            self.assertEqual(state.read_text(), "keep-state")
            self.assertEqual(
                (deployment / ".host-python").read_text().strip(),
                str(Path(sys.executable).resolve()),
            )
            self.assertFalse((deployment / "host-mise").exists())
            environment.pop("DEV_TOOLS_PYTHON")
            repeated = self.run_installer(environment, repository=package)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            verify_source(deployment)
            self.assertEqual(registration.read_text(), "keep-registration")
            self.assertEqual(state.read_text(), "keep-state")

    def test_release_metadata_and_systemd_targets_reject_symbolic_links(self):
        for relative in ("pyproject.toml", "systemd"):
            with (
                self.subTest(relative=relative),
                tempfile.TemporaryDirectory(prefix="dev-tools-linked-install-") as temporary,
            ):
                root = Path(temporary)
                environment = self.isolated_environment(root)
                environment["DEV_TOOLS_PYTHON"] = sys.executable
                deployment = root / "deployment"
                deployment.mkdir()
                outside = root / "outside"
                if relative == "systemd":
                    outside.mkdir()
                    marker = outside / "keep.txt"
                else:
                    marker = outside
                marker.write_text("untouched")
                (deployment / relative).symlink_to(outside, target_is_directory=outside.is_dir())
                rejected = self.run_installer(environment)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("Refusing symbolic-link installation target", rejected.stderr)
                self.assertEqual(marker.read_text(), "untouched")
                self.assertFalse((deployment / ".host-python").exists())

    def test_external_host_installs_without_mise_and_reuses_binding(self):
        with tempfile.TemporaryDirectory(prefix="dev-tools-install-") as temporary:
            root = Path(temporary)
            environment = self.isolated_environment(root)
            alias = root / "external-python"
            alias.symlink_to(sys.executable)
            environment["DEV_TOOLS_PYTHON"] = str(alias)
            installed = self.run_installer(environment)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            host = root / "deployment/.host-python"
            self.assertEqual(host.read_text().strip(), str(Path(sys.executable).resolve()))
            self.assertFalse((root / "deployment/host-mise").exists())
            self.assertFalse((root / "mise.log").exists())
            environment.pop("DEV_TOOLS_PYTHON")
            repeated = self.run_installer(environment)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(host.read_text().strip(), str(Path(sys.executable).resolve()))
            self.assertFalse((root / "deployment/host-mise").exists())

    def test_explicit_external_host_can_replace_missing_binding(self):
        with tempfile.TemporaryDirectory(prefix="dev-tools-install-") as temporary:
            root = Path(temporary)
            environment = self.isolated_environment(root)
            deployment = root / "deployment"
            deployment.mkdir()
            (deployment / ".host-python").write_text(str(root / "missing-python") + "\n")
            environment["DEV_TOOLS_PYTHON"] = sys.executable
            installed = self.run_installer(environment)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(
                (deployment / ".host-python").read_text().strip(),
                str(Path(sys.executable).resolve()),
            )

    def test_invalid_external_host_and_missing_binding_preserve_deployment(self):
        with tempfile.TemporaryDirectory(prefix="dev-tools-install-") as temporary:
            root = Path(temporary)
            environment = self.isolated_environment(root, private_host=True)
            deployment = root / "deployment"
            deployment.mkdir()
            host = deployment / ".host-python"
            host.write_text(sys.executable + "\n")
            marker = deployment / "README.md"
            marker.write_text("existing-controller")
            environment["DEV_TOOLS_PYTHON"] = ""
            empty = self.run_installer(environment)
            self.assertNotEqual(empty.returncode, 0)
            self.assertIn("existing absolute executable", empty.stderr)
            self.assertEqual(host.read_text(), sys.executable + "\n")
            self.assertEqual(marker.read_text(), "existing-controller")
            self.assertFalse((root / "mise.log").exists())
            incompatible = root / "old-python"
            environment["DEV_TOOLS_PYTHON"] = str(incompatible)
            for adjustment, expected in (
                ("sys.version_info = (3, 13, 0)", "Python >=3.14.8,<3.15"),
                ("sys.version_info = (3, 14, 7)", "Python >=3.14.8,<3.15"),
                ("sys.version_info = (3, 15, 0)", "Python >=3.14.8,<3.15"),
                ('sys.platform = "win32"', "native Linux Python"),
                ('sys.modules["ssl"] = None', "host Python validation failed"),
                ('sys.modules["tomllib"] = None', "host Python validation failed"),
            ):
                with self.subTest(adjustment=adjustment):
                    incompatible.write_text(
                        f"#!{sys.executable}\nimport sys\n{adjustment}\nexec(sys.argv[-1])\n"
                    )
                    incompatible.chmod(0o755)
                    invalid = self.run_installer(environment)
                    self.assertNotEqual(invalid.returncode, 0)
                    self.assertIn(expected, invalid.stderr)
                    self.assertEqual(host.read_text(), sys.executable + "\n")
                    self.assertEqual(marker.read_text(), "existing-controller")
                    self.assertFalse((root / "mise.log").exists())
            environment.pop("DEV_TOOLS_PYTHON")
            host.write_text(str(root / "missing-python") + "\n")
            missing = self.run_installer(environment)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("existing absolute executable", missing.stderr)
            self.assertEqual(host.read_text(), str(root / "missing-python") + "\n")
            self.assertEqual(marker.read_text(), "existing-controller")
            self.assertFalse((root / "mise.log").exists())

    def test_isolated_deployment_uses_a_private_host_and_preserves_registrations(self):
        with tempfile.TemporaryDirectory(prefix="dev-tools-install-") as temporary:
            root = Path(temporary)
            environment = self.isolated_environment(root, private_host=True)
            config = root / "config"
            (config / "projects.d").mkdir(parents=True)
            registration = config / "projects.d/existing.json"
            registration.write_text("keep-registration")
            result = self.run_installer(environment)
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
            self.assertEqual(host.read_text().strip(), str(Path(sys.executable).resolve()))
            self.assertIn("install python@3.14.8", (root / "mise.log").read_text())
            # A repeated deployment preserves native registrations and rejects active workers.
            environment["TEST_ACTIVE_UNITS"] = (
                "dev-tools-worker@demo:web.service loaded active running"
            )
            blocked = self.run_installer(environment)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("dev-tools -e wsl stop", blocked.stderr)
            environment.pop("TEST_ACTIVE_UNITS")
            mise_log = (root / "mise.log").read_text()
            # The existing binding also allows reinstalling when mise is absent.
            (root / "fake/mise").unlink()
            repeated = self.run_installer(environment)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(registration.read_text(), "keep-registration")
            self.assertEqual((root / "mise.log").read_text(), mise_log)
            # A deployed host works even after mise disappears from the command path.
            version = subprocess.run(
                ["bash", str(root / "bin/dev-tools"), "--version"],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertEqual(version.stdout.strip(), "dev-tools " + __version__)


if __name__ == "__main__":
    unittest.main()
