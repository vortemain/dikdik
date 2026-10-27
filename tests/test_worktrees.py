"""Git worktree management: create, list and remove isolated worktrees."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from dikdik.worktrees import WorktreeManager, is_repo


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        # Initialize a Git repo
        subprocess.run(["git", "init"], cwd=self.root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.root, capture_output=True)
        self.data = base / "data"
        self.data.mkdir()
        self.wm = WorktreeManager(self.root, self.data)

    def test_create_and_list(self):
        wt_path = self.wm.create("task-1")
        self.assertTrue(wt_path.is_dir())
        self.assertTrue((wt_path / "app.py").is_file())

        worktrees = self.wm.list()
        self.assertEqual(len(worktrees), 1)
        self.assertEqual(worktrees[0]["task"], "task-1")

    def test_create_with_branch(self):
        wt_path = self.wm.create("task-2", branch="feature-branch")
        self.assertTrue(wt_path.is_dir())
        # Verify the branch exists
        result = subprocess.run(["git", "-C", str(self.root), "branch", "--list"],
                               capture_output=True, text=True)
        self.assertIn("feature-branch", result.stdout)

    def test_remove(self):
        self.wm.create("task-3")
        self.wm.remove("task-3")
        self.assertFalse((self.data / "worktrees" / "task-3").exists())

    def test_create_duplicate_refused(self):
        self.wm.create("task-4")
        with self.assertRaises(ValueError):
            self.wm.create("task-4")

    def test_remove_unknown_keyerror(self):
        with self.assertRaises(KeyError):
            self.wm.remove("nonexistent")

    def test_ids_cannot_leave_the_worktrees_folder(self):
        canary = self.data / "canary"
        canary.write_text("keep", encoding="utf-8")
        for bad in ("..", ".", "../x", "a/b", "", "-x"):
            with self.assertRaises(KeyError):
                self.wm.remove(bad)
            with self.assertRaises(ValueError):
                self.wm.create(bad)
        self.assertTrue(canary.is_file())

    def test_repository_hooks_do_not_run(self):
        hooks = self.root / ".git" / "hooks"
        marker = Path(self.temp.name) / "hook-ran"
        (hooks / "post-checkout").write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n", encoding="utf-8")
        self.wm.create("task-6")
        self.assertFalse(marker.exists())

    def test_not_a_repo_raises(self):
        non_repo = Path(self.temp.name) / "not-a-repo"
        non_repo.mkdir()
        data = Path(self.temp.name) / "data2"
        data.mkdir()
        wm = WorktreeManager(non_repo, data)
        with self.assertRaises(ValueError):
            wm.create("task-5")


if __name__ == "__main__":
    unittest.main()