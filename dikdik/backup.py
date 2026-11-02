"""Backup, verification and restore of Dikdik's private data directory (`~/.dikdik`).

    python -m dikdik.backup create [--data DIR] [--output FILE.zip]
    python -m dikdik.backup verify FILE.zip
    python -m dikdik.backup restore FILE.zip [--data DIR] [--force]

A backup is a zip with a manifest listing every file, its size and SHA-256. SQLite databases are
copied with SQLite's online backup API, so a backup is consistent even while Dikdik is running.
Volatile files (the service lock, WAL/SHM side files, temp files, diagnostics logs, caches and
pre-migration copies) are left out.

A backup holds everything Dikdik keeps, including sealed provider keys and the sign-in token:
store it as carefully as the data directory itself. On Windows the secret-store key is wrapped
with DPAPI for the current user, so restored secrets open only for the same Windows account.

`verify` checks every hash and runs `PRAGMA integrity_check` on each database. `restore`
verifies first, refuses while a Dikdik service holds the data directory, and never merges into
an existing directory: with `--force` the current one is moved aside (not deleted) first.
A backup is not proven useful until a restore has been tested; `verify` plus a restore into a
scratch directory is that test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from . import __version__

FORMAT, VERSION = "dikdik-backup", 1
SKIP_NAMES = {"service.lock"}
SKIP_DIRS = {"logs", "marketplace-cache", "__pycache__"}
SKIP_SUFFIXES = ("-wal", "-shm", "-journal", ".tmp", ".dikdik-write")
SQLITE_SUFFIXES = (".sqlite3", ".sqlite", ".db")


def _skip(relative: PurePosixPath) -> bool:
    return (relative.name in SKIP_NAMES or bool(SKIP_DIRS & set(relative.parts[:-1]))
            or relative.name.endswith(SKIP_SUFFIXES) or ".bak-" in relative.name)


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _sqlite_copy(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source, timeout=30)) as live, closing(sqlite3.connect(target)) as copy:
        live.backup(copy)


def _key_mode(data: Path) -> str | None:
    try:
        return json.loads((data / "secrets.bin").read_bytes()).get("mode")
    except (OSError, ValueError):
        return None


def create(data: Path, output: Path) -> dict:
    data, output = Path(data).resolve(), Path(output).resolve()
    if not data.is_dir():
        raise ValueError(f"{data} is not a Dikdik data directory")
    if data in output.parents:
        raise ValueError("Write the backup outside the data directory")
    entries = []
    with tempfile.TemporaryDirectory(prefix="dikdik-backup-") as temp:
        staging = Path(temp)
        partial = output.with_name(output.name + ".partial")
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(data.rglob("*")):
                relative = PurePosixPath(path.relative_to(data).as_posix())
                if not path.is_file() or path.is_symlink() or _skip(relative):
                    continue
                source = path
                if path.name.endswith(SQLITE_SUFFIXES):
                    source = staging / f"{len(entries)}.sqlite3"
                    _sqlite_copy(path, source)
                entries.append({"path": str(relative), "size": source.stat().st_size, "sha256": _digest(source)})
                archive.write(source, f"data/{relative}")
            manifest = {"format": FORMAT, "version": VERSION, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "dikdik": __version__, "platform": os.name, "key_mode": _key_mode(data), "files": entries}
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        os.replace(partial, output)
    return {"output": str(output), "files": len(entries), "bytes": sum(e["size"] for e in entries), "key_mode": manifest["key_mode"]}


def _safe(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in name and ":" not in name


def verify(backup: Path, extract_to: Path | None = None) -> dict:
    """Check the manifest, every file's hash, safe paths and database integrity."""
    problems = []
    with zipfile.ZipFile(backup) as archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, ValueError):
            raise ValueError("Not a Dikdik backup: manifest.json is missing or unreadable") from None
        if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
            raise ValueError("Unsupported backup format")
        listed = {f"data/{entry['path']}": entry for entry in manifest.get("files", [])}
        for name in archive.namelist():
            if not _safe(name):
                raise ValueError(f"Unsafe path in backup: {name!r}")
            if name != "manifest.json" and name not in listed:
                problems.append(f"{name} is not in the manifest")
        with tempfile.TemporaryDirectory(prefix="dikdik-verify-") as temp:
            root = Path(extract_to) if extract_to else Path(temp)
            for name, entry in listed.items():
                if not _safe(entry["path"]):
                    raise ValueError(f"Unsafe path in manifest: {entry['path']!r}")
                try:
                    data = archive.read(name)
                except KeyError:
                    problems.append(f"{entry['path']} is missing")
                    continue
                if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["size"]:
                    problems.append(f"{entry['path']} does not match its recorded hash")
                    continue
                target = root / entry["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                if entry["path"].endswith(SQLITE_SUFFIXES):
                    with closing(sqlite3.connect(target)) as db:
                        result = db.execute("PRAGMA integrity_check").fetchone()[0]
                    if result != "ok":
                        problems.append(f"{entry['path']} failed integrity_check: {result}")
    return {"ok": not problems, "problems": problems, "files": len(listed), "created": manifest.get("created"),
            "dikdik": manifest.get("dikdik"), "key_mode": manifest.get("key_mode")}


def _in_use(data: Path) -> bool:
    if not (data / "service.lock").exists():
        return False
    from .locking import DataLock
    try:
        DataLock(data / "service.lock").close()
        return False
    except ValueError:
        return True


def restore(backup: Path, data: Path, force: bool = False) -> dict:
    data = Path(data).resolve()
    if data.exists() and any(data.iterdir()):
        if _in_use(data):
            raise ValueError("Dikdik is running with this data directory; stop it before restoring")
        if not force:
            raise ValueError(f"{data} is not empty; restore into an empty directory or pass --force to move it aside")
    staging = data.with_name(f"{data.name}.restoring-{int(time.time())}")
    report = verify(backup, extract_to=staging)
    if not report["ok"]:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError("The backup failed verification: " + "; ".join(report["problems"][:5]))
    moved = None
    if data.exists():
        if any(data.iterdir()):
            moved = data.with_name(f"{data.name}.before-restore-{int(time.time())}")
            os.replace(data, moved)
        else:
            data.rmdir()
    os.replace(staging, data)
    return {**report, "restored_to": str(data), "previous_moved_to": str(moved) if moved else None,
            "note": ("Secrets were sealed with Windows DPAPI: they open only for the Windows account that made the backup."
                     if report["key_mode"] == "dpapi" else "")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Back up, verify and restore Dikdik's data directory")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("create")
    make.add_argument("--data", default=str(Path.home() / ".dikdik"))
    make.add_argument("--output", default=None)
    check = commands.add_parser("verify")
    check.add_argument("backup")
    back = commands.add_parser("restore")
    back.add_argument("backup")
    back.add_argument("--data", default=str(Path.home() / ".dikdik"))
    back.add_argument("--force", action="store_true", help="move a non-empty data directory aside first")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            output = args.output or f"dikdik-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip"
            print(json.dumps(create(Path(args.data), Path(output)), indent=2))
            return 0
        if args.command == "verify":
            report = verify(Path(args.backup))
            print(json.dumps(report, indent=2))
            return 0 if report["ok"] else 1
        print(json.dumps(restore(Path(args.backup), Path(args.data), force=args.force), indent=2))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
