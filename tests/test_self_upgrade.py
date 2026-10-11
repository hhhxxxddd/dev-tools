from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools import __version__, controller
from dev_tools.cli import main


@unittest.skipUnless(shutil.which("git"), "Git is required")
class SourceUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "upstream"
        self.checkout = self.root / "checkout"
        self.real_run = subprocess.run
        self.git("init", "--initial-branch=main", str(self.remote))
        self.git("-C", str(self.remote), "config", "user.email", "test@example.invalid")
        self.git("-C", str(self.remote), "config", "user.name", "Test")
        self.commit("first")
        self.git("clone", str(self.remote), str(self.checkout))
        self.git(
            "-C",
            str(self.checkout),
            "remote",
            "set-url",
            "origin",
            "https://github.com/hhhxxxddd/dev-tools.git",
        )
        self.calls = []

    def git(self, *arguments):
        return self.real_run(["git", *arguments], check=True, capture_output=True, text=True)

    def commit(self, text):
        (self.remote / "version.txt").write_text(text, encoding="utf-8")
        self.git("-C", str(self.remote), "add", ".")
        self.git("-C", str(self.remote), "commit", "-m", text)

    def run_git(self, command, **kwargs):
        self.calls.append(command)
        if "fetch" in command:
            command = [str(self.remote) if token == "origin" else token for token in command]
        return self.real_run(command, **kwargs)

    def test_fast_forward_fetches_new_code_without_running_installers(self):
        self.commit("second")
        with patch.object(controller.subprocess, "run", side_effect=self.run_git):
            controller._refresh_checkout(self.checkout)
        self.assertEqual((self.checkout / "version.txt").read_text(), "second")
        self.assertTrue(any("fetch" in command and "origin" in command for command in self.calls))
        self.assertTrue(
            any("merge" in command and "--ff-only" in command for command in self.calls)
        )

    def test_local_changes_rejected_before_fetch(self):
        (self.checkout / "version.txt").write_text("local work", encoding="utf-8")
        with (
            patch.object(controller.subprocess, "run", side_effect=self.run_git),
            self.assertRaisesRegex(ValueError, "local changes"),
        ):
            controller._refresh_checkout(self.checkout)
        self.assertFalse(any("fetch" in command for command in self.calls))
        self.assertEqual((self.checkout / "version.txt").read_text(), "local work")

    def test_divergence_cannot_create_merge_commit(self):
        self.commit("upstream change")
        self.git("-C", str(self.checkout), "config", "user.email", "test@example.invalid")
        self.git("-C", str(self.checkout), "config", "user.name", "Test")
        (self.checkout / "local.txt").write_text("local commit", encoding="utf-8")
        self.git("-C", str(self.checkout), "add", ".")
        self.git("-C", str(self.checkout), "commit", "-m", "local commit")
        original = self.git("-C", str(self.checkout), "rev-parse", "HEAD").stdout
        with (
            patch.object(controller.subprocess, "run", side_effect=self.run_git),
            self.assertRaisesRegex(ValueError, "Source update failed"),
        ):
            controller._refresh_checkout(self.checkout)
        self.assertEqual(self.git("-C", str(self.checkout), "rev-parse", "HEAD").stdout, original)


class SelfUpgradeCliTests(unittest.TestCase):
    def test_top_level_upgrade_uses_the_controller_without_project_transport(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            for arguments in (
                ["upgrade", "-e", "wsl", "--source", temporary],
                ["-e", "wsl", "upgrade", "--source", temporary],
                ["self", "upgrade", "-e", "wsl", "--source", temporary],
            ):
                with (
                    patch.object(controller, "_upgrade", return_value=0) as upgrade,
                    patch(
                        "dev_tools.runtimes.router.forward_remote",
                        side_effect=AssertionError("controller must not use project transport"),
                    ),
                    self.assertRaises(SystemExit) as result,
                ):
                    main(["--config", str(config), *arguments])
                self.assertEqual(result.exception.code, 0)
                args = upgrade.call_args.args[0]
                self.assertEqual(
                    (args.self_action, args.env, args.source), ("upgrade", "wsl", temporary)
                )
                self.assertFalse(hasattr(args, "project_command"))

    def test_top_level_help_lists_upgrade_and_its_help_is_readonly_in_both_languages(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            for language, description in (
                ("zh", "升级 dev-tools 自身"),
                ("en", "Upgrade dev-tools itself"),
            ):
                config.write_text(f'language = "{language}"\n', encoding="utf-8")
                with (
                    patch.object(
                        controller, "deployment_status", return_value={"state": "installed"}
                    ),
                    patch.object(
                        controller, "_upgrade", side_effect=AssertionError("help must not upgrade")
                    ),
                ):
                    output = io.StringIO()
                    with (
                        contextlib.redirect_stdout(output),
                        self.assertRaises(SystemExit) as result,
                    ):
                        main(["--config", str(config), "help"])
                    self.assertEqual(result.exception.code, 0)
                    self.assertIn(description, output.getvalue())
                    for arguments in (["help", "upgrade"], ["upgrade", "--help"]):
                        output = io.StringIO()
                        with (
                            contextlib.redirect_stdout(output),
                            self.assertRaises(SystemExit) as result,
                        ):
                            main(["--config", str(config), *arguments])
                        self.assertEqual(result.exception.code, 0)
                        self.assertIn("--source", output.getvalue())
                        self.assertIn("-e", output.getvalue())

    def test_native_redeploy_uses_recorded_source_without_fetching_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "checkout"
            (root / ".source").write_text(str(source), encoding="utf-8")
            args = SimpleNamespace(
                self_action="update",
                env="wsl",
                source=None,
                json=False,
                settings=SimpleNamespace(distro="Ubuntu"),
            )
            with (
                patch.object(controller, "WINDOWS", False),
                patch.object(controller, "REPOSITORY", root),
                patch("os.geteuid", return_value=0, create=True),
                patch.object(
                    controller,
                    "download_release",
                    side_effect=AssertionError("redeploy must not fetch"),
                ),
                patch.object(controller, "_install_wsl", return_value=0) as install,
            ):
                self.assertEqual(controller.run(args), 0)
                install.assert_called_once_with(source, "Ubuntu")
                (root / ".source").unlink()
                with self.assertRaisesRegex(ValueError, "Redeployment source is unavailable"):
                    controller.run(args)

    def test_wsl_release_upgrade_does_not_require_stopping_windows_workers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(
                env="wsl", source=None, settings=SimpleNamespace(distro="Ubuntu")
            )
            with (
                patch.object(controller, "WINDOWS", True),
                patch.object(controller, "REPOSITORY", root),
                patch.object(
                    controller,
                    "_require_stopped_windows",
                    side_effect=AssertionError("unrelated Windows workers"),
                ),
                patch.object(controller, "download_release", return_value=root / "download"),
                patch.object(controller, "_install_wsl", return_value=0),
            ):
                self.assertEqual(controller._upgrade(args), 0)

    def test_short_and_long_version_do_not_probe_deployments(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                controller, "deployment_status", side_effect=AssertionError("version probe")
            ),
        ):
            for flag in ("-v", "--version"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit:
                    main(["--config", str(Path(temporary) / "config.toml"), flag])
                self.assertEqual(exit.exception.code, 0)
                self.assertIn(__version__, output.getvalue())

    def test_upgrade_refreshes_then_deploys_requested_wsl_source(self):
        source = Path.cwd()
        args = SimpleNamespace(
            env="wsl", source=str(source), settings=SimpleNamespace(distro="Ubuntu")
        )
        events = []
        with (
            patch.object(controller, "WINDOWS", True),
            patch("dev_tools.projects.registry.Registry.names", return_value=()),
            patch.object(
                controller, "_refresh_checkout", side_effect=lambda _: events.append("fetch")
            ),
            patch.object(
                controller, "_install_wsl", side_effect=lambda *_: events.append("install") or 0
            ),
        ):
            self.assertEqual(controller._upgrade(args), 0)
        self.assertEqual(events, ["fetch", "install"])

    def test_top_level_timeout_is_concise_and_hides_command_arguments(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                controller,
                "_upgrade",
                side_effect=subprocess.TimeoutExpired(["git", "secret-token"], 1),
            ),
        ):
            output = io.StringIO()
            with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as exit:
                main(
                    [
                        "--config",
                        str(Path(temporary) / "config.toml"),
                        "self",
                        "upgrade",
                        "-e",
                        "win" if os.name == "nt" else "wsl",
                    ]
                )
            self.assertEqual(exit.exception.code, 1)
            self.assertNotIn("secret-token", output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())
