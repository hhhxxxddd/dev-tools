"""Bounded startup archives and efficient read-only log tails."""

from __future__ import annotations

import codecs
import io
import os
import time
from itertools import pairwise
from pathlib import Path

from ..i18n import message
from .maintenance import atomic_write
from .models import ProjectError

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3
TAIL_BLOCK_BYTES = 64 * 1024


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


def read_log(path: Path, *, lines: int, follow: bool = False):
    """Yield a tail, then follow the named file across rotation or truncation."""
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
    for line in b"".join(reversed(chunks)).splitlines(keepends=True)[-lines:]:
        yield line.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    decoder = io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True
    )
    while follow:
        try:
            # Close readers before yielding/waiting so Windows can rotate logs.
            with path.open("rb") as stream:
                metadata = os.fstat(stream.fileno())
                current_identity = metadata.st_dev, metadata.st_ino
                if current_identity != identity or metadata.st_size < offset:
                    identity, offset = current_identity, 0
                    decoder.reset()
                stream.seek(offset)
                content = stream.read(TAIL_BLOCK_BYTES)
                offset = stream.tell()
        except FileNotFoundError:
            content = b""
        if content:
            text = decoder.decode(content)
            if text:
                yield text
        else:
            time.sleep(0.25)
