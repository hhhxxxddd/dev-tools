from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.i18n import message
from dev_tools.projects import logfiles
from dev_tools.projects.execution import Executor
from dev_tools.projects.models import CommandSpec, ProjectError, TaskSpec
from dev_tools.projects.worker import run_worker
from tests import test_project_engine as engine_fixture


class LogfileTests(unittest.TestCase):
    def test_tail_reads_only_last_block_of_a_large_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            path.write_bytes(b"older-line\n" * 200000 + "first\n最后\nending".encode())
            opened = Path.open
            reads = []

            class Reader:
                def __init__(self, stream):
                    self.stream = stream

                def __getattr__(self, name):
                    return getattr(self.stream, name)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()

                def read(self, length):
                    reads.append(length)
                    return self.stream.read(length)

            def track(candidate, *args, **kwargs):
                return Reader(opened(candidate, *args, **kwargs))

            with patch.object(Path, "open", track):
                self.assertEqual(list(logfiles.read_log(path, lines=2)), ["最后\n", "ending"])
            self.assertLessEqual(sum(reads), logfiles.TAIL_BLOCK_BYTES)

    def test_tail_handles_blank_lines_crlf_and_utf8(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            path.write_bytes("旧行\r\n\r\n中文\r\n最后".encode())
            self.assertEqual(list(logfiles.read_log(path, lines=3)), ["\n", "中文\n", "最后"])
            path.write_bytes(b"")
            self.assertEqual(list(logfiles.read_log(path, lines=20)), [])

    def test_very_long_unterminated_line_has_a_bounded_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            path.write_bytes(b"x" * 400 + b"recent")
            with patch.object(logfiles, "LOG_MAX_BYTES", 64):
                tail = "".join(logfiles.read_log(path, lines=100))
            self.assertEqual(len(tail), 64)
            self.assertTrue(tail.endswith("recent"))

    def test_zero_lines_only_follows_new_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            path.write_bytes(b"previous\n")
            self.assertEqual(list(logfiles.read_log(path, lines=0)), [])
            output = logfiles.read_log(path, lines=0, follow=True)

            def append(_):
                with path.open("ab") as stream:
                    stream.write(b"new\n")

            try:
                with patch.object(logfiles.time, "sleep", side_effect=append):
                    self.assertEqual(next(output), "new\n")
            finally:
                output.close()

    def test_multi_follow_waits_for_new_files_without_blocking_other_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, later = root / "first.log", root / "later.log"
            first.write_text("old\n", encoding="utf-8")
            stream = logfiles.read_logs({"first": first, "later": later}, lines=1, follow=True)
            try:
                self.assertEqual(next(stream), ("first", "old\n"))

                def append(_):
                    with first.open("a", encoding="utf-8") as handle:
                        handle.write("new\n")
                    later.write_text("appeared\n", encoding="utf-8")

                with patch.object(logfiles.time, "sleep", side_effect=append):
                    self.assertEqual(next(stream), ("first", "new\n"))
                    self.assertEqual(next(stream), ("later", "appeared\n"))
            finally:
                stream.close()

    def test_follow_polls_busy_sources_fairly_in_bounded_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = {name: Path(temporary) / (name + ".log") for name in ("a", "b")}
            for path in paths.values():
                path.write_bytes(b"")

            def append(_):
                for name, path in paths.items():
                    with path.open("ab") as handle:
                        handle.write(name.encode() * 12)

            with (
                patch.object(logfiles, "TAIL_BLOCK_BYTES", 4),
                patch.object(logfiles.time, "sleep", side_effect=append) as sleep,
            ):
                stream = logfiles.read_logs(paths, lines=0, follow=True)
                try:
                    self.assertEqual(
                        [next(stream) for _ in range(6)], [("a", "aaaa"), ("b", "bbbb")] * 3
                    )
                    sleep.assert_called_once()
                finally:
                    stream.close()

    def test_rotation_retains_three_bounded_newest_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            with patch.object(logfiles, "LOG_MAX_BYTES", 64):
                for index in range(5):
                    path.write_bytes(bytes([65 + index]) * 300)
                    self.assertTrue(logfiles.rotate_log(path))
                    self.assertFalse(path.exists())
                path.write_bytes(b"small")
                self.assertFalse(logfiles.rotate_log(path))
            self.assertEqual(path.read_bytes(), b"small")
            for number, expected in enumerate((b"E", b"D", b"C"), 1):
                self.assertEqual(
                    path.with_name(f"service.log.{number}").read_bytes(), expected * 64
                )
            self.assertFalse(path.with_name("service.log.4").exists())

    def test_locked_windows_reader_does_not_block_worker_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            path.write_bytes(b"preserve existing content")
            with (
                patch.object(logfiles, "LOG_MAX_BYTES", 16),
                patch.object(Path, "replace", side_effect=PermissionError("reader denies rename")),
            ):
                self.assertFalse(logfiles.rotate_log(path))
            self.assertEqual(path.read_bytes(), b"preserve existing content")

    @unittest.skipIf(os.name == "nt", "native Unix symlink required")
    def test_rotation_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "external.log"
            target.write_bytes(b"preserve")
            path = root / "service.log"
            path.symlink_to(target)
            with self.assertRaises(ProjectError):
                logfiles.rotate_log(path)
            self.assertEqual(target.read_bytes(), b"preserve")

    def test_follow_closes_handles_and_tracks_rotation_and_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            path.write_text("initial\n", encoding="utf-8")
            output = logfiles.read_log(path, lines=1, follow=True)
            try:
                self.assertEqual(next(output), "initial\n")
                with path.open("ab") as stream:
                    stream.write("追加\n".encode())
                self.assertEqual(next(output), "追加\n")
                path.replace(path.with_name("service.log.1"))
                replacement = "replacement file longer than previous offset\n"
                with patch.object(
                    logfiles.time,
                    "sleep",
                    side_effect=lambda _: path.write_text(replacement, encoding="utf-8"),
                ):
                    self.assertEqual(next(output), replacement)
                path.write_text("x\n", encoding="utf-8")
                self.assertEqual(next(output), "x\n")
            finally:
                output.close()

    def test_follow_preserves_utf8_split_between_updates(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            path.write_bytes(b"initial\n")
            output = logfiles.read_log(path, lines=1, follow=True)
            try:
                self.assertEqual(next(output), "initial\n")
                encoded = "中\n".encode()
                with path.open("ab") as stream:
                    stream.write(encoded[:1])

                def complete(_):
                    with path.open("ab") as stream:
                        stream.write(encoded[1:])

                with patch.object(logfiles.time, "sleep", side_effect=complete):
                    self.assertEqual(next(output), "中\n")
            finally:
                output.close()

    def test_follow_seeds_decoder_with_incomplete_initial_utf8(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            encoded = "中\n".encode()
            for split in (1, 2):
                with self.subTest(split=split):
                    path.write_bytes(b"previous\n" + encoded[:split])
                    output = logfiles.read_log(path, lines=1, follow=True)

                    def append(_, remaining=encoded[split:]):
                        with path.open("ab") as stream:
                            stream.write(remaining)

                    try:
                        with patch.object(logfiles.time, "sleep", side_effect=append):
                            self.assertEqual(next(output), "中\n")
                    finally:
                        output.close()

    def test_follow_preserves_crlf_split_at_initial_tail_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service.log"
            path.write_bytes(b"partial\r")
            output = logfiles.read_log(path, lines=1, follow=True)
            try:
                self.assertEqual(next(output), "partial")
                with path.open("ab") as stream:
                    stream.write(b"\nnext\n")
                self.assertEqual(next(output), "\nnext\n")
            finally:
                output.close()

    def test_tasks_and_workers_rotate_closed_logs_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), "windows")
            engine.executor = Executor(engine.spec, engine.binding, engine.backend)
            task = TaskSpec(
                "install", CommandSpec(argv=(sys.executable, "-I", "-c", "print('new')"))
            )
            task_log = engine.binding.state / "logs/tasks/install.log"
            service_log = engine.binding.state / "logs/api.log"
            task_log.parent.mkdir(parents=True)
            task_log.write_bytes(b"old task\n" * 100)
            service_log.write_bytes(b"old service\n" * 100)
            with patch.object(logfiles, "LOG_MAX_BYTES", 64):
                engine.executor.task(task)
                engine.executor = engine_fixture.FakeExecutor(engine.backend)
                engine.executor.resolve = lambda *args, **kwargs: (
                    [sys.executable, "-I", "-c", "pass"],
                    engine.binding.source,
                    os.environ.copy(),
                )
                with patch("dev_tools.projects.worker.signal.signal"):
                    self.assertEqual(run_worker(engine, "api"), 0)
            self.assertEqual(task_log.read_text().strip(), "new")
            for path in (task_log, service_log):
                self.assertEqual(path.with_name(path.name + ".1").stat().st_size, 64)

    def test_worker_records_log_initialization_failure_without_opening_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = engine_fixture.EngineContractTests().fixture(Path(temporary), "windows")
            log_path = engine.binding.state / "logs/api.log"
            failure = message("refusing symbolic-link update: {path}", path=log_path)
            with (
                patch("dev_tools.projects.worker.signal.signal"),
                patch("dev_tools.projects.worker.rotate_log", side_effect=ProjectError(failure)),
            ):
                self.assertEqual(run_worker(engine, "api"), 1)
            self.assertFalse(log_path.exists())
            state = json.loads((engine.binding.state / "workers/api.json").read_text())
            self.assertEqual(state["phase"], "failed")
            self.assertEqual(state["exit_code"], 1)
            self.assertEqual(state["error_detail"], failure.as_dict())


if __name__ == "__main__":
    unittest.main()
