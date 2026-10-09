from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from dev_tools import __version__
from dev_tools.cli import main, parser
from dev_tools.controller import deployment_status, download_release, verify_source
from dev_tools.release import REPOSITORY, build, payload_files


class ControllerTests(unittest.TestCase):
    def invoke(self, arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as outcome:
            main(arguments)
        self.assertEqual(outcome.exception.code, 0)
        return output.getvalue()

    def test_help_and_empty_entry_warn_only_about_missing_deployment(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"DEV_TOOLS_DISTRO": ""}),
        ):
            config = Path(temporary) / "config.toml"
            config.write_text('language = "en"\n[wsl]\ndistro = "CustomDistro"\n', encoding="utf-8")
            with patch(
                "dev_tools.controller.deployment_status", return_value={"state": "missing"}
            ) as probe:
                outputs = [
                    self.invoke(["--config", str(config), *args])
                    for args in ([], ["help"], ["--help"])
                ]
                self.assertEqual(outputs[0], outputs[1])
                self.assertEqual(outputs[1], outputs[2])
                self.assertIn("CustomDistro", outputs[0])
                self.assertIn("dev-tools self update -e wsl", outputs[0])
                self.assertNotRegex(outputs[0], r"[\u4e00-\u9fff]")
                self.assertEqual(probe.call_count, 3)
                probe.assert_called_with("CustomDistro")
                self.invoke(["--config", str(config), "help", "scan"])
                self.invoke(["--config", str(config), "--version"])
                self.assertEqual(probe.call_count, 3)
            with patch(
                "dev_tools.controller.deployment_status", return_value={"state": "installed"}
            ):
                self.assertNotIn(
                    "deployment is missing", self.invoke(["--config", str(config), "help"])
                )

    def test_probe_distinguishes_missing_unavailable_and_installed_without_executing_cli(self):
        with (
            patch("dev_tools.controller.WINDOWS", True),
            patch("dev_tools.controller.shutil.which", return_value="wsl.exe"),
        ):
            for code, output, expected in (
                (3, b"", "missing"),
                (1, b"", "unavailable"),
                (0, b"0.4.0\n", "installed"),
            ):
                with patch(
                    "dev_tools.controller.subprocess.run",
                    return_value=SimpleNamespace(returncode=code, stdout=output),
                ) as run:
                    self.assertEqual(deployment_status("custom")["state"], expected)
                    self.assertEqual(run.call_args.kwargs["timeout"], 5)
                    self.assertEqual(
                        run.call_args.args[0][:6], ["wsl.exe", "-d", "custom", "--", "bash", "-c"]
                    )
                    self.assertNotIn("--version", run.call_args.args[0][-1])
            with patch(
                "dev_tools.controller.subprocess.run",
                side_effect=subprocess.TimeoutExpired("wsl", 5),
            ):
                self.assertEqual(deployment_status("custom")["state"], "unavailable")

    def test_self_wsl_update_bypasses_project_transport_and_retains_config_scope(self):
        with (
            patch("dev_tools.controller.WINDOWS", True),
            patch("dev_tools.controller._install_wsl", return_value=0) as install,
            patch("dev_tools.runtimes.router.forward_remote") as transport,
        ):
            self.invoke(["self", "update", "-e", "wsl"])
            install.assert_called_once()
            transport.assert_not_called()
        for args in (["self", "update", "-e", "wsl"], ["-e", "wsl", "self", "update"]):
            parsed = parser().parse_args(args)
            self.assertEqual(parsed.env, "wsl")
            self.assertFalse(hasattr(parsed, "project_command"))
        with patch(
            "dev_tools.controller.deployment_status",
            return_value={"state": "missing", "version": None, "distro": "Ubuntu"},
        ):
            payload = json.loads(self.invoke(["self", "status", "--json"]))
            self.assertEqual(payload["wsl"]["state"], "missing")

    def test_checkout_origin_and_package_checksums_are_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch(
                    "dev_tools.controller.subprocess.run",
                    return_value=SimpleNamespace(
                        returncode=0, stdout="https://github.com/other/repo.git\n"
                    ),
                ),
                self.assertRaises(ValueError),
            ):
                verify_source(root)
            with patch(
                "dev_tools.controller.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0, stdout="git@github.com:hhhxxxddd/dev-tools.git\n"
                ),
            ):
                verify_source(root)
            built = build(REPOSITORY, root / "dist")
            package = root / "package"
            with zipfile.ZipFile(built["archive"]) as archive:
                archive.extractall(package)
            verify_source(package)
            (package / "scripts/install.sh").write_text("tampered", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_source(package)

    def test_release_download_rejects_hash_mismatch_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch(
                    "dev_tools.controller._read_url",
                    side_effect=HTTPError("url", 404, "missing", {}, None),
                ),
                self.assertRaises(ValueError),
            ):
                download_release(root)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("../outside", "must not extract")
            content = stream.getvalue()
            tag = json.dumps({"tag_name": "v0.4.0"}).encode()
            for checksum in ("0" * 64, hashlib.sha256(content).hexdigest()):
                with (
                    patch(
                        "dev_tools.controller._read_url",
                        side_effect=[tag, (checksum + "  dev-tools-0.4.0.zip\n").encode(), content],
                    ),
                    self.assertRaises(ValueError),
                ):
                    download_release(root)
                self.assertFalse((root.parent / "outside").exists())

    def test_release_build_is_reproducible_and_manifest_uses_its_actual_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = build(REPOSITORY, root / "first", "v" + __version__)
            second = build(REPOSITORY, root / "second", "v" + __version__)
            self.assertEqual(first["sha256"], second["sha256"])
            # Git checkouts with Windows line endings must produce the same assets.
            checkout = root / "crlf-checkout"
            for name, content in payload_files(REPOSITORY).items():
                target = checkout / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content.replace(b"\n", b"\r\n"))
            converted = build(checkout, root / "converted", "v" + __version__)
            self.assertEqual(first["sha256"], converted["sha256"])
            manifest = json.loads((root / "first/dev-tools.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["hash"], first["sha256"])
            self.assertIn("main/pwsh", manifest["depends"])
            self.assertIn("-Action check", manifest["pre_uninstall"])
            self.assertNotIn("wsl", manifest["installer"]["script"])
            with zipfile.ZipFile(first["archive"]) as archive:
                self.assertIn("docs/installation.en.md", archive.namelist())
                self.assertFalse(
                    any("__pycache__" in name or ".local." in name for name in archive.namelist())
                )
            with self.assertRaises(ValueError):
                build(REPOSITORY, root, "v0.2.0")
