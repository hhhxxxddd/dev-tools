"""Record the source inputs actually used by a successful project build."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from ..i18n import message
from .models import ProjectError


def git_source_state(source: Path) -> dict:
    """Read the source checkout's HEAD without invoking Git or project code."""
    source = source.resolve()
    dot_git = next(
        (parent / ".git" for parent in (source, *source.parents) if (parent / ".git").exists()),
        None,
    )
    if dot_git is None:
        return {}
    try:
        if dot_git.is_dir():
            gitdir = dot_git
        else:
            value = dot_git.read_text(encoding="utf-8").strip()
            if not value.startswith("gitdir: "):
                raise ValueError("invalid gitdir")
            gitdir = Path(value[8:])
            if not gitdir.is_absolute():
                gitdir = dot_git.parent / gitdir
            gitdir = gitdir.resolve()
        common_file = gitdir / "commondir"
        common = (
            (gitdir / common_file.read_text(encoding="utf-8").strip()).resolve()
            if common_file.is_file()
            else gitdir
        )
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        busy = any(
            (gitdir / name).exists()
            for name in (
                "index.lock",
                "HEAD.lock",
                "MERGE_HEAD",
                "CHERRY_PICK_HEAD",
                "rebase-merge",
                "rebase-apply",
            )
        )
        branch = ""
        if head.startswith("ref: "):
            ref = head[5:]
            parts = PurePosixPath(ref).parts
            if not ref.startswith("refs/") or ".." in parts or "\\" in ref:
                raise ValueError("invalid git ref")
            branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ""
            head = ""
            for directory in (gitdir, common):
                candidate = directory.joinpath(*parts)
                if candidate.is_file():
                    head = candidate.read_text(encoding="utf-8").strip()
                    break
            packed = common / "packed-refs"
            if not head and packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        head = line.split(" ", 1)[0]
                        break
            # Unborn refs are valid. Unsupported reftable storage is not: do
            # not silently certify a branch whose HEAD we cannot resolve.
            if not head and (common / "reftable" / "tables.list").exists():
                raise ValueError("unsupported reftable refs")
        if head and not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head):
            raise ValueError("invalid git HEAD")
        return {"head": head, "branch": branch, "busy": busy}
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProjectError(message("cannot read project Git state; retry")) from exc


def build_inputs(spec, binding, executor) -> dict[str, dict]:
    """Hash declared watched files and, when requested, the Git revision.

    This inspects files only; it never runs project code or changes state.
    """
    if not spec.builds:
        return {}
    from .monitoring import file_snapshot

    git = git_source_state(binding.source) if spec.rebuild_on_branch else {}
    if git.get("busy"):
        raise ProjectError(message("Git operation in progress; retry after it finishes"))
    result = {}
    for policy in spec.builds:
        files = file_snapshot(binding.source, policy)
        file_names = hashlib.sha256()
        groups = {name: hashlib.sha256() for name in ("source", "resource", "structural")}

        def read_file(name, files=files):
            # Names come only from file_snapshot's validated, non-link walk.
            # Resolving every ancestor again is prohibitively slow on DrvFS.
            path = binding.source / name
            if os.name == "nt" and os.lstat(path).st_file_attributes & 0x400:
                raise ProjectError(message("project sources changed during build; retry"))
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            digest = hashlib.sha256()
            with os.fdopen(os.open(path, flags), "rb") as source:
                before = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(before.st_mode)
                    or (
                        before.st_mtime_ns,
                        before.st_size,
                    )
                    != files[name]
                ):
                    raise ProjectError(message("project sources changed during build; retry"))
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                after = os.fstat(source.fileno())
                if (after.st_mtime_ns, after.st_size) != files[name]:
                    raise ProjectError(message("project sources changed during build; retry"))
            return name, digest.digest()

        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for name, content_hash in pool.map(read_file, sorted(files)):
                    path = binding.source / name
                    file_names.update(name.encode("utf-8") + b"\0")
                    group = (
                        "structural"
                        if any(fnmatch(name, pattern) for pattern in policy.structural)
                        else "resource"
                        if path.suffix in policy.resources or "/resources/" in name
                        else "source"
                    )
                    groups[group].update(name.encode("utf-8") + b"\0" + content_hash)
        except FileNotFoundError as exc:
            raise ProjectError(message("project sources changed during build; retry")) from exc
        result[policy.service] = {
            "policy": hashlib.sha256(
                json.dumps(asdict(policy), sort_keys=True).encode()
            ).hexdigest(),
            "git": git,
            "file_names": file_names.hexdigest(),
            **{name: digest.hexdigest() for name, digest in groups.items()},
        }
    return result


def stale_builds(spec, inputs: dict[str, dict], state: dict) -> tuple[str, ...]:
    recorded = state.get("built_inputs")
    recorded = recorded if isinstance(recorded, dict) else {}
    return tuple(
        policy.service
        for policy in spec.builds
        if recorded.get(policy.service) != inputs[policy.service]
    )


def partial_build_covers(previous, current, kind: str) -> bool:
    """Do not mark a partial build fresh if it skipped another class of changes."""
    if not isinstance(previous, dict):
        return False
    if any(
        previous.get(key) != current[key] for key in ("policy", "git", "file_names", "structural")
    ):
        return False
    other = "resource" if kind == "source" else "source"
    return previous.get(other) == current[other]
