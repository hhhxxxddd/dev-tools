from __future__ import annotations

import codecs
import contextlib
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools import __version__
from dev_tools.cli import main
from dev_tools.settings import Settings, defaults
from dev_tools.ssh_config import collect_hosts
from dev_tools.ssh_list import SshList, _replace, run_list
from tests.test_list_editor import fake_terminal


class SshListTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.config = self.home / ".ssh/config"
        self.system = self.root / "system/ssh_config"
        self.store = self.root / "store"
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def write(self, text, path=None):
        path = path or self.config
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def model(self):
        return SshList(self.home, "fixture-user", self.system)

    def host(self, model, alias="demo"):
        return next(host for host in model.value["hosts"] if host["name"] == alias)

    def change(self, model, key, value, alias="demo"):
        host = self.host(model, alias)
        model.put(host, model.local(host) | {key: value})

    def test_include_edits_preserve_bom_comments_options_and_root(self):
        self.write("# root\r\nInclude conf.d/*.conf\r\n")
        included = self.write(
            "\ufeff# keep\r\nHost demo # annotation\r\n\tUser old # keep user note\r\n IdentityFile ~/.ssh/key\r\nHost other\r\n User stable\r\n",
            self.home / ".ssh/conf.d/one.conf",
        )
        before = included.read_bytes()
        root = self.config.read_bytes()
        model = self.model()
        origin = self.host(model)["field_sources"]
        self.assertEqual(origin["user"], {"source": str(included), "line": 3})
        self.assertIsNone(origin["port"])
        self.change(model, "user", "new")
        self.assertEqual(included.read_bytes(), before)
        self.assertNotIn("IdentityFile", model.preview())
        folder = model.save(self.store)
        self.assertEqual(included.read_bytes(), before.replace(b"User old", b"User new"))
        self.assertEqual(self.config.read_bytes(), root)
        self.assertTrue(included.read_bytes().startswith(codecs.BOM_UTF8))
        self.assertEqual((folder / "0.before").read_bytes(), before)
        records = json.loads((folder / "files.json").read_text(encoding="utf-8"))
        self.assertEqual(records[0]["path"], str(included))
        self.assertTrue(records[0]["existed"])

    def test_add_rename_remove_and_inherited_field_clear(self):
        self.write("Host demo\n User custom\n# keep this comment\nHost *\n User inherited\n")
        model = self.model()
        self.change(model, "user", "")
        self.assertEqual(self.host(model)["user"], "inherited")
        self.assertEqual(self.host(model)["field_sources"]["user"]["line"], 4)
        self.change(model, "name", "renamed")
        self.assertEqual(model.value["hosts"][0]["name"], "renamed")
        model.delete(model.value["hosts"][0])
        self.assertIn(b"# keep this comment\nHost *", model.changes[self.config])
        model.put(
            None,
            {
                "name": "added",
                "hostname": "added.test",
                "user": "",
                "port": "2200",
                "proxy_jump": "jump",
            },
        )
        self.assertEqual(self.host(model, "added")["port"], 2200)

    def test_new_config_not_created_until_save_and_private_permissions(self):
        model = self.model()
        self.assertFalse(self.config.parent.exists())
        model.put(
            None,
            {
                "name": "demo",
                "hostname": "demo.test",
                "user": "deploy",
                "port": "",
                "proxy_jump": "",
            },
        )
        self.assertFalse(self.config.exists())
        model.save(self.store)
        self.assertEqual(
            collect_hosts(self.home, "fixture-user", self.system)["hosts"][0]["user"], "deploy"
        )
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)

    def test_noop_and_reverted_edits_do_not_create_backup(self):
        self.write("Host demo\n\tUser old # note\n")
        model = self.model()
        host = self.host(model)
        model.put(host, model.local(host))
        self.assertEqual(model.preview(), "")
        self.assertIsNone(model.save(self.store))
        self.assertFalse(self.store.exists())

    def test_shared_conditional_proxy_include_and_system_blocks_are_read_only(self):
        for text in (
            "Host demo other\n User old\n",
            "Host demo\nMatch all\n User old\n",
            "Host demo\n ProxyCommand never-execute\n",
            "Host demo\n Include missing.conf\n",
            "Host demo\n User a\n User b\n",
        ):
            with self.subTest(text=text):
                # An unmatched literal Include is permitted by OpenSSH inventory.
                self.write(text.replace("missing.conf", "missing*.conf"))
                model = self.model()
                with self.assertRaisesRegex(ValueError, "read-only"):
                    model.local(self.host(model))
                self.assertEqual(model.pending(), {})
        self.config.unlink()
        self.write("Host demo\n User system\n", self.system)
        model = self.model()
        with self.assertRaisesRegex(ValueError, "read-only"):
            model.delete(self.host(model))

    def test_earlier_rules_block_ineffective_changes_and_inherited_origins_are_exact(self):
        self.write("User global\nHost demo\n HostName demo.test\n")
        model = self.model()
        self.assertEqual(self.host(model)["field_sources"]["user"]["line"], 1)
        with self.assertRaisesRegex(ValueError, "earlier"):
            self.change(model, "user", "new")
        self.assertEqual(model.pending(), {})

    def test_deleting_host_with_repeated_declaration_is_rejected(self):
        self.write("Host demo\n User old\nHost demo\n Port 2200\n")
        model = self.model()
        with self.assertRaisesRegex(ValueError, "multiple"):
            model.delete(self.host(model))

    def test_delete_preview_hides_connection_command_bodies(self):
        self.write(
            "Host demo\n User old\n LocalCommand echo local-secret\n"
            " RemoteCommand echo remote-secret\n KnownHostsCommand echo hosts-secret\n"
        )
        model = self.model()
        model.delete(self.host(model))
        preview = model.preview()
        for marker in ("local-secret", "remote-secret", "hosts-secret"):
            self.assertNotIn(marker, preview)
        for directive in ("LocalCommand", "RemoteCommand", "KnownHostsCommand"):
            self.assertIn(directive, preview)

    def test_include_context_prevents_edits_affecting_other_aliases(self):
        self.write("Include conf.d/*.conf\nHost other\n")
        self.write("Host demo\n User shared\n", self.home / ".ssh/conf.d/one.conf")
        model = self.model()
        # The static reader restores Include gates; unchanged rows must survive.
        before = self.host(model, "other")["user"]
        self.change(model, "user", "new")
        self.assertEqual(self.host(model, "other")["user"], before)

    def test_concurrent_root_include_and_glob_changes_prevent_all_writes(self):
        for changed in ("root", "include", "glob"):
            with self.subTest(changed=changed):
                self.write("Include conf.d/*.conf\n")
                include = self.write("Host demo\n User old\n", self.home / ".ssh/conf.d/one.conf")
                extra = self.home / ".ssh/conf.d/extra.conf"
                extra.unlink(missing_ok=True)
                model = self.model()
                self.change(model, "user", "new")
                if changed == "root":
                    self.config.write_bytes(self.config.read_bytes() + b"# external\n")
                elif changed == "include":
                    include.write_bytes(include.read_bytes() + b"# external\n")
                else:
                    self.write("Host extra\n", extra)
                with (
                    patch("dev_tools.ssh_list._replace") as writer,
                    self.assertRaisesRegex(ValueError, "changed"),
                ):
                    model.save(self.store)
                writer.assert_not_called()
                self.assertIn(b"User old", include.read_bytes())

    @unittest.skipIf(os.name == "nt", "native Unix symlink required")
    def test_retargeted_include_link_is_detected_even_when_all_file_contents_match(self):
        self.write("Include conf.d/*.conf\nInclude choice.conf\n")
        first = self.write("Host demo\n User old\n", self.home / ".ssh/conf.d/one.conf")
        second = self.write("Host other\n User stable\n", self.home / ".ssh/conf.d/two.conf")
        choice = self.home / ".ssh/choice.conf"
        choice.symlink_to(first)
        model = self.model()
        self.change(model, "user", "new")
        choice.unlink()
        choice.symlink_to(second)
        with (
            patch("dev_tools.ssh_list._replace") as writer,
            self.assertRaisesRegex(ValueError, "changed"),
        ):
            model.save(self.store)
        writer.assert_not_called()
        self.assertIn(b"User old", first.read_bytes())

    def test_partial_write_failure_rolls_back_and_preserves_backups_and_drafts(self):
        self.write("Include conf.d/*.conf\n")
        first = self.write("Host demo\n User old\n", self.home / ".ssh/conf.d/one.conf")
        second = self.write("Host second\n User old\n", self.home / ".ssh/conf.d/two.conf")
        originals = {path: path.read_bytes() for path in (first, second)}
        model = self.model()
        self.change(model, "user", "new")
        self.change(model, "user", "new", alias="second")

        def replace(path, after, before):
            if path == second:
                raise OSError("denied")
            _replace(path, after, before)

        with (
            patch("dev_tools.ssh_list._replace", side_effect=replace),
            self.assertRaisesRegex(ValueError, "preserved"),
        ):
            model.save(self.store)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.assertEqual(len(list(self.store.glob("backups/*/*.draft"))), 2)

    def test_invalid_input_duplicate_alias_and_incomplete_inventory_never_write(self):
        self.write("Host demo\nHost other\n")
        model = self.model()
        for key, value in (
            ("name", "other"),
            ("name", "wild*"),
            ("port", "0"),
            ("port", "65536"),
            ("user", "bad\nHost injected"),
            ("hostname", 'bad"value'),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.change(model, key, value)
        self.assertEqual(model.pending(), {})
        self.write("Host demo\n Port invalid\n")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.model()

    def test_interactive_edit_uses_selected_cell_and_escape_cancels(self):
        self.write("Host demo\n User old\n")
        terminal, scope = fake_terminal(
            ["RIGHT", "RIGHT", "i", "s"], texts=["new"], confirms=[True]
        )
        try:
            with (
                patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "1"}),
                patch("dev_tools.ssh_list.default_path", return_value=self.store / "config.toml"),
            ):
                self.assertEqual(run_list(self.home, "fixture-user", self.system, "demo"), 0)
        finally:
            scope.stop()
        self.assertIn(b"User new", self.config.read_bytes())
        terminal.text.assert_called_once()


class SshListRoutingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = Settings(self.root / "config.toml", "missing", defaults(), {})
        settings.data["language"] = "en"
        self.enterContext(patch("dev_tools.cli.load_settings", return_value=settings))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def test_list_edit_json_and_nonterminal_fail_before_native_or_remote_action(self):
        for flags, expected in ((["--json"], 2), ([], 1)):
            with (
                patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "0"}),
                patch("sys.stdin.isatty", return_value=False),
                patch("dev_tools.ssh_list.run_list") as action,
                patch("dev_tools.runtimes.router.forward_remote") as route,
                self.assertRaises(SystemExit) as stopped,
            ):
                main(["ssh", "list", "--edit", *flags])
            self.assertEqual(stopped.exception.code, expected)
            action.assert_not_called()
            route.assert_not_called()

    def test_remote_list_edit_keeps_interactive_stdio_query_and_no_timeout(self):
        remote = "wsl" if os.name == "nt" else "win"
        with (
            patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "1"}),
            patch(
                "dev_tools.runtimes.diagnostics.target_status",
                return_value={"state": "installed", "version": __version__},
            ),
            patch("dev_tools.terminal.raw_input", return_value=contextlib.nullcontext()),
            patch(
                "dev_tools.runtimes.router.forward_remote",
                side_effect=[subprocess.CompletedProcess([], 0), 0],
            ) as route,
            self.assertRaises(SystemExit) as stopped,
        ):
            main(["ssh", "list", "demo", "--edit", "-e", remote])
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(route.call_args_list[0].args[1], ["ssh", "list", "--edit", "--help"])
        self.assertEqual(route.call_args.args[1], ["ssh", "list", "--edit", "--", "demo"])
        self.assertFalse(route.call_args.kwargs["capture"])
        self.assertIsNone(route.call_args.kwargs["timeout"])
