"""Modal list navigation shared by hosts and SSH; native adapters own writes."""

from __future__ import annotations

import os
import sys
from contextvars import ContextVar

from .i18n import message, render, t
from .terminal import CancelEdit, Terminal

_terminal = ContextVar("list_terminal", default=None)


def require_terminal() -> None:
    if (
        not (sys.stdin.isatty() and sys.stdout.isatty())
        and os.environ.get("DEV_TOOLS_TERMINAL") != "1"
    ):
        raise ValueError(message("List editing requires an interactive terminal"))


def field(label: str, current: str = "", *, clear: bool = False) -> str:
    terminal = _terminal.get()
    value = (
        terminal.text(t(label), current)
        if terminal
        else input(t("{field} [{value}]: ", field=t(label), value=current))
    ).strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(message("Control characters are not allowed in list fields"))
    return "" if clear and value == "-" else value if terminal else value or current


def edit_list(*, headers, rows, edit, delete, preview, save, toggle=None, describe=None) -> bool:
    require_terminal()
    saved = False
    try:
        with Terminal() as terminal:
            token = _terminal.set(terminal)
            selected, column, status = (
                0,
                0,
                t("Select a field, then press i. Esc cancels field editing; Ctrl+U clears it."),
            )
            shortcuts = t("i/Enter: edit | a: add | d/Delete: delete | s/:w: save | q/:q: exit")
            if toggle:
                shortcuts += t(" | Space: toggle")
            try:
                while True:
                    values = rows()
                    selected = max(0, min(selected, len(values) - 1))
                    detail = describe(selected, column) if describe and values else ""
                    terminal.grid(headers, values, selected, column, status, detail, shortcuts)
                    key = terminal.key()
                    try:
                        if key == ":":
                            key = {"w": "s", "q": "q"}.get(terminal.text(":"), "UNKNOWN")
                        if key in ("q", "ESC"):
                            break
                        if key in ("UP", "k", "DOWN", "j", "HOME", "END", "PAGEUP", "PAGEDOWN"):
                            step = (
                                max(1, terminal.size[1] - 6) if key in ("PAGEUP", "PAGEDOWN") else 1
                            )
                            if key == "HOME":
                                selected = 0
                            elif key == "END":
                                selected = max(0, len(values) - 1)
                            else:
                                delta = -step if key in ("UP", "k", "PAGEUP") else step
                                selected = max(0, min(len(values) - 1, selected + delta))
                        elif key in ("LEFT", "h", "RIGHT", "l", "TAB"):
                            column = (column + (-1 if key in ("LEFT", "h") else 1)) % len(headers)
                        elif key == "a":
                            edit(None, None)
                            status = t("Changes stay in memory until saved.")
                        elif key in ("i", "e", "ENTER") and values:
                            edit(selected, column)
                            status = t("Changes stay in memory until saved.")
                        elif key in ("d", "DELETE") and values:
                            delete(selected)
                            status = t("Changes stay in memory until saved.")
                        elif key == " " and values and toggle:
                            toggle(selected)
                            status = t("Changes stay in memory until saved.")
                        elif key == "s":
                            difference = preview()
                            if not difference:
                                status = t("No changes to save.")
                            elif terminal.confirm(difference):
                                save()
                                saved = True
                                break
                    except CancelEdit:
                        status = t("Field edit cancelled.")
                    except (OSError, ValueError) as exc:
                        status = render(exc)
            finally:
                _terminal.reset(token)
    except EOFError:
        pass
    print(t("List changes saved.") if saved else t("List editing closed; pending edits discarded."))
    return saved
