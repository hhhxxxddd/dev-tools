from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dev_tools.projects.build_freshness import git_source_state
from dev_tools.projects.monitoring import git_snapshot


@unittest.skipUnless(shutil.which("git"), "Git required")
class GitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.git("init", "--initial-branch=main", str(self.source), cwd=self.root)
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))

    def git(self, *arguments, cwd=None):
        return subprocess.run(
            ["git", "-c", "init.templateDir=", *arguments],
            cwd=cwd or self.source,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=10,
        ).stdout.strip()

    def snapshot(self, source=None):
        def run(argv, *, cwd, capture, check, **kwargs):
            return subprocess.run(
                argv, cwd=cwd, capture_output=capture, text=True, check=check, **kwargs
            )

        executor = SimpleNamespace(
            binding=SimpleNamespace(source=source or self.source), run=Mock(side_effect=run)
        )
        value = git_snapshot(executor)
        self.assertEqual(executor.run.call_count, 1)
        return value

    def commit(self):
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        )
        return self.git("rev-parse", "HEAD")

    def test_unborn_repository_retains_branch_and_busy_state(self):
        self.assertEqual(self.snapshot(), {"head": "", "branch": "main", "busy": False})
        (self.source / ".git/index.lock").touch()
        self.assertTrue(self.snapshot()["busy"])

    def test_packed_refs_detached_head_and_linked_worktrees(self):
        head = self.commit()
        self.git("pack-refs", "--all")
        self.assertEqual(self.snapshot(), {"head": head, "branch": "main", "busy": False})
        linked = self.root / "linked worktree"
        self.git("worktree", "add", "-b", "feature", str(linked))
        self.assertEqual(self.snapshot(linked), {"head": head, "branch": "feature", "busy": False})
        gitdir = Path(self.git("rev-parse", "--absolute-git-dir", cwd=linked))
        (gitdir / "MERGE_HEAD").write_text(head)
        self.assertTrue(self.snapshot(linked)["busy"])
        self.git("checkout", "--detach", head)
        self.assertEqual(self.snapshot(), {"head": head, "branch": "", "busy": False})

    def test_read_only_build_state_supports_packed_refs_and_worktrees(self):
        head = self.commit()
        self.git("pack-refs", "--all")
        linked = self.root / "linked worktree"
        self.git("worktree", "add", "-b", "feature", str(linked))
        with patch("subprocess.run") as execute:
            self.assertEqual(
                git_source_state(self.source), {"head": head, "branch": "main", "busy": False}
            )
            self.assertEqual(
                git_source_state(linked), {"head": head, "branch": "feature", "busy": False}
            )
        execute.assert_not_called()
