"""Select native collectors without importing Unix-only modules on Windows."""

import os


def native():
    if os.name == "nt":
        from . import windows

        return windows
    from . import wsl

    return wsl
