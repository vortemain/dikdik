"""Git worktree management: create isolated worktrees for tasks.

A worktree is a separate working directory linked to the same Git
repository. This lets each task work in isolation without disturbing
the main workspace. Worktrees are created under the data directory
and cleaned up when the task is deleted.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=env, timeout=30, check=check, stdin=subprocess.DEVNULL)


def is_repo(root: Path) -> bool:
    try:
        return _git(root, "rev-parse", "--is-inside-work-tree", check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class WorktreeManager:
    def __init__(self, root: Path, data: Path):
        self.root = root
        self.worktrees_dir = data / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: str, branch: str | None = None) -> Path:
        """Create a worktree for a task. Returns the worktree path."""
        if not is_repo(self.root):
            raise ValueError("Workspace is not a Git repository; worktrees need Git")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 64:
            raise ValueError("Task id must be a 1-64 character string")
        wt_path = self.worktrees_dir / task_id
        if wt_path.exists():
            raise ValueError(f"Worktree for task {task_id} already exists")
        # Create a new branch or use the current branch
        if branch:
            if not isinstance(branch, str) or len(branch) > 100 or branch.startswith("-"):
                raise ValueError("Branch name must be a safe string")
            current = _git(self.root, "branch", "--show-current").stdout.strip()
            base = current or "main"
            _git(self.root, "branch", branch, check=False)
            _git(self.root, "worktree", "add", str(wt_path), branch, check=False)
        else:
            _git(self.root, "worktree", "add", str(wt_path), "HEAD", check=False)
        if not wt_path.is_dir():
            raise ValueError("Could not create worktree")
        return wt_path

    def remove(self, task_id: str) -> None:
        """Remove a task's worktree."""
        wt_path = self.worktrees_dir / task_id
        if not wt_path.is_dir():
            raise KeyError(task_id)
        try:
            _git(self.root, "worktree", "remove", "--force", str(wt_path), check=False)
        except (OSError, subprocess.SubprocessError):
            pass
        if wt_path.exists():
            import shutil
            shutil.rmtree(wt_path, ignore_errors=True)

    def list(self) -> list[dict]:
        """List all worktrees."""
        worktrees = []
        if not is_repo(self.root):
            return worktrees
        try:
            result = _git(self.root, "worktree", "list", "--porcelain", check=False)
            if result.returncode != 0:
                return worktrees
            for line in result.stdout.strip().splitlines():
                if line.startswith("worktree "):
                    path = line.removeprefix("worktree ")
                    task_id = Path(path).name if path else ""
                    if self.worktrees_dir in Path(path).parents or Path(path) == self.worktrees_dir:
                        worktrees.append({"task": task_id, "path": path})
        except (OSError, subprocess.SubprocessError):
            pass
        return worktrees