import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.controller import verify_source
from dev_tools.package_smoke import smoke
from dev_tools.release import REPOSITORY, build, payload_files, release_notes
from dev_tools.validation import validate


class ReleaseContractsTests(unittest.TestCase):
    def test_official_build_requires_clean_source_and_matching_tag(self):
        if not shutil.which("git"):
            self.skipTest("Git unavailable")
        with tempfile.TemporaryDirectory(prefix="dev-tools-release-") as temporary:
            root = Path(temporary) / "source"
            for name, content in payload_files(REPOSITORY).items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(root), *args],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                ).stdout.strip()

            git("init")
            git("config", "user.name", "Release fixture")
            git("config", "user.email", "release@example.invalid")
            git("config", "core.autocrlf", "false")
            git("remote", "add", "origin", "https://github.com/hhhxxxddd/dev-tools.git")
            git("add", ".")
            git("commit", "-m", "Fixture")
            tag = "v" + __version__
            git("tag", tag)
            output = Path(temporary) / "output"
            original_run = subprocess.run

            def foreign_owned_checkout(argv, **kwargs):
                if (
                    argv[0] == "git"
                    and argv[-1] == f"refs/tags/{tag}^{{commit}}"
                    and f"safe.directory={root.resolve()}" not in argv
                ):
                    return subprocess.CompletedProcess(argv, 128, "", "detected dubious ownership")
                return original_run(argv, **kwargs)

            with patch("dev_tools.release.subprocess.run", side_effect=foreign_owned_checkout):
                result = build(root, output, tag, official=True)
            self.assertEqual(
                result["provenance"], {"commit": git("rev-parse", "HEAD"), "dirty": False}
            )
            (root / "README.md").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "clean tagged"):
                build(root, output, tag, official=True)
            candidate = build(root, output, tag)
            self.assertTrue(candidate["provenance"]["dirty"])
            git("add", ".")
            git("commit", "-m", "Next source")
            with self.assertRaisesRegex(ValueError, "tag must point"):
                build(root, output, tag, official=True)

    def test_package_checksum_is_verified_before_entrypoint_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            built = build(REPOSITORY, output)
            archive = Path(built["archive"])
            archive.write_bytes(archive.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                smoke(archive)

    def test_release_notes_use_source_commit_and_require_real_local_links(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs/releases").mkdir(parents=True)
            (root / "docs/installation.md").write_text("# Installation\n")
            (root / "docs/releases/v1.0.0.md").write_text("[Install](../installation.md)\n")
            notes = release_notes(root, "v1.0.0", commit)
            self.assertIn(f"/blob/{commit}/docs/installation.md", notes)
            self.assertNotIn("](../", notes)
            with self.assertRaises(ValueError):
                release_notes(root, "v../../outside", commit)

    def test_metadata_links_and_bilingual_toml_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src/example").mkdir(parents=True)
            (root / "src/example/__init__.py").write_text('__version__ = "1.0.0"\n')
            (root / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
            (root / "README.md").write_text("# 中文\n\n```toml\nvalue = 1\n```\n", encoding="utf-8")
            (root / "README.en.md").write_text("# English\n\n```toml\nvalue = 1\n```\n")
            self.assertEqual(validate(root, "example")["pairs"], 1)
            (root / "README.en.md").write_text("# English\n\n```toml\nvalue = 2\n```\n")
            with self.assertRaisesRegex(ValueError, "TOML examples differ"):
                validate(root, "example")
            (root / "README.md").write_text("# 中文\n[missing](missing.md)\n", encoding="utf-8")
            (root / "README.en.md").write_text("# English\n")
            with self.assertRaisesRegex(ValueError, "missing local link"):
                validate(root, "example")
            with self.assertRaisesRegex(ValueError, "versions must agree"):
                validate(root, "example", "v2.0.0")

    def test_receipt_does_not_contain_machine_bindings(self):
        import zipfile

        with tempfile.TemporaryDirectory() as temporary:
            result = build(REPOSITORY, Path(temporary))
            for name in ("SHA256SUMS", "dev-tools.json"):
                self.assertNotIn(b"\r\n", (Path(temporary) / name).read_bytes())
            with zipfile.ZipFile(result["archive"]) as archive:
                receipt = json.loads(archive.read(".release.json"))
                self.assertEqual(receipt["schema_version"], 1)
                self.assertEqual(receipt["provenance"], result["provenance"])
                for name in receipt["files"]:
                    self.assertNotIn(".host-python", name)
                    self.assertNotIn("host-mise", name)
                    self.assertNotIn(".local.", name)

    def test_package_receipt_rejects_unlisted_code_and_unknown_format(self):
        import zipfile

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = build(REPOSITORY, output / "dist")
            package = output / "package"
            with zipfile.ZipFile(result["archive"]) as archive:
                archive.extractall(package)
            verify_source(package)
            injected = package / "src/dev_tools/unlisted.py"
            injected.write_text("raise RuntimeError('unreviewed code')\n")
            with self.assertRaises(ValueError):
                verify_source(package)
            injected.unlink()
            receipt_path = package / ".release.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["schema_version"] = 99
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_source(package)

    def test_package_rejects_malformed_version_metadata_without_traceback(self):
        import zipfile

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = build(REPOSITORY, output / "dist")
            package = output / "package"
            with zipfile.ZipFile(result["archive"]) as archive:
                archive.extractall(package)
            receipt_path = package / ".release.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            for content in (
                'name = "fixture"\n',
                'project = "invalid"\n',
                '[project]\nname = "fixture"\n',
            ):
                with self.subTest(content=content):
                    metadata = package / "pyproject.toml"
                    metadata.write_text(content, encoding="utf-8")
                    receipt["files"]["pyproject.toml"] = hashlib.sha256(
                        metadata.read_bytes()
                    ).hexdigest()
                    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "unverified controller package"):
                        verify_source(package)


if __name__ == "__main__":
    unittest.main()
