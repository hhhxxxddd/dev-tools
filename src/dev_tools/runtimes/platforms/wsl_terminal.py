"""WSL terminal mode, restored even when an editor or transport is interrupted."""

from __future__ import annotations

import sys
import termios
import tty
from contextlib import contextmanager


@contextmanager
def raw_input():
    descriptor = sys.stdin.fileno()
    original = termios.tcgetattr(descriptor) if sys.stdin.isatty() else None
    try:
        if original is not None:
            tty.setraw(descriptor)
        yield
    finally:
        if original is not None:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, original)
