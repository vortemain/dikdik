"""Git on the approved workspace: read status and history, and commit what the user applied.

Git runs with fixed arguments (never a shell), a timeout, no pager, no prompts and
repository hooks disabled, so a commit made from Dikdik cannot run repository code.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

TIMEOUT = 15
STATUS_LIMIT = 500
LOG_LIMIT = 50
SAFE = ["-c", "core.pager=cat", "-c", "core.fsmonitor=false", "-c", "core.quotepath=false",
        "-c", "color.ui=false", "-c", "commit.gpgsign=false"]


class GitError(ValueError):
    pass


def _git(root: Path, *args: str, check: bool = True, timeout: float = TIMEOUT) -> str:
    if shutil.which("git") is None:
        raise GitError("Git is not installed on this machine")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    with tempfile.TemporaryDirectory() as no_hooks:
        try:
            done = subprocess.run(["git", "-C", str(root), *SAFE, "-c", f"core.hooksPath={no_hooks}", *args],
                                  capture_output=True, timeout=timeout, env=env, shell=False, stdin=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
    capped = len(commits) >= LOG_LIMIT
    return {"repo": True, "installed": True, "branch": branch, "status": status, "commits": commits,
            "commits_capped": capped, "ahead": int(ahead) if ahead.isdigit() else None}


def diff(root: Path, commit: str) -> dict:
    """Show the diff for a specific commit (or HEAD)."""
    if not is_repo(root):
        raise GitError("This workspace is not a Git repository")
    if not isinstance(commit, str) or not commit.strip() or len(commit) > 80 or commit.startswith("-"):
        raise GitError("Commit hash must be a safe Git reference")
    objtype = _git(root, "cat-file", "-t", commit, check=False).strip()
    if objtype != "commit":
        raise GitError("No such commit")
    parent = _git(root, "rev-parse", "--verify", f"{commit}~1", check=False).strip()
    ref = parent if parent and not parent.startswith("fatal:") else "--root"
    subject = _git(root, "log", "-1", "--format=%s", commit).strip()
    patch = _git(root, "diff", ref, commit, "--no-color", "--stat", "--patch", check=False)
    return {"commit": commit, "subject": subject, "diff": patch[:200000]}


def search(root: Path, query: str, limit: int = 50) -> list[dict]:
    """Search commit messages for a query string (case-insensitive)."""
    if not is_repo(root):
        raise GitError("This workspace is not a Git repository")
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise GitError("Search query must contain 1-200 characters")
    if not isinstance(limit, int) or limit < 1 or limit > 200:
        limit = 50
    log = _git(root, "log", f"-{limit}", "--grep=" + query.strip(), "-i",
               "--format=%H%x1f%h%x1f%an%x1f%at%x1f%s%x1e", check=False)
    results = []
    for record in log.split("\x1e"):
        parts = record.strip().split("\x1f")
        if len(parts) == 5:
            results.append({"hash": parts[0], "short": parts[1], "author": parts[2],
                             "time": int(parts[3]), "subject": parts[4]})
    return results


# --- remotes: fetch, pull, push, branches and pull requests -------------------------
# These reach the network with the computer's own sign-in (Git Credential Manager, SSH
# keys, the `gh` CLI). Dikdik never asks for, stores or passes a token; a sign-in window
# may appear the first time, a hidden terminal prompt never can (GIT_TERMINAL_PROMPT=0).
NETWORK_TIMEOUT = 180
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def _clean_url(url: str) -> str:
    """A remote URL without any user or password it may carry."""
    parts = urlsplit(url)
    if parts.scheme in {"http", "https"} and (parts.username or parts.password):
        return urlunsplit((parts.scheme, parts.hostname + (f":{parts.port}" if parts.port else ""), parts.path, "", ""))
    return url


def _check_branch(name) -> str:
    if not isinstance(name, str) or not BRANCH.fullmatch(name) or ".." in name or name.endswith((".lock", "/", ".")) or "//" in name:
        raise GitError("Branch names use letters, digits, '.', '_', '-' and '/'")
    return name


def remote_info(root: Path) -> dict:
    """Branches, the upstream and how far apart they are, and whether `gh` can open PRs."""
    if not is_repo(root):
        raise GitError("This workspace is not a Git repository")
    branch = _git(root, "branch", "--show-current").strip()
    branches = [b for b in _git(root, "branch", "--format=%(refname:short)").splitlines() if b.strip()][:200]
    remotes = {}
    for line in _git(root, "remote", "-v", check=False).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            remotes.setdefault(parts[0], _clean_url(parts[1]))
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False).strip()
    upstream = upstream if upstream and not upstream.startswith("fatal") and "@{upstream}" not in upstream else None
    behind = ahead = None
    if upstream:
        counts = _git(root, "rev-list", "--left-right", "--count", "@{upstream}...HEAD", check=False).split()
        if len(counts) == 2 and all(c.isdigit() for c in counts):
            behind, ahead = int(counts[0]), int(counts[1])
    gh = shutil.which("gh")
    signed_in = False
    if gh:
        try:
            signed_in = subprocess.run([gh, "auth", "status"], capture_output=True, timeout=15, stdin=subprocess.DEVNULL,
                                       env={**os.environ, "GH_PROMPT_DISABLED": "1"},
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            signed_in = False
    return {"branch": branch or None, "branches": branches, "remotes": remotes, "upstream": upstream,
            "ahead": ahead, "behind": behind, "gh": {"installed": gh is not None, "signed_in": signed_in}}


def fetch(root: Path) -> dict:
    _git(root, "fetch", "--prune", "--no-recurse-submodules", timeout=NETWORK_TIMEOUT)
    return remote_info(root)


def pull(root: Path) -> dict:
    """Fast-forward only: a pull never creates a merge commit or rewrites your work."""
    try:
        _git(root, "pull", "--ff-only", "--no-recurse-submodules", timeout=NETWORK_TIMEOUT)
    except GitError as exc:
        if "Not possible to fast-forward" in str(exc) or "divergent" in str(exc).lower():
            raise GitError("Your branch and its upstream have both changed; merge or rebase them yourself first") from None
        raise
    return remote_info(root)


def push(root: Path, approve: object) -> dict:
    """Each push is its own explicit decision; a new branch gets an upstream on origin."""
    if approve is not True:
        raise GitError("Pushing needs explicit approval for this push")
    info = remote_info(root)
    if not info["branch"]:
        raise GitError("Check out a branch before pushing")
    if not info["remotes"]:
        raise GitError("This repository has no remote to push to")
    args = ["push"] if info["upstream"] else ["push", "--set-upstream", "origin" if "origin" in info["remotes"] else next(iter(info["remotes"])), info["branch"]]
    _git(root, *args, "--no-verify", timeout=NETWORK_TIMEOUT)
    return remote_info(root)


def switch_branch(root: Path, name, create: bool = False) -> dict:
    name = _check_branch(name)
    _git(root, "switch", *(["-c"] if create is True else []), name)
    return remote_info(root)


def create_pr(root: Path, body: dict) -> dict:
    """Open a pull request for the current branch with the `gh` CLI and its own sign-in."""
    if body.get("approve") is not True:
        raise GitError("Opening a pull request needs explicit approval")
    gh = shutil.which("gh")
    if gh is None:
        raise GitError("Install the GitHub CLI (gh) and run `gh auth login` once to open pull requests from Dikdik")
    info = remote_info(root)
    if not info["upstream"]:
        raise GitError("Push this branch first")
    args = [gh, "pr", "create", "--head", info["branch"]]
    title, text = body.get("title"), body.get("body")
    if isinstance(title, str) and title.strip():
        args += ["--title", title.strip()[:256], "--body", (text if isinstance(text, str) else "")[:20000]]
    else:
        args += ["--fill"]
    if body.get("draft") is True:
        args.append("--draft")
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=NETWORK_TIMEOUT, env=env, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise GitError("GitHub did not answer in time") from None
    lines = (done.stdout + done.stderr).strip().splitlines()
    url = next((line.strip() for line in lines if line.strip().startswith("https://")), None)
    if done.returncode and not url:
        raise GitError("gh: " + (lines[-1][:300] if lines else "could not open the pull request"))
    return {"url": url, "existing": done.returncode != 0}


def commit(root: Path, message: str, paths: list[str], identity: tuple[str, str] | None = None) -> dict:
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
        who = ["-c", f"user.name={identity[0]}", "-c", f"user.email={identity[1]}"] if identity and all(identity) else []
        _git(root, *who, "commit", "--no-verify", "-m", message.strip(), "--", *paths)
    except GitError as exc:
        if "Please tell me who you are" in str(exc) or "user.email" in str(exc) or "empty ident" in str(exc):
            raise GitError("Set your Git name and email in Settings → General (or git config --global user.name and user.email)") from None
        raise
    head = _git(root, "log", "-1", "--format=%H%x1f%h%x1f%s").strip().split("\x1f")
    return {"hash": head[0], "short": head[1], "subject": head[2], "files": paths}
