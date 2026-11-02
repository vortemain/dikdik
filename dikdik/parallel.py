"""Parallel writing agents with explicit file ownership.

The coordinating agent splits work into 2-4 units, each owning a disjoint set of files or folders
(``src/api/`` owns everything under it; ``src/app.py`` owns one file). Every writer starts from
the same snapshot of the candidate and may edit or create only paths it owns; no writer runs
commands, so writers never race on the working copy. The coordinator merges the results:
because ownership is disjoint, each changed path has exactly one author and the merge is a union.
A writer whose result touches a path it does not own, or that did not finish, is rejected as a whole. Tests run once, by
the coordinator, on the merged files.
"""

from __future__ import annotations

import posixpath
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

MIN_UNITS, MAX_UNITS, MAX_SCOPES = 2, 4, 40


def normalize(scope: str) -> str:
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 500:
        raise ValueError("Each owned path must be a non-empty relative path")
    text = scope.strip().replace("\\", "/")
    folder = text.endswith("/")
    if text.startswith("/") or ":" in text.split("/")[0] or any(part == ".." for part in text.split("/")):
        raise ValueError(f"Owned paths must stay inside the project: {scope}")
    clean = posixpath.normpath(text)
    if clean in {".", ""}:
        raise ValueError("A writer cannot own the whole project; split it into folders or files")
    return clean + "/" if folder else clean


def owns(scopes: list[str], path: str) -> bool:
    path = path.replace("\\", "/")
    return any(path == scope or (scope.endswith("/") and path.startswith(scope)) for scope in scopes)


def overlap(a: str, b: str) -> bool:
    return a == b or (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b))


def plan(units) -> list[dict]:
    """Validate units and check that no two writers own the same path."""
    if not isinstance(units, list) or not MIN_UNITS <= len(units) <= MAX_UNITS:
        raise ValueError(f"Split the work into {MIN_UNITS}-{MAX_UNITS} units")
    checked = []
    for index, unit in enumerate(units):
        if not isinstance(unit, dict) or set(unit) - {"task", "paths"}:
            raise ValueError("Each unit is {task, paths}")
        task, paths = unit.get("task"), unit.get("paths")
        if not isinstance(task, str) or not task.strip() or len(task) > 4000:
            raise ValueError("Each unit needs a task of at most 4000 characters")
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_SCOPES:
            raise ValueError(f"Each unit owns 1-{MAX_SCOPES} files or folders")
        checked.append({"index": index, "task": task.strip(), "paths": sorted({normalize(p) for p in paths})})
    for i, first in enumerate(checked):
        for second in checked[i + 1:]:
            for a in first["paths"]:
                for b in second["paths"]:
                    if overlap(a, b):
                        raise ValueError(f"Units {first['index'] + 1} and {second['index'] + 1} both own {min(a, b, key=len)}; ownership must not overlap")
    return checked


def run(units: list[dict], snapshot: dict[str, str], writer: Callable[[dict, dict[str, str]], dict],
        cancel: threading.Event) -> list[dict]:
    """Run every writer concurrently on its own copy of the snapshot."""
    def one(unit):
        if cancel.is_set():
            return {"state": "cancelled", "files": dict(snapshot), "error": "Cancelled before starting"}
        try:
            outcome = writer(unit, dict(snapshot))
        except Exception as exc:
            return {"state": "failed", "files": dict(snapshot), "error": str(exc)[:500]}
        return outcome if isinstance(outcome, dict) and isinstance(outcome.get("files"), dict) else {
            "state": "failed", "files": dict(snapshot), "error": "The writer returned no files"}
    with ThreadPoolExecutor(max_workers=len(units), thread_name_prefix="dikdik-writer") as pool:
        return list(pool.map(one, units))


def merge(snapshot: dict[str, str], units: list[dict], outcomes: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """Union of every accepted writer's changes; returns (merged files, per-unit report)."""
    merged = dict(snapshot)
    authors: dict[str, int] = {}
    report = []
    for unit, outcome in zip(units, outcomes):
        files = outcome["files"]
        changed = sorted(p for p, text in files.items() if snapshot.get(p) != text)
        removed = sorted(set(snapshot) - set(files))
        outside = [p for p in changed + removed if not owns(unit["paths"], p)]
        entry = {"unit": unit["index"] + 1, "task": unit["task"][:200], "owns": unit["paths"], "state": outcome.get("state"),
                 "changed": changed, **({"error": outcome["error"]} if outcome.get("error") else {})}
        if changed and outcome.get("state") != "completed":
            # A writer that stopped part-way may leave half-done edits; none of them are merged.
            entry.update(status="rejected", reason=f"the writer ended {outcome.get('state')} before finishing; its edits were not merged")
        elif outside:
            entry.update(status="rejected", reason="changed files it does not own: " + ", ".join(outside[:10]))
        elif removed:
            entry.update(status="rejected", reason="removed files: " + ", ".join(removed[:10]))
        elif any(p in authors for p in changed):  # impossible with disjoint ownership; kept as a guard
            entry.update(status="rejected", reason="conflicts with unit " + str(authors[next(p for p in changed if p in authors)] + 1))
        else:
            for path in changed:
                merged[path] = files[path]
                authors[path] = unit["index"]
            entry["status"] = "merged" if changed else "no_changes"
        report.append(entry)
    return merged, report
