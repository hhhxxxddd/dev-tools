"""Small, read-only and redacted observations, independent of application/framework names."""

from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MAX_TAIL = 8192
ERROR_LINE = re.compile(
    r"(?:^|\s)(?:Caused by:\s*|Unhandled exception\.\s*)?"
    r"(?P<kind>(?:[A-Za-z_][\w.]{0,80})?(?:Error|Exception)|(?i:error)(?:\s+[A-Z]{1,6}\d{2,8}|\[[A-Z]\d{2,8}\])?|(?i:exception)):\s*(?P<text>\S.+)"
)


def safe_target(value):
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https", "tcp"} or not url.hostname:
            return None
        host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        authority = host + (f":{url.port}" if url.port else "")
        result = urlunsplit((url.scheme, authority, url.path, "", ""))
        return result[:256] if not any(ord(c) < 32 for c in result) else None
    except TypeError, ValueError:
        return None


def safe_message(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value[:4096])
    value = "".join(char for char in value if char.isprintable() or char in "\n\t")
    value = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://\S+", "[URL]", value)
    value = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[redacted]", value)
    value = re.sub(
        r"(?i)([\w.-]*(?:password|passwd|secret|token|api[_ -]?key|authorization|cookie|session_id)[\w.-]*[\"']?(?:\s+provided)?\s*[=:]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[redacted]",
        value,
    )
    value = re.sub(
        r"(?i)\b(password|passwd|secret|token|api[_ -]?key|authorization|cookie|session_id|private key)\b.*",
        r"\1 [redacted]",
        value,
    )
    value = re.sub(r"\b[A-Za-z0-9_+/=-]{40,}\b", "[redacted]", value)
    value = " ".join(value.split())
    value = re.split(r"(?<=[.!?。])\s+", value, maxsplit=1)[0]
    return value[:280] + ("…" if len(value) > 280 else "") if value else None


def failure_messages(text):
    """Keep the deepest/latest exception lines as clues, never a guessed root cause."""
    tail = text[-MAX_TAIL:].encode("utf-8", errors="replace")[-MAX_TAIL:]
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", tail.decode("utf-8", errors="replace"))
    values = []
    for line in text[-MAX_TAIL:].splitlines():
        match = ERROR_LINE.search(line)
        if match:
            location = line[: match.start()].strip()
            if not re.search(r"(?:\(\d+,\d+\)|:\d+(?::\d+)?):?$", location):
                location = ""
            value = safe_message(f"{location} {match['kind']}: {match['text']}")
            if value:
                values.append(value)
    return list(dict.fromkeys(reversed(values)))[:2]


def failure_log(path: Path, worker: dict, *, now=None, start=0, end=None):
    """Avoid symlinks/FIFOs and old logs unrelated to the current failure."""
    now = time.time() if now is None else now
    if (
        type(start) is not int
        or start < 0
        or end is not None
        and (type(end) is not int or end < start)
    ):
        return []
    try:
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            return []
        observed = worker.get("updated_at")
        if worker.get("phase") in {"failed", "restarting"} and isinstance(observed, int | float):
            if metadata.st_mtime < observed - 5:
                return []
        elif metadata.st_mtime < now - 30:
            return []
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                return []
            if end is not None and opened.st_size < end:
                return []
            limit = opened.st_size if end is None else end
            offset = max(start, limit - MAX_TAIL)
            stream.seek(offset)
            text = stream.read(max(0, limit - offset)).decode("utf-8", errors="replace")
        return failure_messages(text)
    except OSError, ValueError:
        return []
