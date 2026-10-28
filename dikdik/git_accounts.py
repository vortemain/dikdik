"""Git accounts: see and set up this computer's own sign-ins, no tokens in Dikdik.

GitHub through the `gh` CLI and GitLab through `glab`: their status, a browser
sign-in that the CLI runs itself (it prints a one-time code; Dikdik shows it and the
page to open), `gh auth setup-git` so plain Git pushes and pulls use that sign-in,
and the account's repositories for one-click cloning. Tokens stay in the CLI's own
store (the system keyring); Dikdik never reads, stores or shows them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time

TIMEOUT = 20
CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
URL = re.compile(r"https://[\w.-]+/login/device\S*|https://[\w./-]+/oauth/authorize\S*")
TOKEN_LIKE = re.compile(r"\b(gh[opusr]_[A-Za-z0-9_]{10,}|glpat-[\w-]{10,})")


def _env() -> dict:
    return {**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0", "NO_COLOR": "1", "GH_NO_UPDATE_NOTIFIER": "1"}


def _run(args: list[str], timeout: float = TIMEOUT) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=_env(), stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_config(key: str) -> str:
    done = _run(["git", "config", "--global", "--get", key]) if shutil.which("git") else None
    return done.stdout.strip() if done and done.returncode == 0 else ""


def _account(cli: str, host: str) -> dict:
    path = shutil.which(cli)
    if path is None:
        return {"installed": False, "signed_in": False}
    done = _run([path, "auth", "status", "--hostname", host])
    text = TOKEN_LIKE.sub("[token]", ((done.stdout if done else "") + (done.stderr if done else "")))
    match = re.search(r"account (\S+)", text) or re.search(r"[Ll]ogged in to \S+ as (\S+)", text)
    protocol = re.search(r"Git operations protocol: (\w+)", text)
    return {"installed": True, "signed_in": bool(done and done.returncode == 0), "account": match.group(1) if match else None,
            "protocol": protocol.group(1) if protocol else None}


def status() -> dict:
    return {"git": {"installed": shutil.which("git") is not None, "name": _git_config("user.name"), "email": _git_config("user.email"),
                    "credential_helper": _git_config("credential.helper")},
            "github": {**_account("gh", "github.com"), "cli": "gh", "install": "https://cli.github.com"},
            "gitlab": {**_account("glab", "gitlab.com"), "cli": "glab", "install": "https://gitlab.com/gitlab-org/cli"}}


class SignIns:
    """At most one browser sign-in at a time per provider; the CLI owns the whole flow."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}

    def start(self, provider: str) -> dict:
        commands = {"github": ["gh", "auth", "login", "--web", "--hostname", "github.com", "--git-protocol", "https"],
                    "gitlab": ["glab", "auth", "login", "--hostname", "gitlab.com", "--web"]}
        if provider not in commands:
            raise ValueError("Choose GitHub or GitLab")
        path = shutil.which(commands[provider][0])
        if path is None:
            raise ValueError(f"Install the {'GitHub' if provider == 'github' else 'GitLab'} CLI first ({status()[provider]['install']})")
        with self.lock:
            job = self.jobs.get(provider)
            if job and job["process"].poll() is None:
                return self._public(job)
            process = subprocess.Popen([path, *commands[provider][1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, env=_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            job = {"provider": provider, "process": process, "code": None, "url": None, "started": time.time(), "output": ""}
            self.jobs[provider] = job
        threading.Thread(target=self._watch, args=(job,), daemon=True).start()
        try:
            process.stdin.write("\n")  # "Press Enter to open the browser": the CLI opens it itself
            process.stdin.flush()
        except OSError:
            pass
        deadline = time.time() + 10
        while time.time() < deadline and job["code"] is None and process.poll() is None:
            time.sleep(0.1)
        return self._public(job)

    def _watch(self, job):
        for line in job["process"].stdout:
            job["output"] = (job["output"] + TOKEN_LIKE.sub("[token]", line))[-2000:]
            job["code"] = job["code"] or (CODE.search(line).group(1) if CODE.search(line) else None)
            job["url"] = job["url"] or (URL.search(line).group(0) if URL.search(line) else None)
        job["process"].wait()
        if job["process"].returncode == 0 and job["provider"] == "github" and shutil.which("gh"):
            _run([shutil.which("gh"), "auth", "setup-git", "--hostname", "github.com"])  # Git itself now uses this sign-in

    def poll(self, provider: str) -> dict:
        job = self.jobs.get(provider)
        if job is None:
            raise KeyError(provider)
        return self._public(job)

    @staticmethod
    def _public(job) -> dict:
        running = job["process"].poll() is None
        state = "waiting" if running else ("done" if job["process"].returncode == 0 else "failed")
        return {"provider": job["provider"], "state": state, "code": job["code"],
                "url": job["url"] or ("https://github.com/login/device" if job["provider"] == "github" else None),
                "error": None if state != "failed" else (job["output"].strip().splitlines() or ["The sign-in did not finish"])[-1][:300]}

    def setup_git(self) -> dict:
        path = shutil.which("gh")
        if path is None:
            raise ValueError("Install the GitHub CLI first (https://cli.github.com)")
        done = _run([path, "auth", "setup-git", "--hostname", "github.com"])
        if not done or done.returncode:
            raise ValueError("gh could not set up Git; sign in to GitHub first")
        return status()


def repositories(provider: str, query: str = "") -> list[dict]:
    """The signed-in account's repositories (and its organisations'), newest activity first."""
    if provider != "github":
        raise ValueError("Repository lists are available for GitHub")
    path = shutil.which("gh")
    if path is None:
        raise ValueError("Install the GitHub CLI and sign in to list your repositories")
    done = _run([path, "repo", "list", "--limit", "200", "--json", "nameWithOwner,description,isPrivate,updatedAt,url"], timeout=40)
    if not done or done.returncode:
        raise ValueError("gh could not list repositories; sign in to GitHub first")
    try:
        rows = json.loads(done.stdout or "[]")
    except ValueError:
        rows = []
    needle = (query or "").strip().lower()
    repos = [{"name": r.get("nameWithOwner", ""), "description": (r.get("description") or "")[:200], "private": bool(r.get("isPrivate")),
              "updated": r.get("updatedAt"), "url": f"https://github.com/{r.get('nameWithOwner', '')}.git",
              "ssh_url": f"git@github.com:{r.get('nameWithOwner', '')}.git"}
             for r in rows if isinstance(r, dict) and r.get("nameWithOwner")]
    if needle:
        repos = [r for r in repos if needle in r["name"].lower() or needle in r["description"].lower()]
    return sorted(repos, key=lambda r: r["updated"] or "", reverse=True)
