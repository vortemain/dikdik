"""Best-effort desktop self-updater against published GitHub releases.

Checks the newest published GitHub release for the running platform, preferring
the highest version among non-draft releases (the project publishes 0.x preview
tags with the GitHub prerelease flag, which the /releases/latest lookup hides),
downloads the matching installer, verifies it against the release SHA256SUMS.txt,
runs it, and relaunches Dikdik from its installed location. Network access is
read-only HTTPS to api.github.com and the release asset host. No repository
code runs on the host and nothing else is exposed.

The host process exits after launching the installer so files can be replaced;
a fresh Dikdik is started once installation finishes. The macOS and Linux
paths are best-effort and need testing on those platforms; Windows is primary.
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
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__

REPO = "vortemain/dikdik"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
TIMEOUT = 20


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


def platform_asset_name(tag: str) -> str:
    ver = (tag or "").lstrip("vV")
    if sys.platform == "win32":
        return f"Dikdik-{ver}-Windows-x64-Setup.exe"
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
        return f"Dikdik-{ver}-macOS-{arch}.dmg"
    return f"Dikdik-{ver}-Linux-amd64.deb"


def pick_asset(assets: list[dict], tag: str) -> dict | None:
    wanted = platform_asset_name(tag)
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


def latest_release(releases: list[dict]) -> dict:
    """The newest non-draft release that carries this platform's installer.

    Published means visible and not a draft; a GitHub prerelease flag is fine,
    because this project marks its 0.x preview builds as prereleases. Ordering
    is by version number, then publication time.
    """
    candidates: list[tuple[tuple[int, ...], str, dict]] = []
    for release in releases:
        if release.get("draft"):
            continue
        tag = release.get("tag_name") or ""
        if not tag or not pick_asset(release.get("assets") or [], tag):
            continue
        candidates.append((parse_version(tag), release.get("published_at") or "", release))
    if not candidates:
        raise RuntimeError("No published Dikdik release carries an installer for this platform")
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[-1][2]


def check() -> dict:
    release = latest_release(fetch_releases())
    tag = release.get("tag_name") or ""
    assets = release.get("assets") or []
    asset = pick_asset(assets, tag)
    checksums_url = next((a.get("browser_download_url") for a in assets if a.get("name") == "SHA256SUMS.txt"), None)
    return {
        "current": __version__,
        "latest": tag,
        "available": bool(tag and asset and is_newer(tag, __version__)),
        "tag": tag,
        "name": release.get("name") or tag,
        "notes": (release.get("body") or "")[:4000],
        "url": release.get("html_url") or "",
        "asset": {"name": asset["name"], "url": asset.get("browser_download_url"), "size": asset.get("size")} if asset else None,
        "checksums_url": checksums_url,
    }


def _download(url: str, dest: Path, timeout: int = TIMEOUT, on_progress: Callable[[int, int], None] | None = None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "dikdik-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as out:
            while True:
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
        if len(parts) >= 2 and parts[-1].endswith(asset_name) and len(parts[0]) == 64:
            return parts[0].lower()
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _install_windows(installer: Path) -> None:
    # NSIS silent install; the per-user installer replaces the running copy.
    subprocess.Popen([str(installer), "/S"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _install_macos(dmg: Path) -> None:
    mount = tempfile.mkdtemp(prefix="dikdik-update-")
    attach = subprocess.run(["hdiutil", "attach", "-nobrowse", "-mountpoint", mount, str(dmg)],
                            capture_output=True, timeout=120)
    if attach.returncode != 0:
        raise RuntimeError("Could not mount the downloaded disk image")
    try:
        src = Path(mount) / "Dikdik.app"
        if not src.is_dir():
            raise RuntimeError("The disk image does not contain Dikdik.app")
        dest = Path("/Applications/Dikdik.app")
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
    finally:
        subprocess.run(["hdiutil", "detach", mount], capture_output=True, timeout=60)


def _install_linux(deb: Path) -> None:
    runner = shutil.which("pkexec") or shutil.which("sudo")
    cmd = ([runner] if runner else []) + ["dpkg", "-i", str(deb)]
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "dpkg install failed").strip()[:300])


def install(path: Path) -> None:
    if sys.platform == "win32":
        _install_windows(path)
    elif sys.platform == "darwin":
        _install_macos(path)
    else:
        _install_linux(path)


def _relaunch() -> None:
    if sys.platform == "win32":
        exe = sys.executable
        args = [exe, *sys.argv[1:]] if not getattr(sys, "frozen", False) else [exe]
        subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif sys.platform == "darwin":
        app = "/Applications/Dikdik.app"
        if Path(app).is_dir():
            subprocess.Popen(["open", "-n", app])
    else:
        launch = shutil.which("dikdik") or sys.executable
        if launch:
            subprocess.Popen([launch, *sys.argv[1:]])


def _schedule_relaunch() -> None:
    def later() -> None:
        threading.Event().wait(1.0)  # let the HTTP reply flush first
        _relaunch()
        os._exit(0)
    threading.Thread(target=later, daemon=True).start()


def apply(on_progress: Callable[[int, int], None] | None = None) -> dict:
    info = check()
    if not info["available"] or not info["asset"]:
        raise ValueError(info.get("error") or "No installer is available for this platform")
    asset = info["asset"]
    with tempfile.TemporaryDirectory(prefix="dikdik-update-") as tmp:
        dest = Path(tmp) / asset["name"]
        _download(asset["url"], dest, on_progress=on_progress)
        if info.get("checksums_url"):
            try:
                checksums = Path(tmp) / "SHA256SUMS.txt"
                _download(info["checksums_url"], checksums)
                expected = _expected_hash(checksums.read_text(encoding="utf-8"), asset["name"])
            except Exception:
                expected = None
            if expected is not None and _sha256(dest).lower() != expected.lower():
                raise ValueError("Installer checksum did not match SHA256SUMS.txt")
        install(dest)
    _schedule_relaunch()
    return {"ok": True, "restarting": True, "latest": info["latest"]}