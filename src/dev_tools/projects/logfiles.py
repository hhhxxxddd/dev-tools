"""Bounded startup archives and efficient read-only log tails."""

from __future__ import annotations

import codecs
import io
import os
import re
import time
from itertools import pairwise
from pathlib import Path

from ..i18n import message
from .maintenance import atomic_write
from .models import ProjectError

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3
TAIL_BLOCK_BYTES = 64 * 1024


def failure_codes(path: Path, start: int) -> list[str]:
    """Read only this failed run's bounded tail; never retain arbitrary log text."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(start, stream.tell() - 4096))
            recent = stream.read(4096).decode("utf-8", errors="replace")
        return list(
            dict.fromkeys(
                re.findall(
                    r"\b(?:ERR_[A-Z0-9_]{2,64}|EADDRINUSE|EACCES|ENOENT|ECONNREFUSED|MODULE_NOT_FOUND|[A-Z][A-Za-z]{1,48}Error)\b",
                    recent,
                )
            )
        )[:3]
    except OSError:
        return []


def rotate_log(path: Path) -> bool:
    """Rotate closed logs before a worker/task starts; never truncate a live writer."""
    archives = [path.with_name(f"{path.name}.{number}") for number in range(1, LOG_BACKUPS + 1)]
    for candidate in (path, *archives):
        if candidate.is_symlink():
            raise ProjectError(message("refusing symbolic-link update: {path}", path=candidate))
    if not path.is_file() or path.stat().st_size < LOG_MAX_BYTES:
        return False
    try:
        # A prior uninterrupted run may be much larger than the archive budget.
        # Keep only its newest bytes, without reading the complete file.
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - LOG_MAX_BYTES))
            recent = stream.read(LOG_MAX_BYTES)
        archives[-1].unlink(missing_ok=True)
        for previous, following in reversed(tuple(pairwise(archives))):
            if previous.exists():
                previous.replace(following)
        path.replace(archives[0])
        if size > LOG_MAX_BYTES:
            atomic_write(archives[0], recent)
    except PermissionError:
        # Windows readers can deny rename/delete sharing. Keep the current log
        # intact and try again at the next start, instead of preventing startup.
        return False
    return True


def _tail(path: Path, lines: int):
    with path.open("rb") as stream:
        metadata = os.fstat(stream.fileno())
        identity = metadata.st_dev, metadata.st_ino
        stream.seek(0, os.SEEK_END)
        offset = position = stream.tell()
        chunks = []
        newlines = size = 0
        # The byte limit also bounds very long lines or binary/progress output.
        while lines > 0 and position > 0 and newlines <= lines and size < LOG_MAX_BYTES:
            length = min(position, TAIL_BLOCK_BYTES, LOG_MAX_BYTES - size)
            position -= length
            stream.seek(position)
            chunk = stream.read(length)
            chunks.append(chunk)
            size += len(chunk)
            newlines += chunk.count(b"\n")
    content = b"".join(b"".join(reversed(chunks)).splitlines(keepends=True)[-lines:])
    return identity, offset, content


class _Cursor:
    def __init__(self, path: Path):
        self.path = path
        self.identity, self.offset = None, 0
        self.decoder = io.IncrementalNewlineDecoder(
            codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True
        )

    def tail(self, lines, *, follow=False):
        try:
            self.identity, self.offset, content = _tail(self.path, lines)
            # Keep an incomplete UTF-8 character or CR at EOF in the same
            # decoder used by subsequent polls, rather than replacing it early.
            return self.decoder.decode(content, final=not follow).splitlines(keepends=True)
        except FileNotFoundError:
            return []

    def read(self):
        try:
            # Each poll closes its handle before yielding, including on Windows.
            with self.path.open("rb") as stream:
                metadata = os.fstat(stream.fileno())
                identity = metadata.st_dev, metadata.st_ino
                if identity != self.identity or metadata.st_size < self.offset:
                    self.identity, self.offset = identity, 0
                    self.decoder.reset()
                stream.seek(self.offset)
                content = stream.read(TAIL_BLOCK_BYTES)
                self.offset = stream.tell()
        except FileNotFoundError:
            return ""
        return self.decoder.decode(content) if content else ""


def read_logs(paths: dict[str, Path], *, lines: int, follow: bool = False):
    """Multiplex files fairly without threads; missing logs can appear later."""
    if not paths:
        return
    cursors = {name: _Cursor(path) for name, path in paths.items()}
    for name, cursor in cursors.items():
        for content in cursor.tail(lines, follow=follow):
            yield name, content
    while follow:
        found = False
        for name, cursor in cursors.items():
            content = cursor.read()
            if content:
                found = True
                yield name, content
        if not found:
            time.sleep(0.25)


def read_log(path: Path, *, lines: int, follow: bool = False):
    """Yield a tail, then follow the named file across rotation or truncation."""
    for _, content in read_logs({"log": path}, lines=lines, follow=follow):
        yield content
