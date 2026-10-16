"""Shared change classification and Git settling, independent of process supervision."""

from __future__ import annotations

import fnmatch
import os
import stat
import time
from pathlib import Path
from threading import Event

from ..i18n import t
from ..inspection import IGNORED_DIRECTORIES
from .config import load_project
from .models import BuildSpec, ProjectError, within
from .planning import fingerprint
from .registry import read_json, write_json

SOURCE_IDLE_INTERVAL = 3.0
OBSERVATION_MAX_AGE = SOURCE_IDLE_INTERVAL * 2


class IdlePoll:
    """Bounded polling still discovers new files and Windows-originated edits."""

    def __init__(self, minimum: float, maximum: float):
        self.minimum, self.maximum = minimum, maximum
        self.delay = minimum

    def update(self, active: bool) -> None:
        self.delay = self.minimum if active else min(self.maximum, self.delay * 2)


def source_snapshot(engine, *, shared=False, since=0.0) -> tuple[dict, str]:
    if shared and engine.binding.source != engine.binding.workspace:
        try:
            record = read_json(engine.binding.state / "source-observation.json")
            observed = record.get("observed_at")
            git = record.get("git")
            if (
                record.get("schema") == 1
                and record.get("intent") == engine.intent().get("token")
                and type(observed) in {int, float}
                and observed >= since
                and 0 <= time.monotonic() - observed <= OBSERVATION_MAX_AGE
                and isinstance(git, dict)
                and all(isinstance(git.get(key, ""), str) for key in ("head", "branch"))
                and type(git.get("busy", False)) is bool
                and isinstance(record.get("revision"), str)
            ):
                return git, record["revision"]
        except ValueError, OSError:
            pass
    return git_snapshot(engine.executor), fingerprint(engine.binding, spec=engine.spec)


def _resume_snapshots(engine, current: dict) -> dict:
    """Consume an optional native handover without losing edits during monitor replacement."""
    path = engine.binding.state / "watch-handover.json"
    try:
        record = read_json(path)
        path.unlink(missing_ok=True)
        snapshots = record.get("snapshots")
        created = record.get("created_at")
        if (
            record.get("schema") == 1
            and record.get("intent") == engine.intent().get("token")
            and record.get("revision") == engine.state().get("prepared_revision")
            and type(created) in {int, float}
            and 0 <= time.monotonic() - created <= 120
            and isinstance(snapshots, dict)
            and snapshots.keys() == current.keys()
            and all(
                isinstance(files, dict)
                and all(
                    isinstance(name, str)
                    and isinstance(value, list)
                    and len(value) == 2
                    and all(type(part) is int for part in value)
                    for name, value in files.items()
                )
                for files in snapshots.values()
            )
        ):
            return {
                service: {name: tuple(value) for name, value in files.items()}
                for service, files in snapshots.items()
            }
    except OSError, ValueError:
        pass
    return current


def file_snapshot(root: Path, policy: BuildSpec) -> dict[str, tuple[int, int]]:
    root = root.resolve()
    result = {}

    def record(path: Path, entry: os.DirEntry[str] | None = None) -> None:
        key = path.relative_to(root).as_posix()
        if path.suffix not in policy.extensions and not any(
            fnmatch.fnmatch(key, pattern) for pattern in policy.structural
        ):
            return
        try:
            metadata = (entry or path).stat(follow_symlinks=False)
            if stat.S_ISREG(metadata.st_mode):
                result[key] = metadata.st_mtime_ns, metadata.st_size
        except FileNotFoundError:
            pass

    for relative in policy.watch:
        directory = within(root, relative)
        if directory.is_file():
            record(directory)
            continue
        pending = [directory]
        while pending:
            current = pending.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if (
                                entry.name not in IGNORED_DIRECTORIES
                                and not entry.name.startswith(".cache")
                                and not os.path.isjunction(entry.path)
                            ):
                                pending.append(Path(entry.path))
                        else:
                            record(Path(entry.path), entry)
            except FileNotFoundError:
                continue
    return result


def change_kind(before: dict, after: dict, policy: BuildSpec) -> str | None:
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    if not changed:
        return None
    if any(
        name not in before
        or name not in after
        or any(fnmatch.fnmatch(name, pattern) for pattern in policy.structural)
        for name in changed
    ):
        return "structural"
    if any(Path(name).suffix in policy.resources or "/resources/" in name for name in changed):
        return "resource"
    return "source"


def git_snapshot(executor) -> dict[str, str | bool]:
    try:
        result = executor.run(
            [
                "git",
                "-c",
                f"safe.directory={executor.binding.source}",
                "rev-parse",
                "--absolute-git-dir",
                "--verify",
                "HEAD",
            ],
            cwd=executor.binding.source,
            capture=True,
            check=False,
            timeout=10,
        )
        lines = result.stdout.splitlines()
        # An unborn repository still prints its Git directory before --verify fails.
        if not lines or not Path(lines[0]).is_absolute():
            return {}
        git = Path(lines[0])
        reference = (git / "HEAD").read_text(encoding="utf-8").strip()
        busy = any(
            (git / item).exists()
            for item in (
                "index.lock",
                "HEAD.lock",
                "MERGE_HEAD",
                "CHERRY_PICK_HEAD",
                "rebase-merge",
                "rebase-apply",
            )
        )
        return {
            "head": lines[1] if result.returncode == 0 and len(lines) == 2 else "",
            "branch": reference.removeprefix("ref: refs/heads/")
            if reference.startswith("ref: refs/heads/")
            else "",
            "busy": busy,
        }
    except ProjectError, OSError, UnicodeError:
        return {}


def sync_loop(engine, stop: Event, ready=lambda: None) -> None:
    from ..runtimes.platforms.locking import operation_lock

    poll = IdlePoll(0.75, SOURCE_IDLE_INTERVAL)
    previous_observation = None
    loaded_revision = None
    ready()
    while not stop.wait(poll.delay):
        intent = engine.intent()
        if intent and not intent.get("running"):
            return
        try:
            current, revision = source_snapshot(engine)
            write_json(
                engine.binding.state / "source-observation.json",
                {
                    "schema": 1,
                    "intent": intent.get("token"),
                    "observed_at": time.monotonic(),
                    "git": current,
                    "revision": revision,
                },
            )
            state = engine.state()
            observation = current, revision
            changed = observation != previous_observation
            previous_observation = observation
            if (
                state.get("recovery")
                or current.get("busy")
                or (engine.spec.rebuild_on_branch and current != state.get("git", {}))
                or state.get("prepared_revision") != revision
            ):
                poll.update(changed)
                continue
            with operation_lock(engine.binding.state):
                latest_intent = engine.intent()
                if latest_intent != intent:
                    if latest_intent and not latest_intent.get("running"):
                        return
                    poll.update(True)
                    continue
                state = engine.state()
                if state.get("recovery") or state.get("prepared_revision") != revision:
                    poll.update(True)
                    continue
                if loaded_revision != revision:
                    engine._set_spec(
                        load_project(engine.binding.source, engine.binding.environment)
                    )
                    loaded_revision = revision
                transferred = engine.backend.sync(engine.spec)
                # Unknown results from external backends keep fast polling.
                poll.update(changed or transferred is not False)
        except (ValueError, OSError) as exc:
            poll.update(False)
            if "operation is in progress" not in str(exc):
                print(str(exc), flush=True)


def watch_loop(engine, stop: Event, ready=lambda: None) -> None:
    watch_root = engine.binding.workspace
    snapshots = _resume_snapshots(
        engine,
        {policy.service: file_snapshot(watch_root, policy) for policy in engine.spec.builds},
    )
    observed_git, observed_revision = source_snapshot(engine, shared=True)
    observed_files = snapshots
    settled_since = time.monotonic()
    retry_after = 0.0
    fresh_after = 0.0
    poll = IdlePoll(0.5, 1.5)
    ready()
    while not stop.wait(poll.delay):
        intent = engine.intent()
        if intent and not intent.get("running"):
            return
        try:
            current_git, current_revision = source_snapshot(engine, shared=True, since=fresh_after)
            current_files = {
                policy.service: file_snapshot(watch_root, policy) for policy in engine.spec.builds
            }
        except (ValueError, OSError) as exc:
            poll.update(False)
            if time.monotonic() >= retry_after:
                print(t("自动维护待处理：{error}", error=str(exc)), flush=True)
                retry_after = time.monotonic() + 30
            continue
        if current_git.get("busy"):
            poll.update(True)
            settled_since = time.monotonic()
            continue
        if (
            current_git != observed_git
            or current_files != observed_files
            or current_revision != observed_revision
        ):
            poll.update(True)
            observed_git, observed_files = current_git, current_files
            observed_revision = current_revision
            settled_since = time.monotonic()
            retry_after = 0.0
            continue
        state = engine.state()
        recovery = state.get("recovery") or {}
        branch_changed = engine.spec.rebuild_on_branch and current_git != state.get("git", {})
        pending = (
            bool(recovery)
            or branch_changed
            or current_revision != state.get("prepared_revision")
            or current_files != snapshots
        )
        poll.update(pending)
        if time.monotonic() - settled_since < 1.5 or time.monotonic() < retry_after:
            continue
        try:
            if pending and engine.binding.source != engine.binding.workspace:
                # A checkout or dependency edit may begin after the sync worker
                # published its cache. Revalidate before acting on native edits.
                fresh_git, fresh_revision = source_snapshot(engine)
                if fresh_git != current_git or fresh_revision != current_revision:
                    observed_git, observed_revision = fresh_git, fresh_revision
                    fresh_after = settled_since = time.monotonic()
                    poll.update(True)
                    continue
            if (
                current_revision != state.get("prepared_revision")
                or recovery.get("operation") in {"prepare", "start"}
                or branch_changed
            ):
                engine.maintain(structural=bool(branch_changed))
                engine.save(git=current_git)
                snapshots = {
                    policy.service: file_snapshot(watch_root, policy)
                    for policy in engine.spec.builds
                }
                observed_files = snapshots
                observed_revision = fingerprint(engine.binding, spec=engine.spec)
                fresh_after = time.monotonic()
                settled_since = time.monotonic()
            elif recovery.get("operation") == "build" and recovery.get("service") is None:
                engine.rebuild(kind="branch", lock_timeout=0)
                engine.save(git=current_git)
                snapshots = {
                    policy.service: file_snapshot(watch_root, policy)
                    for policy in engine.spec.builds
                }
                fresh_after = time.monotonic()
            else:
                for policy in engine.spec.builds:
                    kind = change_kind(
                        snapshots.get(policy.service, {}), current_files[policy.service], policy
                    )
                    if recovery.get("service") == policy.service:
                        kind = recovery.get("kind", kind)
                    if kind:
                        engine.rebuild(service=policy.service, kind=kind, lock_timeout=0)
                        snapshots[policy.service] = file_snapshot(watch_root, policy)
                        fresh_after = time.monotonic()
            retry_after = 0.0
        except (ValueError, OSError) as exc:
            if "operation is in progress" not in str(exc):
                print(t("自动维护待处理：{error}", error=str(exc)), flush=True)
                retry_after = time.monotonic() + 30
