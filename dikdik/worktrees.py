"""Git worktree management: create isolated worktrees for tasks.

A worktree is a separate working directory linked to the same Git
repository. This lets each task work in isolation without disturbing
the main workspace. Worktrees are created under the data directory
and cleaned up when the task is deleted.

Git runs through gitops, so repository hooks stay disabled: `git worktree add`
would otherwise run a post-checkout hook on the host. Worktree ids arrive in URL
paths, so they are checked against a strict pattern before any path is built;
an id such as ".." must never resolve to the data directory itself.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .gitops import GitError, _git, is_repo

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")


def _task_id(task_id) -> str:
    if not isinstance(task_id, str) or not ID.fullmatch(task_id):
        raise ValueError("Task id must be 1-64 letters, digits, dashes or underscores")
    return task_id


class WorktreeManager:
    def __init__(self, root: Path, data: Path):
        self.root = root
        self.worktrees_dir = data / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: str, branch: str | None = None) -> Path:
        """Create a worktree for a task. Returns the worktree path."""
        if not is_repo(self.root):
            raise ValueError("Workspace is not a Git repository; worktrees need Git")
        wt_path = self.worktrees_dir / _task_id(task_id)
        if wt_path.exists():
            raise ValueError(f"Worktree for task {task_id} already exists")
        if branch is not None:
            if not isinstance(branch, str) or not BRANCH.fullmatch(branch) or ".." in branch or branch.endswith((".lock", "/")):
                raise ValueError("Branch name must be a safe Git branch name")
            args = ["worktree", "add", "-b", branch, str(wt_path), "HEAD"]
            if _git(self.root, "branch", "--list", branch).strip():
                args = ["worktree", "add", str(wt_path), branch]  # reuse an existing branch
        else:
            args = ["worktree", "add", "--detach", str(wt_path), "HEAD"]
        try:
            _git(self.root, *args)
        except GitError as exc:
            raise ValueError(f"Could not create worktree: {exc}") from None
        return wt_path

    def remove(self, task_id: str) -> None:
        """Remove a task's worktree."""
        try:
            wt_path = self.worktrees_dir / _task_id(task_id)
        except ValueError:
            raise KeyError(task_id) from None
        if not wt_path.is_dir():
            raise KeyError(task_id)
        try:
            _git(self.root, "worktree", "remove", "--force", str(wt_path), check=False)
        except GitError:
            pass
        if wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
        try:
            _git(self.root, "worktree", "prune", check=False)
        except GitError:
            pass

    def list(self) -> list[dict]:
        """List the worktrees Dikdik created for tasks."""
        worktrees = []
        if not is_repo(self.root):
            return worktrees
        try:
            listing = _git(self.root, "worktree", "list", "--porcelain", check=False)
        except GitError:
            return worktrees
        base = self.worktrees_dir.resolve()
        for line in listing.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree ")).resolve()
                if path.parent == base:
                    worktrees.append({"task": path.name, "path": str(path)})
        return worktrees
