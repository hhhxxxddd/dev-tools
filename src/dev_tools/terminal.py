"""Native terminal keys and a bounded screen; no third-party dependencies."""

from __future__ import annotations

import codecs
import os
import select
import shutil
import sys
import time
import unicodedata
from contextlib import contextmanager

from .i18n import t


def clean(value: object) -> str:
    return "".join(
        f"\\x{ord(c):02x}" if ord(c) < 32 or 127 <= ord(c) < 160 else c for c in str(value)
    )


def width(value: str) -> int:
    return sum(
        0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1
        for c in value
    )


def fit(value: str, size: int) -> str:
    result, used = [], 0
    for char in clean(value):
        step = width(char)
        if used + step > size:
            break
        result.append(char)
        used += step
    return "".join(result) + " " * (size - used)


@contextmanager
def raw_input():
    """Also used on the caller while a target controller owns the terminal."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetStdHandle.argtypes = [wintypes.DWORD]
        api.GetStdHandle.restype = wintypes.HANDLE
        api.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        modes = []
        try:
            for number, clear, add in ((-10, 7, 0), (-11, 0, 4)):
                handle = api.GetStdHandle(number & 0xFFFFFFFF)
                mode = wintypes.DWORD()
                if api.GetConsoleMode(handle, ctypes.byref(mode)):
                    modes.append((handle, mode.value))
                    if not api.SetConsoleMode(handle, mode.value & ~clear | add):
                        raise ctypes.WinError(ctypes.get_last_error())
            yield
        finally:
            for handle, mode in reversed(modes):
                api.SetConsoleMode(handle, mode)
    else:
        from .runtimes.platforms.wsl_terminal import raw_input as native_raw_input

        with native_raw_input():
            yield


class CancelEdit(Exception):
    pass


class Terminal:
    def __enter__(self):
        self._raw = raw_input()
        self._raw.__enter__()
        self._windows_console = os.name == "nt" and sys.stdin.isatty()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            self.write("\x1b[?1049h\x1b[?25l")
        except OSError:
            self._raw.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        finally:
            self._raw.__exit__(*exc)

    @property
    def size(self):
        size = shutil.get_terminal_size((100, 30))
        return max(2, size.columns), max(3, size.lines)

    def write(self, value):
        sys.stdout.write(value)
        sys.stdout.flush()

    def _char(self, *, wait=True):
        if self._windows_console:
            import msvcrt

            if not wait:
                deadline = time.monotonic() + 0.06
                while not msvcrt.kbhit():
                    if time.monotonic() >= deadline:
                        return ""
                    time.sleep(0.005)
            return msvcrt.getwch()
        descriptor = sys.stdin.fileno()
        if not wait:
            if os.name == "nt":
                import ctypes
                import msvcrt
                from ctypes import wintypes

                api = ctypes.WinDLL("kernel32", use_last_error=True)
                api.PeekNamedPipe.argtypes = [
                    wintypes.HANDLE,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                    wintypes.LPVOID,
                    ctypes.POINTER(wintypes.DWORD),
                    wintypes.LPVOID,
                ]
                deadline = time.monotonic() + 0.06
                while True:
                    available = wintypes.DWORD()
                    if not api.PeekNamedPipe(
                        msvcrt.get_osfhandle(descriptor),
                        None,
                        0,
                        None,
                        ctypes.byref(available),
                        None,
                    ):
                        return ""
                    if available.value:
                        break
                    if time.monotonic() >= deadline:
                        return ""
                    time.sleep(0.005)
            elif not select.select([descriptor], [], [], 0.06)[0]:
                return ""
        while True:
            data = os.read(descriptor, 1)
            if not data:
                return ""
            value = self._decoder.decode(data)
            if value:
                return value

    def key(self):
        char = getattr(self, "_pending", "") or self._char()
        self._pending = ""
        if not char or char == "\x04":
            raise EOFError
        if char == "\x03":
            raise KeyboardInterrupt
        if char in ("\x00", "\xe0") and self._windows_console:
            return {
                "H": "UP",
                "P": "DOWN",
                "K": "LEFT",
                "M": "RIGHT",
                "S": "DELETE",
                "G": "HOME",
                "O": "END",
                "I": "PAGEUP",
                "Q": "PAGEDOWN",
            }.get(self._char(), "UNKNOWN")
        if char == "\x1b":
            next_char = self._char(wait=False)
            if next_char not in ("[", "O"):
                self._pending = next_char
                return "ESC"
            sequence = ""
            for _ in range(12):
                part = self._char(wait=False)
                sequence += part
                if not part or part.isalpha() or part == "~":
                    break
            return {
                "A": "UP",
                "B": "DOWN",
                "C": "RIGHT",
                "D": "LEFT",
                "H": "HOME",
                "F": "END",
                "1~": "HOME",
                "4~": "END",
                "3~": "DELETE",
                "5~": "PAGEUP",
                "6~": "PAGEDOWN",
            }.get(sequence, "UNKNOWN")
        return {
            "\r": "ENTER",
            "\n": "ENTER",
            "\x7f": "BACKSPACE",
            "\b": "BACKSPACE",
            "\t": "TAB",
            "\x15": "CLEAR",
        }.get(char, char)

    def screen(self, lines, footer):
        columns, height = self.size
        body = [fit(str(line), columns - 1) for line in lines[: height - 2]]
        self.write("\x1b[H\x1b[2J" + "\r\n".join(body))
        self.write(f"\x1b[{height - 1};1H" + fit(footer, columns - 1))

    def grid(self, headers, rows, selected, column, status, detail="", shortcuts=""):
        columns, height = self.size
        if columns < 40 or height < 10:
            self.screen([t("Terminal too small; resize or press q to exit.")], status)
            return
        cell = max(2, (columns - 4) // len(headers) - 1)
        visible = max(1, height - 7)
        start = max(0, selected - visible + 1)
        lines = [
            status,
            "  " + " ".join(fit(t(h), cell) for h in headers),
        ]
        for index, row in enumerate(rows[start : start + visible], start):
            values = [fit(str(item), cell) for item in row]
            if index == selected:
                values[column] = "[" + fit(str(row[column]), max(0, cell - 2)) + "]"
            lines.append(("> " if index == selected else "  ") + " ".join(values))
        if not rows:
            lines.append(t("Empty list; press a to add an entry."))
        lines.append(t("Row {row}/{total}", row=selected + 1 if rows else 0, total=len(rows)))
        lines.append(detail)
        self.screen(lines, "")
        self.write(f"\x1b[{height - 2};1H" + fit(shortcuts, columns - 1))

    def text(self, label, current=""):
        value, cursor = list(current), len(current)
        while True:
            columns, height = self.size
            prefix = fit(clean(label), min(width(clean(label)), max(0, columns // 3))) + ": "
            available = max(1, columns - width(prefix) - 2)
            start = cursor
            while start > 0 and width("".join(value[start - 1 : cursor])) < available:
                start -= 1
            shown = "".join(value[start:])
            self.write(f"\x1b[{height - 1};1H\x1b[2K" + fit(prefix + shown, columns - 1))
            self.write(
                f"\x1b[{height - 1};{min(columns, width(prefix) + width(''.join(value[start:cursor])) + 1)}H\x1b[?25h"
            )
            try:
                key = self.key()
            finally:
                self.write("\x1b[?25l")
            if key == "ESC":
                raise CancelEdit
            if key == "ENTER":
                return "".join(value)
            if key in ("LEFT", "RIGHT", "HOME", "END"):
                cursor = {
                    "LEFT": max(0, cursor - 1),
                    "RIGHT": min(len(value), cursor + 1),
                    "HOME": 0,
                    "END": len(value),
                }[key]
            elif key == "BACKSPACE" and cursor:
                del value[cursor - 1]
                cursor -= 1
            elif key == "DELETE" and cursor < len(value):
                del value[cursor]
            elif key == "CLEAR":
                value, cursor = [], 0
            elif len(key) == 1 and key.isprintable() and len(value) < 4096:
                value.insert(cursor, key)
                cursor += 1

    def confirm(self, difference):
        lines, offset = difference.splitlines(), 0
        while True:
            height = self.size[1] - 2
            self.screen(
                lines[offset : offset + height],
                t("Save changes? y: save | n/Esc: return | arrows/PgUp/PgDn: scroll"),
            )
            key = self.key()
            if key == "y":
                return True
            if key in ("n", "q", "ESC"):
                return False
            if key in ("UP", "k", "PAGEUP", "DOWN", "j", "PAGEDOWN"):
                step = height if key in ("PAGEUP", "PAGEDOWN") else 1
                offset = max(
                    0,
                    min(
                        max(0, len(lines) - height),
                        offset + (-step if key in ("UP", "k", "PAGEUP") else step),
                    ),
                )
