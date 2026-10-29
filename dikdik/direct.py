"""Direct edits with checkpoints: the agent writes your real files, and a turn can be undone.

In local mode the agent works like Claude Code: its edits land in the project folder
(so your own tools, tests and editor see them at once) instead of waiting for Apply.
Every write is checked first: if the file on disk is no longer what the agent last
saw, someone else changed it and the agent does not overwrite it. Before the first
change to each file in a turn, its previous content is recorded in a checkpoint,
and Undo puts every file back, again only where nobody changed it since.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


def _read(target: Path) -> str | None:
    try:
        return target.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return "\0unreadable"  # never equal to any text, so it counts as changed by someone else


def _write(target: Path, text: str | None) -> None:
    if text is None:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.dikdik-write")
    temporary.write_bytes(text.encode("utf-8"))
    if target.exists():
        try:
            temporary.chmod(target.stat().st_mode)
        except OSError:
            pass
    os.replace(temporary, target)


class DirectWrites:
    """One turn's writes into the workspace, with the checkpoint that undoes them."""

    def __init__(self, root: Path, original: dict[str, str], checkpoint: Path, resume: bool = False):
        self.root = root.resolve()
        self.known = dict(original)          # what the agent believes each file holds on disk
        self.before: dict[str, str | None] = {}  # content before this turn first changed it
        self.checkpoint = checkpoint
        self.created = time.time()
        self._resumed = resume
        self._guarded = set()
        if resume:
            record = json.loads(checkpoint.read_text(encoding="utf-8"))
            if record.get("undone"):
                raise ValueError("This checkpoint was undone; send a new request instead of resuming it")
            self.created = record["created"]
            for path, change in record["files"].items():
                self._target(path)  # validate every restored path before using it
                self.before[path] = change["before"]
                self.known[path] = change["after"]
                self._guarded.add(path)

    def _begin_new_writes(self):
        if self._resumed:
            # A verification-only resume keeps the previous Undo. If it makes
            # further edits, Undo should restore the state at this resume boundary.
            self.checkpoint = self.checkpoint.with_name(uuid.uuid4().hex + ".json")
            self.before = {}
            self.created = time.time()
            self._resumed = False

    def _target(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if self.root not in target.parents or target.relative_to(self.root).parts[0].lower() == ".git":
            raise ValueError(f"{path} is outside the project")
        return target

    def preview(self, files: dict[str, str]) -> list[dict]:
        """What write() would change, without writing. Each entry: path, before, after."""
        changes = []
        for path, text in sorted(files.items()):
            if self.known.get(path) == text:
                continue
            target = self._target(path)
            current = _read(target)
            if current != self.known.get(path):
                continue  # conflict — would be skipped
            changes.append({"path": path, "before": current, "after": text})
        return changes

    def write(self, files: dict[str, str]) -> list[str]:
        """Bring the folder up to date with the agent's files. Returns paths someone else
        changed meanwhile, which are left alone."""
        conflicts = []
        for path, text in sorted(files.items()):
            if self.known.get(path) == text:
                if path in self._guarded and _read(self._target(path)) != text:
                    conflicts.append(path)
                continue
            target = self._target(path)
            current = _read(target)
            if current != self.known.get(path):
                conflicts.append(path)
                continue
            self._begin_new_writes()
            self.before.setdefault(path, current)
            self._guarded.add(path)
            _write(target, text)
            self.known[path] = text
        self.save()
        return conflicts

    def observe(self, after: dict[str, str]) -> None:
        """A command ran in the folder: what it changed is part of this turn too."""
        for path, text in after.items():
            if self.known.get(path) != text:
                self._begin_new_writes()
                self.before.setdefault(path, self.known.get(path))
                self._guarded.add(path)
                self.known[path] = text
        self.save()

    def changed(self) -> list[str]:
        return sorted(path for path, old in self.before.items() if old != self.known.get(path))

    def save(self) -> None:
        if not self.before:
            return
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        record = {"created": self.created, "undone": False,
                  "files": {path: {"before": old, "after": self.known.get(path)} for path, old in self.before.items()}}
        temporary = self.checkpoint.with_suffix(".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, self.checkpoint)


def undo(root: Path, checkpoint: Path) -> dict:
    """Restore every file of one checkpoint that still holds what the turn left there."""
    try:
        record = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError("checkpoint") from None
    if record.get("undone"):
        raise ValueError("This turn was already undone")
    return _rollback(root, checkpoint, record, forward=False)


def redo(root: Path, checkpoint: Path) -> dict:
    """Re-apply an undone turn: put its changes back where Undo left them untouched.
    Undo and Redo can be repeated in turn for as long as nobody edits the files."""
    try:
        record = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError("checkpoint") from None
    if not record.get("undone"):
        raise ValueError("This turn has not been undone")
    return _rollback(root, checkpoint, record, forward=True)


def _rollback(root: Path, checkpoint: Path, record: dict, *, forward: bool) -> dict:
    """`forward` re-applies a turn (its "after" content); otherwise restore its "before"."""
    key, other = ("after", "before") if forward else ("before", "after")
    root = root.resolve()
    restored, conflicts = [], []
    for path, change in sorted(record["files"].items()):
        target = (root / path).resolve()
        if root not in target.parents:
            continue
        if _read(target) != change.get(other, change["before"]):
            conflicts.append(path)
            continue
        _write(target, change[key])
        restored.append(path)
    record["undone"] = not forward
    record["conflicts"] = conflicts
    checkpoint.write_text(json.dumps(record), encoding="utf-8")
    return {"restored": restored, "conflicts": conflicts}
