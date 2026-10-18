from __future__ import annotations

import argparse
import codecs
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.cli import main
from dev_tools.hosts import display, execute
from dev_tools.hosts_config import Document, HostsList, HostsStore
from tests.test_list_editor import fake_terminal


class HostsListTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "hosts"
        self.before = codecs.BOM_UTF16_LE + (
            "# keep\r\n127.0.0.1   localhost # keep formatting\r\n# BEGIN dev-tools hosts: dev\r\n# 192.0.2.1 app.test # old\r\n# END dev-tools hosts\r\n"
        ).encode("utf-16-le")
        self.path.write_bytes(self.before)
        self.store = HostsStore(self.root / "store")
        self.enterContext(
            patch("dev_tools.hosts.native_paths", return_value=(self.path, self.store))
        )
        self.enterContext(patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "1"}))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def invoke(self, keys, texts=(), query="", writer=None):
        def write(before, after):
            self.assertEqual(self.path.read_bytes(), before)
            self.path.write_bytes(after)
            return True

        args = argparse.Namespace(hosts_action="list", list_edit=True, query=query, json=False)
        terminal, scope = fake_terminal(keys, texts, confirms=[True] * 5)
        try:
            with patch(
                "dev_tools.runtimes.platforms.windows_hosts.write_system_hosts",
                side_effect=writer or write,
            ) as called:
                result = execute(args)
        finally:
            scope.stop()
        return result, called, terminal

    def test_filtered_toggle_keeps_unrelated_bytes_and_can_restore(self):
        result, writer, _ = self.invoke([" ", "s"], query="app.test")
        self.assertTrue(result["changed"])
        writer.assert_called_once()
        after = Document.decode(self.path.read_bytes())
        self.assertEqual(after.bom, codecs.BOM_UTF16_LE)
        self.assertIn("127.0.0.1   localhost # keep formatting\r\n", after.text)
        self.assertIn("\r\n192.0.2.1 app.test # old\r\n", after.text)
        self.assertIn("# END dev-tools hosts\r\n", after.text)
        self.assertEqual(self.store.restore(self.path.read_bytes()), self.before)

    def test_add_edit_delete_and_validation_before_confirmation(self):
        self.invoke(
            ["a", "END", "RIGHT", "i", "RIGHT", "i", "UP", "DELETE", "s"],
            texts=["192.0.2.2", "new.test alias.test", "added", "other.test", "-"],
        )
        text = Document.decode(self.path.read_bytes()).text
        self.assertIn("192.0.2.2 other.test\r\n", text)
        self.assertNotIn("app.test", text)
        self.assertIn("# BEGIN dev-tools hosts: dev\r\n# END dev-tools hosts", text)

    def test_noop_cancel_and_invalid_entry_do_not_write(self):
        for keys, texts in (
            (["i", "s", "q"], ["127.0.0.1"]),
            (["a", "q"], ["bad", "new.test", ""]),
            ([" ", "q"], []),
            ([EOFError()], []),
        ):
            result, writer, _ = self.invoke(keys, texts)
            self.assertFalse(result["changed"])
            writer.assert_not_called()
            self.assertEqual(self.path.read_bytes(), self.before)
        self.assertFalse(self.store.root.exists())

    def test_conflicts_can_be_repaired_and_never_reach_confirmation(self):
        _, writer, terminal = self.invoke(
            ["a", "s", "END", "DELETE", "q"], texts=["192.0.2.2", "localhost", ""]
        )
        writer.assert_not_called()
        terminal.confirm.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_failed_write_preserves_encoded_draft(self):
        result, _, _ = self.invoke([" ", "s", "q"], writer=ValueError("denied"))
        self.assertFalse(result["changed"])
        drafts = list((self.store.root / "drafts").glob("*.hosts"))
        self.assertEqual(len(drafts), 1)
        self.assertTrue(drafts[0].read_bytes().startswith(codecs.BOM_UTF16_LE))
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_saved_list_reports_backup_and_failed_dns_refresh_after_leaving_screen(self):
        def write(before, after):
            self.path.write_bytes(after)
            return False

        result, _, _ = self.invoke([" ", "s"], writer=write)
        with contextlib.redirect_stdout(output := io.StringIO()):
            display(result)
        self.assertIn(result["backup"], output.getvalue())
        self.assertIn("ipconfig /flushdns", output.getvalue())

    def test_interrupted_save_preserves_encoded_draft(self):
        with self.assertRaises(KeyboardInterrupt):
            self.invoke([" ", "s"], writer=KeyboardInterrupt())
        drafts = list((self.store.root / "drafts").glob("*.hosts"))
        self.assertEqual(len(drafts), 1)
        self.assertTrue(drafts[0].read_bytes().startswith(codecs.BOM_UTF16_LE))
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_json_combination_rejected_before_action(self):
        with patch("dev_tools.hosts.execute") as action, self.assertRaises(SystemExit) as stopped:
            main(["hosts", "--json", "list", "--edit"])
        self.assertEqual(stopped.exception.code, 2)
        action.assert_not_called()

    def test_model_rejects_injection_and_keeps_unterminated_line(self):
        model = HostsList("127.0.0.1 old.test")
        entry = {"address": "127.0.0.1", "names": ["new.test"], "comment": "", "enabled": True}
        model.put(entry, 1)
        self.assertEqual(model.text, "127.0.0.1 new.test")
        for names in (["new.test", "#injected"], ["bad\nname"]):
            with self.assertRaises(ValueError):
                model.put(entry | {"names": names})
