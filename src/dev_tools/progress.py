"""Live, localized progress for foreground actions; workers stay silent."""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, copy_context

from .i18n import t
from .presentation import colored

_reporter = ContextVar("dev_tools_progress", default=None)
HEARTBEAT_SECONDS = 5


class Progress:
    def __init__(self, project, stream):
        self.project = project
        self.stream = stream
        self.stages = []
        self.lock = threading.RLock()
        self.stopped = threading.Event()

    def write(self, text, color="yellow"):
        if self.stopped.is_set():
            return
        try:
            print(
                colored(f"[{self.project}] {text}", color, stream=self.stream),
                file=self.stream,
                flush=True,
            )
        except OSError, ValueError:
            # Progress is advisory: a closed pipe must not abort project startup.
            self.stopped.set()

    def heartbeat(self):
        while not self.stopped.wait(HEARTBEAT_SECONDS):
            with self.lock:
                if self.stages:
                    text, started = self.stages[-1]
                    self.write(
                        t(
                            "{stage} — still working ({seconds:.0f}s)",
                            stage=text,
                            seconds=time.monotonic() - started,
                        )
                    )


@contextmanager
def progress_scope(project: str, *, enabled=True, stream=None):
    if not enabled:
        yield
        return
    reporter = Progress(project, sys.stderr if stream is None else stream)
    token = _reporter.set(reporter)
    thread = threading.Thread(target=copy_context().run, args=(reporter.heartbeat,), daemon=True)
    thread.start()
    try:
        yield
    finally:
        reporter.stopped.set()
        thread.join()
        _reporter.reset(token)


@contextmanager
def stage(source: str, **parameters):
    reporter = _reporter.get()
    if reporter is None:
        yield
        return
    text, started = t(source, **parameters), time.monotonic()
    with reporter.lock:
        reporter.stages.append((text, started))
        reporter.write(text)
    try:
        yield
    except BaseException:
        with reporter.lock:
            reporter.write(t("{stage} — failed", stage=text), "red")
        raise
    else:
        with reporter.lock:
            reporter.write(
                t(
                    "{stage} — done ({seconds:.1f}s)",
                    stage=text,
                    seconds=time.monotonic() - started,
                ),
                "green",
            )
    finally:
        with reporter.lock:
            reporter.stages.pop()
