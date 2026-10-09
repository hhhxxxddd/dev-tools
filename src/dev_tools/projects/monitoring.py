"""Shared change classification and Git settling, independent of process supervision."""

from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path
from threading import Event

from ..scanner import IGNORED_DIRECTORIES
from .models import BuildSpec, ProjectError, within


def file_snapshot(root: Path, policy: BuildSpec) -> dict[str, tuple[int, int]]:
    result = {}
    for relative in policy.watch:
        directory = within(root, relative)
        entries = (
            [(str(directory.parent), [], [directory.name])]
            if directory.is_file()
            else os.walk(directory, followlinks=False)
        )
        for current, directories, files in entries:
            directories[:] = sorted(
                name
                for name in directories
                if name not in IGNORED_DIRECTORIES
                and not name.startswith(".cache")
                and not (Path(current) / name).is_symlink()
            )
            for name in sorted(files):
                path = Path(current) / name
                if path.is_symlink():
                    continue
                key = path.relative_to(root).as_posix()
                if path.suffix not in policy.extensions and not any(
                    fnmatch.fnmatch(key, pattern) for pattern in policy.structural
                ):
                    continue
                try:
                    stat = path.stat()
                    result[key] = stat.st_mtime_ns, stat.st_size
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
    def query(*arguments: str) -> str:
        result = executor.run(
            ["git", "-c", f"safe.directory={executor.binding.source}", *arguments],
            cwd=executor.binding.source,
            capture=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    try:
        directory = query("rev-parse", "--absolute-git-dir")
        if not directory:
            return {}
        git = Path(directory)
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
            "head": query("rev-parse", "HEAD"),
            "branch": query("symbolic-ref", "--short", "-q", "HEAD"),
            "busy": busy,
        }
    except OSError:
        return {}


def sync_loop(engine, stop: Event, ready=lambda: None) -> None:
    from ..runtimes.platforms.locking import operation_lock

    ready()
    while not stop.wait(0.75):
        current = git_snapshot(engine.executor)
        state = engine.state()
        previous = state.get("git", {})
        if (
            state.get("recovery")
            or current.get("busy")
            or (engine.spec.rebuild_on_branch and current != previous)
        ):
            continue
        try:
            with operation_lock(engine.binding.state):
                engine.backend.sync(engine.spec)
        except ProjectError as exc:
            if "operation is in progress" not in str(exc):
                print(str(exc), flush=True)


def watch_loop(engine, stop: Event, ready=lambda: None) -> None:
    snapshots = {
        policy.service: file_snapshot(engine.binding.source, policy)
        for policy in engine.spec.builds
    }
    observed_git = git_snapshot(engine.executor)
    observed_files = snapshots
    settled_since = time.monotonic()
    retry_after = 0.0
    ready()
    while not stop.wait(0.5):
        current_git = git_snapshot(engine.executor)
        current_files = {
            policy.service: file_snapshot(engine.binding.source, policy)
            for policy in engine.spec.builds
        }
        if current_git.get("busy"):
            settled_since = time.monotonic()
            continue
        if current_git != observed_git or current_files != observed_files:
            observed_git, observed_files = current_git, current_files
            settled_since = time.monotonic()
            continue
        if time.monotonic() - settled_since < 1.5 or time.monotonic() < retry_after:
            continue
        state = engine.state()
        recovery = state.get("recovery") or {}
        if recovery and recovery.get("operation") != "build":
            continue
        try:
            if (engine.spec.rebuild_on_branch and current_git != state.get("git", {})) or (
                recovery.get("operation") == "build" and recovery.get("service") is None
            ):
                engine.rebuild(kind="branch", lock_timeout=0)
                engine.save(git=current_git)
                snapshots = current_files
            else:
                for policy in engine.spec.builds:
                    kind = change_kind(
                        snapshots[policy.service], current_files[policy.service], policy
                    )
                    if recovery.get("service") == policy.service:
                        kind = recovery.get("kind", kind)
                    if kind:
                        engine.rebuild(service=policy.service, kind=kind, lock_timeout=0)
                        snapshots[policy.service] = current_files[policy.service]
            retry_after = 0.0
        except (ProjectError, OSError) as exc:
            if "operation is in progress" not in str(exc):
                print(f"build pending: {exc}", flush=True)
                retry_after = time.monotonic() + 30
