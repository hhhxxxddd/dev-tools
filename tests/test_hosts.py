from __future__ import annotations

import argparse
import codecs
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.cli import main, parser
from dev_tools.hosts import execute
from dev_tools.hosts_config import (
    BEGIN,
    END,
    Document,
    HostsStore,
    digest,
    parse_entries,
    validate_content,
    validate_profile,
)
from dev_tools.i18n import language_scope
from dev_tools.settings import Settings, defaults


class HostsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "system/hosts"
        self.path.parent.mkdir()
        self.original = (
            codecs.BOM_UTF8
            + "# 原有条目\r\n127.0.0.1 localhost\r\n# 192.0.2.5 disabled.test # 示例\r\n".encode()
        )
        self.path.write_bytes(self.original)
        self.store = HostsStore(self.root / "user/hosts")
        self.settings = Settings(self.root / "config.toml", "missing", defaults(), {})

    def profile(self, name="dev", content="127.0.0.1 app.test\n::1 app.test\n"):
        path = self.store.profile(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def execute(self, operation, **kwargs):
        args = argparse.Namespace(
            hosts_action=operation,
            json=True,
            profile=None,
            query="",
            backup=None,
            settings=self.settings,
        )
        for key, value in kwargs.items():
            setattr(args, key, value)

        def write(before, after):
            self.assertEqual(self.path.read_bytes(), before)
            self.path.write_bytes(after)
            return True

        with (
            patch("builtins.input", return_value=""),
            patch("dev_tools.hosts.native_paths", return_value=(self.path, self.store)),
            patch(
                "dev_tools.runtimes.platforms.windows_hosts.write_system_hosts", side_effect=write
            ),
        ):
            return execute(args)

    def test_list_is_read_only_and_includes_disabled_ipv6_and_filters(self):
        value = self.execute("list")
        self.assertTrue(value["complete"])
        self.assertEqual(len(value["entries"]), 2)
        self.assertTrue(value["entries"][0]["enabled"])
        self.assertFalse(value["entries"][1]["enabled"])
        self.assertEqual(len(self.execute("list", query="示例")["entries"]), 1)
        self.assertFalse(self.store.root.exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_switch_off_and_restore_preserve_manual_content_bom_and_newlines(self):
        self.profile()
        result = self.execute("use", profile="dev")
        switched = self.path.read_bytes()
        self.assertTrue(result["changed"])
        self.assertTrue(switched.startswith(self.original))
        self.assertNotIn(b"\n", switched.replace(b"\r\n", b""))
        entries = self.execute("list")["entries"]
        self.assertEqual([item["profile"] for item in entries], [None, None, "dev", "dev"])
        self.assertEqual(self.execute("profiles")["profiles"][0]["status"], "active")
        self.execute("off")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.execute("restore")
        self.assertEqual(self.path.read_bytes(), switched)
        self.assertEqual(len(self.execute("backups")["backups"]), 3)

    def test_repeat_activation_and_off_are_noops_without_backups(self):
        self.assertFalse(self.execute("off")["changed"])
        self.assertFalse(self.store.root.exists())
        self.profile()
        self.execute("use", profile="dev")
        records = self.store.backups()
        self.assertFalse(self.execute("use", profile="dev")["changed"])
        self.assertEqual(self.store.backups(), records)

    def test_conflicting_profile_rejected_before_backup_or_write(self):
        self.profile(content="192.0.2.8 LOCALHOST.\n")
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.execute("use", profile="dev")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse((self.store.root / "backups").exists())
        validate_content("127.0.0.1 a.test\n::1 A.TEST\n")

    def test_profile_to_profile_switch_replaces_only_managed_content(self):
        self.profile()
        self.profile("test", "192.0.2.8 app.test\n")
        self.execute("use", profile="dev")
        self.execute("use", profile="test")
        content = Document.decode(self.path.read_bytes()).text
        self.assertEqual(content.count(BEGIN), 1)
        self.assertIn("192.0.2.8 app.test", content)
        self.assertTrue(self.path.read_bytes().startswith(self.original))

    def test_damaged_managed_markers_do_not_get_overwritten(self):
        self.profile()
        for text in (
            BEGIN + "dev\n",
            END + "\n",
            BEGIN + "dev\n" + BEGIN + "dev\n" + END + "\n",
            BEGIN + "dev\n" + END + "\n" + BEGIN + "test\n" + END + "\n",
        ):
            self.path.write_text(text, encoding="utf-8")
            before = self.path.read_bytes()
            with self.assertRaises(ValueError):
                self.execute("use", profile="dev")
            self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_domains_ports_addresses_and_profile_names(self):
        for value in (
            "invalid app.test",
            "127.0.0.1:80 app.test",
            "127.0.0.1 *.test",
            "127.0.0.1 -bad.test",
            "127.0.0.1 app.test\x1b[31m",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_content(value)
        for name in ("../outside", "test.hosts", "CON", "nul", "", "a" * 65):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_profile(name)
        entries, invalid = parse_entries(
            "127.0.0.1 good.test\nbroken line\n# explanatory comment\n"
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(invalid, [2])

    def test_utf16_round_trip_and_empty_profile_is_active(self):
        self.path.write_bytes(codecs.BOM_UTF16_LE + "# 原有\r\n".encode("utf-16-le"))
        original = self.path.read_bytes()
        self.profile(content="")
        self.execute("use", profile="dev")
        self.assertTrue(self.path.read_bytes().startswith(original))
        self.assertEqual(self.execute("profiles")["profiles"][0]["status"], "active")
        self.execute("off")
        self.assertEqual(self.path.read_bytes(), original)

    def test_manual_changes_after_backup_block_restore(self):
        self.profile()
        backup = self.execute("use", profile="dev")["backup"]
        self.path.write_bytes(self.path.read_bytes() + b"# external edit\r\n")
        changed = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "refusing"):
            self.execute("restore", backup=backup)
        self.assertEqual(self.path.read_bytes(), changed)
        self.assertFalse(self.execute("backups")["backups"][0]["can_restore"])

    def test_backup_checksum_is_checked(self):
        self.profile()
        backup = self.execute("use", profile="dev")["backup"]
        (self.store.root / "backups" / (backup + ".hosts")).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.execute("restore", backup=backup)

    def test_backup_metadata_cannot_override_snapshot_identity(self):
        self.profile()
        backup = self.execute("use", profile="dev")["backup"]
        metadata = self.store.root / "backups" / (backup + ".json")
        value = json.loads(metadata.read_text(encoding="utf-8"))
        value.update(id="../outside", unrelated="ignored")
        metadata.write_text(json.dumps(value), encoding="utf-8")
        records = self.store.backups()
        self.assertEqual(records[0]["id"], backup)
        self.assertNotIn("unrelated", records[0])
        self.execute("restore", backup=backup)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_edit_profile_stages_and_validates_without_changing_system_hosts(self):
        def editor(command, **kwargs):
            Path(command[-1]).write_text("127.0.0.1 edited.test\n", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        with (
            patch(
                "dev_tools.hosts.editor_command",
                side_effect=lambda settings, path: ["editor", str(path)],
            ),
            patch("dev_tools.hosts.subprocess.run", side_effect=editor),
            patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "0"}),
        ):
            result = self.execute("edit", profile="dev", json=False)
        self.assertFalse(result["applied"])
        self.assertIn("edited.test", self.store.load_profile("dev"))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list((self.store.root / "drafts").iterdir()), [])

    def test_direct_edit_preserves_encoding_and_creates_backup(self):
        def editor(command, **kwargs):
            path = Path(command[-1])
            content = path.read_bytes().decode("utf-8")
            path.write_bytes((content + "127.0.0.1 new.test\r\n").encode())
            return SimpleNamespace(returncode=0)

        with (
            patch(
                "dev_tools.hosts.editor_command",
                side_effect=lambda settings, path: ["editor", str(path)],
            ),
            patch("dev_tools.hosts.subprocess.run", side_effect=editor),
            patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "0"}),
        ):
            result = self.execute("edit")
        self.assertTrue(result["changed"])
        self.assertTrue(self.path.read_bytes().startswith(self.original))
        self.execute("restore")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_invalid_editor_result_leaves_draft_without_touching_profile(self):
        profile = self.profile()
        original = profile.read_bytes()

        def editor(command, **kwargs):
            Path(command[-1]).write_text("not-an-ip bad.test", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        with (
            patch(
                "dev_tools.hosts.editor_command",
                side_effect=lambda settings, path: ["editor", str(path)],
            ),
            patch("dev_tools.hosts.subprocess.run", side_effect=editor),
            patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "0"}),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(ValueError),
        ):
            self.execute("edit", profile="dev")
        self.assertEqual(profile.read_bytes(), original)
        self.assertEqual(len(list((self.store.root / "drafts").iterdir())), 1)

    def test_concurrent_system_edit_is_not_lost(self):
        from dev_tools.hosts import _apply

        after = self.original + b"127.0.0.1 new.test\r\n"
        self.path.write_bytes(self.original + b"# concurrent\r\n")
        with (
            patch("dev_tools.runtimes.platforms.windows_hosts.write_system_hosts") as writer,
            self.assertRaisesRegex(ValueError, "changed"),
        ):
            _apply(self.path, self.store, self.original, after, "use", as_json=True)
        writer.assert_not_called()
        self.assertEqual(self.store.backups(), [])

    def test_profile_modified_after_activation_is_identified(self):
        self.profile()
        self.execute("use", profile="dev")
        self.profile(content="127.0.0.1 different.test\n")
        self.assertEqual(self.execute("profiles")["profiles"][0]["status"], "modified")

    def test_existing_conflicts_and_bom_comments_are_preserved_without_blocking_unrelated_profiles(
        self,
    ):
        self.path.write_text(
            "\n\ufeff# existing header\n192.0.2.1 old.test\n192.0.2.2 old.test\n", encoding="utf-8"
        )
        before = self.path.read_bytes()
        value = self.execute("list")
        self.assertTrue(value["complete"])
        self.assertEqual(value["conflicts"][0]["domain"], "old.test")
        self.profile()
        self.execute("use", profile="dev")
        self.assertTrue(self.path.read_bytes().startswith(before))
        self.execute("off")
        self.assertEqual(self.path.read_bytes(), before)

    def test_edit_validation_preserves_old_diagnostics_but_rejects_new_conflicts(self):
        from dev_tools.hosts_config import validate_edit

        before = "192.0.2.1 old.test\n192.0.2.2 old.test\nbroken old line\n"
        validate_edit(before, before + "127.0.0.1 new.test\n")
        with self.assertRaises(ValueError):
            validate_edit(before, before + "192.0.2.3 old.test\n")
        with self.assertRaises(ValueError):
            validate_edit(before, before + "new broken line\n")

    def test_windows_adapter_handles_permission_cancel_and_dns_warning(self):
        from dev_tools.runtimes.platforms.windows_hosts import write_system_hosts

        after = self.original + b"# fixture\r\n"
        for code in (2, 3, 1):
            with (
                patch(
                    "dev_tools.runtimes.platforms.windows_hosts.subprocess.run",
                    return_value=SimpleNamespace(returncode=code),
                ),
                self.assertRaises(ValueError),
            ):
                write_system_hosts(self.original, after)
        with (
            patch(
                "dev_tools.runtimes.platforms.windows_hosts.subprocess.run",
                return_value=SimpleNamespace(returncode=4),
            ) as helper,
            patch(
                "dev_tools.runtimes.platforms.windows_hosts.system_hosts_path",
                return_value=self.path,
            ),
        ):
            self.path.write_bytes(after)
            self.assertFalse(write_system_hosts(self.original, after))
        command = helper.call_args.args[0]
        self.assertTrue(Path(command[4]).is_file())
        self.assertNotIn("-Target", command)
        self.assertIn(digest(self.original), command)

    def test_environment_flags_help_and_json_edit_rejection(self):
        for args in (
            ["-e", "win", "hosts", "list", "--json"],
            ["hosts", "list", "--json", "-e", "win"],
            ["hosts", "--json", "-e", "win", "use", "dev"],
        ):
            parsed = parser().parse_args(args)
            self.assertEqual(parsed.env, "win")
            self.assertTrue(parsed.json)
            self.assertFalse(hasattr(parsed, "project_command"))
        for language in ("zh", "en"):
            self.settings.data["language"] = language
            with (
                patch("dev_tools.cli.load_settings", return_value=self.settings),
                contextlib.redirect_stdout(out := io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as stopped,
            ):
                main(["hosts", "edit", "--json"])
            self.assertEqual(stopped.exception.code, 2)
            self.assertEqual(json.loads(out.getvalue())["error"]["code"], "invalid_arguments")
        with language_scope("en"):
            self.assertNotRegex(parser().format_help(), r"[\u4e00-\u9fff]")

    @unittest.skipIf(os.name == "nt", "WSL routing")
    def test_wsl_requires_explicit_windows_and_forwards_without_foreign_config(self):
        remote = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "schema_version": 3,
                    "action": "hosts",
                    "operation": "list",
                    "environment": "windows",
                    "entries": [],
                }
            ),
            "",
        )
        with (
            patch("dev_tools.cli.load_settings", return_value=self.settings),
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            ),
            patch("dev_tools.runtimes.router.forward_remote", return_value=remote) as route,
            contextlib.redirect_stdout(out := io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(["--config", "caller.toml", "hosts", "list", "-e", "win", "--json"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(json.loads(out.getvalue())["operation"], "list")
        self.assertEqual(route.call_args.args, ("win", ["hosts", "list", "--json"]))
        with (
            patch("dev_tools.cli.load_settings", return_value=self.settings),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(["hosts", "list", "--json"])
        self.assertEqual(stopped.exception.code, 1)

    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows helper")
    def test_windows_atomic_writer_retains_acl_and_rejects_changed_target(self):
        helper = Path(__file__).resolve().parents[1] / "scripts/dev-tools-hosts.ps1"
        before = self.path.read_bytes()
        after = b"127.0.0.1 fixture.test\r\n"
        content = self.root / "staged.hosts"
        content.write_bytes(after)
        runner = self.root / "check.ps1"
        runner.write_text(
            """param($Helper, $Target, $Content, $Expected)
$ErrorActionPreference="Stop"
. $Helper
Initialize-DevToolsHostsNative
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { [DevToolsHostsNative]::EnableOwnerRestore() }
$seed=Get-Acl -LiteralPath $Target
$seed.SetGroup([Security.Principal.WindowsIdentity]::GetCurrent().User)
Set-Acl -LiteralPath $Target -AclObject $seed
$acl=Get-Acl -LiteralPath $Target
Set-DevToolsHostsContent $Target ([IO.File]::ReadAllBytes($Content)) $Expected
$updated=Get-Acl -LiteralPath $Target
if ($updated.Owner -ne $acl.Owner -or $updated.Group -ne $acl.Group -or $updated.AccessToString -ne $acl.AccessToString -or $updated.AreAccessRulesProtected -ne $acl.AreAccessRulesProtected) { throw "ACL changed: $($acl.Sddl) -> $($updated.Sddl)" }
try { Set-DevToolsHostsContent $Target ([IO.File]::ReadAllBytes($Content)) $Expected; throw "expected conflict" } catch { if ($_.Exception.Message -ne "hosts_changed") { throw } }
""",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(runner),
                str(helper),
                str(self.path),
                str(content),
                digest(before),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.path.read_bytes(), after)
        self.assertEqual(list(self.path.parent.glob(".dev-tools-hosts-*")), [])


if __name__ == "__main__":
    unittest.main()
