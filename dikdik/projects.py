"""Projects: open any folder, start a new one, or clone a repository, like Claude Code.

One Dikdik server serves one project at a time and switches in place: the engine
for the old folder closes (its conversations stay in the shared database, tagged
with their folder) and a new engine opens on the chosen one. Everything about the
user (keys, model profiles, MCP servers, skills, settings) lives in the shared data
directory and is the same in every project.

Cloning uses the computer's own Git sign-in (Git Credential Manager, SSH keys); no
token is asked for or stored. Repository hooks never run, and submodules are not
fetched.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .repository import approved_root

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}")
SCP_URL = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._/~-]+")
CLONE_TIMEOUT = 900
FOLDER_LIMIT = 1000
SAFE_GIT = ["-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
            "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always", "-c", "protocol.file.allow=always"]


def program_root() -> Path | None:
    """The folder that holds this Dikdik installation: a source checkout or an
    installed package's parent (site-packages). That code is never a project."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if sys.platform == "darwin" and executable.parent.name == "MacOS":
            return executable.parents[2]  # The whole .app, not only Contents/Frameworks.
        return executable.parent  # Include the launcher above PyInstaller's _internal.
    file = globals().get("__file__", "")
    if not file:
        return None
    try:
        return Path(file).resolve().parents[1]
    except (OSError, ValueError, IndexError):
        return None


def inside_program_folder(path: Path) -> bool:
    """True for Dikdik's own program folder and anything inside it. Ancestors stay
    allowed: a virtual-environment install must not make the user's project unopenable."""
    root = program_root()
    if root is None:
        return False
    try:
        root = Path(root).resolve()
        target = Path(path).resolve()
    except (OSError, ValueError):
        return False
    return target == root or root in target.parents


def check_location(root: Path, data: Path, limit: Path | None = None) -> Path:
    try:
        root = approved_root(str(root))
    except OSError:
        raise ValueError(f"{root} is not a folder this computer can open") from None
    data = Path(data).resolve()
    limit = Path(limit).resolve() if limit is not None else None
    if limit is not None and root != limit and limit not in root.parents:
        raise ValueError(f"On this server, projects live inside {limit}")
    if data == root or root in data.parents or data in root.parents:
        raise ValueError("Dikdik keeps its own data in this folder; choose a project folder that does not contain it")
    if inside_program_folder(root):
        raise ValueError("This folder is Dikdik's own program code; choose one of your project folders")
    return root


def repository_name(url: str) -> str:
    tail = url.rstrip("/").replace(":", "/").rsplit("/", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def check_clone_url(url) -> str:
    """https://, ssh://, git@host:path or file:// only, without embedded credentials."""
    if not isinstance(url, str) or not url.strip() or len(url) > 1000 or url.strip().startswith("-"):
        raise ValueError("Paste a repository URL such as https://github.com/owner/repo")
    url = url.strip()
    if SCP_URL.fullmatch(url):
        return url
    parts = urlsplit(url)
    if parts.scheme not in {"https", "ssh", "file"}:
        raise ValueError("Use an https://, ssh://, git@host:path or file:// repository URL")
    if parts.scheme != "file" and (not parts.hostname or parts.password or (parts.username and parts.scheme == "https")):
        raise ValueError("Leave sign-in details out of the URL; Git uses this computer's own sign-in")
    return url


def pick_folder(title: str = "Choose a project folder") -> str | None:
    """Show this computer's own folder picker (the server runs here). None if cancelled."""
    if sys.platform == "win32":
        script = ("$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
                  "Add-Type -AssemblyName System.Windows.Forms;"
                  "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost=$true; ShowInTaskbar=$false};"
                  "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;"
                  f"$dialog.Description = '{title.replace(chr(39), chr(39) * 2)}'; $dialog.ShowNewFolderButton = $true;"
                  "if ($dialog.ShowDialog($owner) -eq 'OK') { [Console]::Out.Write($dialog.SelectedPath) }")
        command = ["powershell", "-NoProfile", "-STA", "-Command", script]
    elif sys.platform == "darwin":
        prompt = title.replace("\\", "\\\\").replace('"', '\\"')
        command = ["osascript", "-e", f'POSIX path of (choose folder with prompt "{prompt}")']
    elif shutil.which("zenity"):
        command = ["zenity", "--file-selection", "--directory", f"--title={title}"]
    elif shutil.which("kdialog"):
        command = ["kdialog", "--getexistingdirectory", str(Path.home()), "--title", title]
    else:
        raise ValueError("No folder picker is available here; type or paste the folder path instead")
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=600, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("The folder picker could not be shown; type or paste the folder path instead") from None
    cancelled = (sys.platform == "darwin" and "(-128)" in done.stderr) or (sys.platform not in {"win32", "darwin"} and done.returncode == 1)
    if done.returncode and not cancelled:
        raise ValueError("The folder picker could not be shown; use Browse or paste the folder path instead")
    chosen = done.stdout.strip()
    return chosen or None


def list_folders(path: str) -> dict:
    """Local, authenticated folder chooser; works without an interactive desktop."""
    if not isinstance(path, str) or not path.strip() or len(path) > 1000 or "\0" in path:
        raise ValueError("Enter a folder path")
    root = Path(path.strip()).expanduser()
    if not root.is_absolute():
        raise ValueError("Enter a full folder path")
    try:
        root = root.resolve(strict=True)
        folders, truncated = [], False
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        folders.append({"name": entry.name, "path": str(root / entry.name)})
                except OSError:
                    continue
                if len(folders) > FOLDER_LIMIT:
                    truncated = True
                    break
        folders.sort(key=lambda item: item["name"].casefold())
    except (OSError, RuntimeError, ValueError):
        raise ValueError("Cannot open this folder. Check the path and your access, or choose another location.") from None
    locations = [{"name": "Home", "path": str(Path.home())}]
    try:
        drives = os.listdrives() if hasattr(os, "listdrives") else [str(Path(root.anchor))]
    except OSError:
        drives = [root.anchor]
    locations += [{"name": drive, "path": drive} for drive in drives]
    return {"path": str(root), "parent": str(root.parent) if root != root.parent else None,
            "folders": folders[:FOLDER_LIMIT], "locations": locations, "truncated": truncated}


class Projects:
    """Owns the live engine and swaps it when the user changes project."""

    def __init__(self, engine, on_switch=None, limit: Path | None = None):
        self.engine = engine
        self.data = engine.data
        self.on_switch = on_switch
        self.limit = Path(limit).resolve() if limit is not None else None  # server project boundary
        self.lock = threading.Lock()

    def describe(self) -> dict:
        current = str(self.engine.root)
        recent = [entry for entry in self.engine.workspaces.list() if isinstance(entry, dict) and entry.get("path")]
        # Dikdik's own program folder may sit in the registry from before this rule; never offer it.
        recent = [entry for entry in recent if not inside_program_folder(Path(str(entry["path"])))]
        return {"current": {"path": current, "name": Path(current).name},
                "recent": [{"path": e["path"], "name": e.get("name") or Path(e["path"]).name,
                            "exists": Path(e["path"]).is_dir(), "current": e["path"] == current} for e in recent]}

    def open(self, path) -> dict:
        from .engine import Engine  # imported here: engine imports nothing from this module
        if not isinstance(path, str) or not path.strip() or len(path) > 1000:
            raise ValueError("Choose a folder")
        root = check_location(Path(path.strip()).expanduser(), self.data, self.limit)
        with self.lock:
            old = self.engine
            if root == old.root:
                return self.describe()
            with old.lock:
                if old.active:
                    raise ValueError("A task is still running here; stop it or wait before switching projects")
            old.close()
            try:
                new = Engine(root, self.data)
            except Exception:
                self.engine = Engine(old.root, self.data)  # keep serving the old project
                if self.on_switch:
                    self.on_switch(self.engine)
                raise
            self.engine = new
            new.workspaces.add(str(root))
            if self.on_switch:
                self.on_switch(new)
        return self.describe()

    def create(self, body) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Send the new project's name and location")
        name, parent = body.get("name"), body.get("parent")
        if not isinstance(name, str) or not NAME.fullmatch(name.strip()) or name.strip() in {".", ".."}:
            raise ValueError("Name the project with letters, digits, spaces, dots, dashes or underscores")
        base = self._parent(parent)
        target = base / name.strip()
        if target.exists():
            raise ValueError(f"{target} already exists; open it instead")
        target.mkdir()
        if body.get("git") is True:
            self._git(["init", "--quiet", str(target)], base, timeout=60)
        return self.open(str(target))

    def clone(self, body) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Send the repository URL and where to put it")
        url = check_clone_url(body.get("url"))
        base = self._parent(body.get("parent"))
        name = body.get("name") or repository_name(url)
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in {".", ".."}:
            raise ValueError("Choose a folder name for the clone")
        target = base / name
        if target.exists():
            raise ValueError(f"{target} already exists; open it or choose another name")
        try:
            self._git(["clone", "--quiet", "--no-recurse-submodules", "--", url, str(target)], base, timeout=CLONE_TIMEOUT)
        except ValueError:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return self.open(str(target))

    def _parent(self, parent) -> Path:
        if parent in (None, ""):
            base = self.limit or (Path.home() / "Projects" if (Path.home() / "Projects").is_dir() else Path.home())
        elif isinstance(parent, str) and len(parent) <= 1000:
            base = Path(parent.strip()).expanduser()
        else:
            raise ValueError("Choose where to put the project")
        base = base.resolve()
        if not base.is_dir():
            raise ValueError(f"{base} is not a folder")
        if base == self.data or self.data in base.parents:
            raise ValueError("Choose a location outside Dikdik's data folder")
        if inside_program_folder(base):
            raise ValueError("Choose a location outside Dikdik's program folder")
        if self.limit is not None and base != self.limit and self.limit not in base.parents:
            raise ValueError(f"On this server, projects live inside {self.limit}")
        return base

    @staticmethod
    def _git(args: list[str], cwd: Path, timeout: int) -> None:
        if shutil.which("git") is None:
            raise ValueError("Git is not installed on this computer")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # a sign-in window may appear; a hidden prompt may not
        try:
            done = subprocess.run(["git", *SAFE_GIT, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                                  env=env, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ValueError("Git took too long; check the URL and your connection") from None
        except OSError:
            raise ValueError("Git could not be started") from None
        if done.returncode:
            lines = [line for line in done.stderr.strip().splitlines() if line.strip()]
            raise ValueError("Git: " + (lines[-1][:300] if lines else "the command failed"))
