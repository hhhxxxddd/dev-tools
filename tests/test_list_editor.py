from __future__ import annotations

import codecs
import contextlib
import io
import os
import unittest
from unittest.mock import Mock, patch

from dev_tools.i18n import language_scope
from dev_tools.list_editor import edit_list, field
from dev_tools.terminal import CancelEdit, Terminal, fit, width


def fake_terminal(keys=(), texts=(), confirms=()):
    terminal = Mock(size=(100, 30))
    terminal.key.side_effect = keys
    terminal.text.side_effect = texts
    terminal.confirm.side_effect = confirms
    scope = patch("dev_tools.list_editor.Terminal")
    factory = scope.start()
    factory.return_value.__enter__.return_value = terminal
    return terminal, scope


class ListEditorTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(language_scope("en"))
        self.enterContext(patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "1"}))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.options = {
            "headers": ("Address", "User"),
            "rows": Mock(return_value=[("a", "u"), ("b", "v")]),
            "edit": Mock(),
            "delete": Mock(),
            "preview": Mock(return_value="--- old\n+++ new\n-change\n+change"),
            "save": Mock(),
        }

    def run_editor(self, keys, texts=(), confirms=(), **kwargs):
        terminal, scope = fake_terminal(keys, texts, confirms)
        try:
            result = edit_list(**(self.options | kwargs))
        finally:
            scope.stop()
        return result, terminal

    def test_arrow_and_vim_navigation_edit_delete_toggle_and_confirm_save(self):
        toggle = Mock()
        saved, _ = self.run_editor(
            ["DOWN", "RIGHT", "i", "k", "h", "ENTER", "a", "DELETE", " ", "s", "s"],
            confirms=[False, True],
            toggle=toggle,
        )
        self.assertTrue(saved)
        self.assertEqual(
            [call.args for call in self.options["edit"].call_args_list],
            [(1, 1), (0, 0), (None, None)],
        )
        self.options["delete"].assert_called_once_with(0)
        toggle.assert_called_once_with(0)
        self.options["save"].assert_called_once()

    def test_colon_commands_and_cancelled_field(self):
        self.options["edit"].side_effect = CancelEdit
        saved, terminal = self.run_editor(["i", ":", ":"], texts=["bad", "q"])
        self.assertFalse(saved)
        self.assertTrue(
            any(
                "cancelled" in str(call) or "取消" in str(call)
                for call in terminal.grid.call_args_list
            )
        )
        self.options["save"].assert_not_called()
        self.assertTrue(self.run_editor([":"], texts=["w"], confirms=[True])[0])

    def test_cancel_eof_interrupt_noop_and_failure_do_not_lose_control(self):
        for keys in (["q"], [EOFError()]):
            self.assertFalse(self.run_editor(keys)[0])
        with self.assertRaises(KeyboardInterrupt):
            self.run_editor([KeyboardInterrupt()])
        self.options["preview"].return_value = ""
        self.assertFalse(self.run_editor(["s", "q"])[0])
        self.options["save"].assert_not_called()
        self.options["preview"].return_value = "diff"
        self.options["save"].side_effect = [ValueError("conflict"), None]
        self.assertTrue(self.run_editor(["s", "s"], confirms=[True, True])[0])

    def test_non_terminal_rejected_before_opening_screen(self):
        with (
            patch.dict(os.environ, {"DEV_TOOLS_TERMINAL": "0"}),
            patch("sys.stdin.isatty", return_value=False),
            patch("dev_tools.list_editor.Terminal") as screen,
            self.assertRaisesRegex(ValueError, "terminal"),
        ):
            edit_list(**self.options)
        screen.assert_not_called()

    def test_empty_list_end_then_add_keeps_selection_on_new_row(self):
        values = []
        self.options["rows"].return_value = values
        self.options["edit"].side_effect = lambda index, column: (
            values.append(("a", "u")) if index is None else None
        )
        self.assertFalse(self.run_editor(["END", "a", "i", "q"])[0])
        self.assertEqual(self.options["edit"].call_args.args, (0, 0))

    def test_field_defaults_clear_and_control_character_rejection(self):
        with patch("builtins.input", side_effect=["", "-", "bad\x1b"]):
            self.assertEqual(field("User", "alice"), "alice")
            self.assertEqual(field("User", "alice", clear=True), "")
            with self.assertRaises(ValueError):
                field("User")

    def test_text_editing_cursor_delete_clear_and_cancel(self):
        terminal = Terminal()
        terminal.write = Mock()
        terminal.key = Mock(
            side_effect=["LEFT", "BACKSPACE", "DELETE", "中", "HOME", "A", "END", "ENTER"]
        )
        self.assertEqual(terminal.text("User", "abc"), "Aa中")
        terminal.key = Mock(side_effect=["CLEAR", "ENTER"])
        self.assertEqual(terminal.text("User", "abc"), "")
        terminal.key = Mock(side_effect=["ESC"])
        with self.assertRaises(CancelEdit):
            terminal.text("User")

    def test_escape_sequences_and_controls_are_decoded(self):
        terminal = Terminal()
        terminal._windows_console = False
        terminal._decoder = codecs.getincrementaldecoder("utf-8")()
        for sequence, expected in (
            (["\x1b", "[", "A"], "UP"),
            (["\x1b", "[", "3", "~"], "DELETE"),
            (["\x1b", ""], "ESC"),
            (["\r"], "ENTER"),
            (["\x15"], "CLEAR"),
        ):
            with patch.object(terminal, "_char", side_effect=sequence):
                self.assertEqual(terminal.key(), expected)

    def test_width_and_screen_never_emit_data_control_sequences(self):
        self.assertEqual(width("中a"), 3)
        self.assertEqual(fit("中a", 2), "中")
        terminal = Terminal()
        terminal.write = Mock()
        terminal.screen(["bad\x1b[31m"], "status")
        output = terminal.write.call_args_list[0].args[0]
        self.assertNotIn("bad\x1b", output)
        self.assertIn("bad\\x1b", output)

    def test_grid_moves_status_to_top_and_shortcuts_above_input(self):
        terminal = Terminal()
        terminal.screen = Mock()
        terminal.write = Mock()
        terminal.grid(("Alias",), [("demo",)], 0, 0, "Top status", "source", "one line shortcuts")
        self.assertEqual(terminal.screen.call_args.args[0][0], "Top status")
        self.assertEqual(terminal.screen.call_args.args[1], "")
        output = terminal.write.call_args.args[0]
        self.assertTrue(output.startswith(f"\x1b[{terminal.size[1] - 2};1H"))
        self.assertNotIn("\n", output)
        self.assertIn("one line shortcuts", output)
