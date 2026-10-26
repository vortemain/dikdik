"""Git on the approved workspace: read status and history, and commit what the user applied.

Git runs with fixed arguments (never a shell), a timeout, no pager, no prompts and
repository hooks disabled, so a commit made from Dikdik cannot run repository code.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

TIMEOUT = 15
STATUS_LIMIT = 500
LOG_LIMIT = 30
SAFE = ["-c", "core.pager=cat", "-c", "core.fsmonitor=false", "-c", "core.quotepath=false",
        "-c", "color.ui=false", "-c", "commit.gpgsign=false"]


class GitError(ValueError):
    pass


def _git(root: Path, *args: str, check: bool = True) -> str:
    if shutil.which("git") is None:
        raise GitError("Git is not installed on this machine")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    with tempfile.TemporaryDirectory() as no_hooks:
        try:
            done = subprocess.run(["git", "-C", str(root), *SAFE, "-c", f"core.hooksPath={no_hooks}", *args],
                                  capture_output=True, timeout=TIMEOUT, env=env, shell=False, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise GitError("Git did not respond in time") from None
        except OSError:
            raise GitError("Git could not be started") from None
    if check and done.returncode:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(detail[-1][:300] if detail else f"git {args[0]} failed")
    return done.stdout.decode("utf-8", "replace")


def is_repo(root: Path) -> bool:
    try:
        return _git(root, "rev-parse", "--is-inside-work-tree", check=False).strip() == "true"
    except GitError:
        return False


STATUS_NAMES = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "U": "conflicted", "?": "untracked", "T": "type changed"}


def info(root: Path) -> dict:
    if not is_repo(root):
        return {"repo": False, "installed": shutil.which("git") is not None}
    branch = _git(root, "branch", "--show-current").strip() or "(detached)"
    status = []
    entries = iter(_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal").split("\0"))
    for line in entries:
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if code[0] in "RC":
            next(entries, None)  # -z puts a rename's original path in its own entry
        letter = (code[0] if code[0] not in " ?" else code[1]) if code != "??" else "?"
        status.append({"path": path, "code": code, "status": STATUS_NAMES.get(letter, "changed"), "staged": code[0] not in " ?"})
        if len(status) >= STATUS_LIMIT:
            break
    commits = []
    log = _git(root, "log", f"-{LOG_LIMIT}", "--format=%H%x1f%h%x1f%an%x1f%at%x1f%s%x1e", check=False)
    for record in log.split("\x1e"):
        parts = record.strip().split("\x1f")
        if len(parts) == 5:
            commits.append({"hash": parts[0], "short": parts[1], "author": parts[2], "time": int(parts[3]), "subject": parts[4]})
    ahead = _git(root, "rev-list", "--count", "@{upstream}..HEAD", check=False).strip()
    return {"repo": True, "installed": True, "branch": branch, "status": status, "commits": commits,
            "ahead": int(ahead) if ahead.isdigit() else None}


def commit(root: Path, message: str, paths: list[str]) -> dict:
    if not isinstance(message, str) or not message.strip() or len(message) > 2000:
        raise GitError("Commit message must contain 1–2000 characters")
    if not isinstance(paths, list) or not paths or len(paths) > 500 or not all(isinstance(p, str) and p and not p.startswith("-") for p in paths):
        raise GitError("Choose 1–500 files to commit")
    if not is_repo(root):
        raise GitError("This workspace is not a Git repository")
    for path in paths:
        target = (root / path).resolve()
        if root.resolve() not in target.parents:
            raise GitError(f"Path is outside the workspace: {path}")
    _git(root, "add", "--", *paths)
    try:
        _git(root, "commit", "--no-verify", "-m", message.strip(), "--", *paths)
    except GitError as exc:
        if "Please tell me who you are" in str(exc) or "user.email" in str(exc) or "empty ident" in str(exc):
            raise GitError("Set your Git name and email first: git config --global user.name \"Your Name\" and user.email") from None
        raise
    head = _git(root, "log", "-1", "--format=%H%x1f%h%x1f%s").strip().split("\x1f")
    return {"hash": head[0], "short": head[1], "subject": head[2], "files": paths}
