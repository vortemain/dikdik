"""Desktop self-updater against published GitHub releases.

Checks the newest published release that carries this platform's installer (0.x
preview tags carry GitHub's prerelease flag, which /releases/latest hides),
downloads it in the background with progress and cancel, verifies it against the
release's SHA256SUMS.txt, then hands over to a small detached helper that waits
for this process to exit, installs the update the way the platform expects, and
opens the new version:

- Windows: the Inno Setup installer runs silently (/VERYSILENT) once Dikdik has
  exited, so no file is in use; Dikdik then starts from the same folder.
- macOS: the app inside the disk image is copied with ditto (symlinks and the
  signature intact) next to the running bundle, swapped in, de-quarantined and
  opened. A bundle in a folder this user cannot write is opened for a manual drag.
- Linux: the .deb is installed with `pkexec apt-get install`, which asks for the
  administrator password in a system dialog and resolves dependencies, before
  Dikdik exits and starts again from /opt/dikdik.
- A Python (pip or source) install is never replaced by a desktop installer: the
  check reports the pip command for the new wheel instead.

The next launch reports whether the update took effect (pending.json in the
update folder). Network access is read-only HTTPS to api.github.com and the
release asset host; no repository code runs on the host.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path, PurePath, PurePosixPath
from typing import Callable

from . import __version__

REPO = "vortemain/dikdik"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
TIMEOUT = 20
CHECK_TTL = 15 * 60  # seconds a successful check is reused (GitHub allows 60 anonymous calls an hour)

_lock = threading.Lock()
_cancel = threading.Event()
_state: dict = {"state": "idle"}
_checked: dict = {}


class Cancelled(Exception):
    pass


def current_version() -> str:
    return __version__


def parse_version(value: str) -> tuple[int, ...]:
    """Numeric tuple for ordering; prerelease/build suffixes are ignored."""
    tag = (value or "").strip().lstrip("vV")
    base = tag.split("-", 1)[0].split("+", 1)[0]
    parts: list[int] = []
    for chunk in base.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def install_kind() -> str:
    """How this copy runs: a packaged desktop app ("windows", "macos", "linux") or "python"."""
    if not getattr(sys, "frozen", False):
        return "python"
    return "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"


def platform_asset_name(tag: str) -> str:
    ver = (tag or "").lstrip("vV")
    if sys.platform == "win32":
        return f"Dikdik-{ver}-Windows-x64-Setup.exe"
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
        return f"Dikdik-{ver}-macOS-{arch}.dmg"
    return f"Dikdik-{ver}-Linux-amd64.deb"


def wheel_asset_name(tag: str) -> str:
    return f"dikdik-{(tag or '').lstrip('vV')}-py3-none-any.whl"


def pick_asset(assets: list[dict], tag: str, name: str | None = None) -> dict | None:
    wanted = name or platform_asset_name(tag)
    for asset in assets:
        if asset.get("name") == wanted:
            return asset
    return None


def fetch_releases(timeout: int = TIMEOUT) -> list[dict]:
    req = urllib.request.Request(API, headers={"User-Agent": "dikdik-updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if getattr(resp, "status", 200) != 200:
            raise RuntimeError(f"GitHub returned status {resp.status}")
        releases = json.loads(resp.read().decode("utf-8"))
    if not isinstance(releases, list):
        raise RuntimeError("GitHub returned an unexpected release list")
    return releases


def latest_release(releases: list[dict], asset_name: Callable[[str], str] = platform_asset_name) -> dict:
    """The newest non-draft release that carries this install's file.

    Published means visible and not a draft; a GitHub prerelease flag is fine,
    because this project marks its 0.x preview builds as prereleases. Ordering
    is by version number, then publication time.
    """
    candidates: list[tuple[tuple[int, ...], str, dict]] = []
    for release in releases:
        if release.get("draft"):
            continue
        tag = release.get("tag_name") or ""
        if not tag or not pick_asset(release.get("assets") or [], tag, asset_name(tag)):
            continue
        candidates.append((parse_version(tag), release.get("published_at") or "", release))
    if not candidates:
        raise RuntimeError("No published Dikdik release carries an installer for this platform")
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[-1][2]


def check(force: bool = False) -> dict:
    """The newest release for this install, reused for a few minutes unless forced."""
    with _lock:
        if not force and _checked.get("info") and time.monotonic() - _checked["at"] < CHECK_TTL:
            return dict(_checked["info"])
    kind = install_kind()
    naming = wheel_asset_name if kind == "python" else platform_asset_name
    release = latest_release(fetch_releases(), naming)
    tag = release.get("tag_name") or ""
    assets = release.get("assets") or []
    asset = pick_asset(assets, tag, naming(tag))
    checksums_url = next((a.get("browser_download_url") for a in assets if a.get("name") == "SHA256SUMS.txt"), None)
    available = bool(tag and asset and is_newer(tag, __version__))
    info = {
        "current": __version__,
        "latest": tag,
        "available": available,
        "tag": tag,
        "name": release.get("name") or tag,
        "notes": (release.get("body") or "")[:6000],
        "url": release.get("html_url") or "",
        "published": release.get("published_at") or "",
        "install": kind,
        # A packaged app installs itself; a Python install is upgraded with pip, never by a desktop installer.
        "can_install": available and kind != "python" and bool(checksums_url),
        "pip_command": f'python -m pip install --upgrade "{asset.get("browser_download_url")}"' if available and kind == "python" else "",
        "asset": {"name": asset["name"], "url": asset.get("browser_download_url"), "size": asset.get("size")} if asset else None,
        "checksums_url": checksums_url,
    }
    with _lock:
        _checked.update(at=time.monotonic(), info=info)
    return dict(info)


def update_dir() -> Path:
    """Downloads live here, not in a TemporaryDirectory: the installer must outlive this process."""
    return Path(tempfile.gettempdir()) / "dikdik-update"


def _set(**fields) -> None:
    with _lock:
        _state.update(fields)


def status() -> dict:
    """Progress of the running update, plus how the previous one ended (shown once after a restart)."""
    with _lock:
        current = dict(_state)
    previous = previous_result()
    if previous:
        current["previous"] = previous
    current["install"] = install_kind()
    current["current"] = __version__
    return current


def previous_result() -> dict | None:
    try:
        pending = json.loads((update_dir() / "pending.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    target = str(pending.get("to") or "")
    ok = bool(target) and not is_newer(target, __version__)
    return {"ok": ok, "from": pending.get("from", ""), "to": target, "url": pending.get("url", "")}


def dismiss_previous() -> None:
    try:
        (update_dir() / "pending.json").unlink()
    except OSError:
        pass


def _download(url: str, dest: Path, timeout: int = TIMEOUT, on_progress: Callable[[int, int], None] | None = None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "dikdik-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as out:
            while True:
                if _cancel.is_set():
                    raise Cancelled()
                block = resp.read(65536)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if on_progress is not None:
                    on_progress(done, total)


def _expected_hash(checksums_text: str, asset_name: str) -> str | None:
    for line in checksums_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*").endswith(asset_name) and len(parts[0]) == 64:
            return parts[0].lower()
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _detached(argv: list[str]) -> None:
    """Start a helper that outlives this process and has no console window."""
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        subprocess.Popen(argv, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(argv, start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ps(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sh(value) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def windows_helper(installer: Path, exe: Path, pid: int) -> list[str]:
    """PowerShell that waits for Dikdik to exit, installs silently, then opens the installed Dikdik.
    The Inno Setup flags: no wizard or prompts, never reboot, close anything still holding files."""
    script = (f"Wait-Process -Id {pid} -Timeout 60 -ErrorAction SilentlyContinue; "
              f"Start-Process -FilePath {_ps(installer)} -ArgumentList "
              "'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS','/SP-' -Wait; "
              f"Start-Process -FilePath {_ps(exe)}")
    return ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-Command", script]


def macos_helper(dmg: PurePath, bundle: PurePath, pid: int) -> list[str]:
    """sh that waits for Dikdik to exit, swaps in the new bundle with ditto (symlinks and signature
    intact), clears quarantine and opens it. If anything fails the disk image is opened instead."""
    new = bundle.with_name(bundle.name + ".new")
    script = (f"while kill -0 {pid} 2>/dev/null; do sleep 0.2; done; "
              "m=$(mktemp -d) && "
              f"hdiutil attach -nobrowse -noverify -mountpoint \"$m\" {_sh(dmg)} >/dev/null && "
              f"rm -rf {_sh(new)} && ditto \"$m/Dikdik.app\" {_sh(new)} && "
              f"rm -rf {_sh(bundle)} && mv {_sh(new)} {_sh(bundle)}; ok=$?; "
              "hdiutil detach \"$m\" -quiet >/dev/null 2>&1; "
              f"if [ $ok -eq 0 ]; then xattr -dr com.apple.quarantine {_sh(bundle)} 2>/dev/null; open {_sh(bundle)}; "
              f"else open {_sh(dmg)}; fi")
    return ["/bin/sh", "-c", script]


def relaunch_helper(exe: PurePath | str, pid: int) -> list[str]:
    script = f"while kill -0 {pid} 2>/dev/null; do sleep 0.2; done; exec {_sh(exe)}"
    return ["/bin/sh", "-c", script]


def macos_bundle(executable: str | None = None) -> PurePath:
    """The running Dikdik.app (…/Dikdik.app/Contents/MacOS/Dikdik), wherever the user put it."""
    path = PurePosixPath(executable) if executable else Path(sys.executable).resolve()
    for parent in path.parents:
        if parent.suffix == ".app":
            return parent
    return PurePosixPath("/Applications/Dikdik.app")


def hand_off(installer: Path) -> None:
    """Prepare the platform's install so it completes after this process exits, then open the new Dikdik."""
    pid = os.getpid()
    kind = install_kind()
    if kind == "windows":
        _detached(windows_helper(installer, Path(sys.executable), pid))
    elif kind == "macos":
        bundle = macos_bundle()
        if not os.access(str(bundle.parent), os.W_OK):
            subprocess.Popen(["open", str(installer)])
            raise RuntimeError(f"Dikdik cannot replace itself in {bundle.parent}. The installer is open: "
                               "drag Dikdik to Applications, then reopen it.")
        _detached(macos_helper(installer, bundle, pid))
    elif kind == "linux":
        pkexec, apt = shutil.which("pkexec"), shutil.which("apt-get")
        if not pkexec or not apt:
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.Popen([opener, str(installer)])
            raise RuntimeError("Installing needs pkexec and apt-get; the package was opened in your software installer instead.")
        done = subprocess.run([pkexec, apt, "install", "-y", "--allow-downgrades", str(installer)],
                              capture_output=True, text=True, timeout=900)
        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            raise RuntimeError("The package was not installed" + (f": {detail[-1][:200]}" if detail else
                               " (the administrator password was not given)."))
        _detached(relaunch_helper(sys.executable, pid))
    else:
        raise RuntimeError("This Dikdik runs from Python; upgrade it with pip instead.")


def _exit_soon(delay: float = 1.5) -> None:
    def later() -> None:
        threading.Event().wait(delay)  # let the last status reply reach the window first
        os._exit(0)
    threading.Thread(target=later, daemon=True).start()


def _run(info: dict) -> None:
    asset = info["asset"]
    folder = update_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.iterdir():
            if old.is_file() and old.name not in {asset["name"], "pending.json"}:
                old.unlink(missing_ok=True)
        dest = folder / asset["name"]
        _set(state="downloading", done=0, total=int(asset.get("size") or 0))
        _download(asset["url"], dest, on_progress=lambda done, total: _set(done=done, total=total or _state.get("total", 0)))
        _set(state="verifying")
        checksums = folder / "SHA256SUMS.txt"
        _download(info["checksums_url"], checksums)
        expected = _expected_hash(checksums.read_text(encoding="utf-8"), asset["name"])
        if expected is None or _sha256(dest).lower() != expected:
            dest.unlink(missing_ok=True)
            raise RuntimeError("The download did not match the release checksum, so it was deleted. Try again later.")
        if _cancel.is_set():
            raise Cancelled()
        _set(state="installing")
        (folder / "pending.json").write_text(json.dumps({"from": __version__, "to": info["latest"], "url": info.get("url", "")}),
                                             encoding="utf-8")
        hand_off(dest)
        _set(state="restarting")
        _exit_soon()
    except Cancelled:
        _set(state="idle", cancelled=True)
    except Exception as exc:  # reported to the window; the current version keeps running
        dismiss_previous()
        _set(state="failed", error=str(exc)[:300] or type(exc).__name__)


def start(info: dict | None = None) -> dict:
    """Begin downloading and installing the available update in the background."""
    with _lock:
        if _state.get("state") in {"downloading", "verifying", "installing", "restarting"}:
            return dict(_state)
    info = info or check()
    if not info.get("can_install") or not info.get("asset"):
        raise ValueError("Upgrade with pip: " + info["pip_command"] if info.get("pip_command") else
                         "No installer is available for this platform")
    _cancel.clear()
    with _lock:
        _state.clear()
        _state.update(state="downloading", latest=info["latest"], done=0, total=int(info["asset"].get("size") or 0))
    threading.Thread(target=_run, args=(info,), name="dikdik-update", daemon=True).start()
    return status()


def cancel() -> dict:
    """Stop a download; once installing has begun it finishes."""
    with _lock:
        if _state.get("state") in {"downloading", "verifying"}:
            _cancel.set()
    return status()
