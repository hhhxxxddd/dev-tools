from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path

from ...i18n import message
from ...projects.models import ProjectError


@contextmanager
def operation_lock(state: Path, *, timeout: float = 0):
    state.mkdir(parents=True, exist_ok=True)
    with (state / "operation.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                remaining = deadline - time.monotonic()
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or remaining <= 0:
                    raise ProjectError(message("another project operation is in progress")) from exc
                time.sleep(min(0.05, remaining))
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
