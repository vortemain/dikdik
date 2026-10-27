"""A registry of approved workspace roots, stored in the data directory.

The registry is a simple list of paths the user has approved as workspace
roots. The server starts with one --workspace; the registry lets the UI
offer recently used roots for a future multi-workspace mode.
"""

import json
import time
from pathlib import Path


class WorkspaceRegistry:
    def __init__(self, data: Path):
        self.path = data / "workspaces.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []

    def _write(self, entries: list[dict]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries[-20:], indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def list(self) -> list[dict]:
        return self._read()

    def add(self, root: str, name: str | None = None) -> dict:
        if not isinstance(root, str) or not root.strip() or len(root) > 1000:
            raise ValueError("Workspace root must be a safe path string")
        resolved = str(Path(root).resolve())
        entries = self._read()
        # Remove any existing entry for this root, then prepend.
        entries = [e for e in entries if e.get("path") != resolved]
        entry = {"path": resolved, "name": name or Path(resolved).name, "added": time.time()}
        entries.insert(0, entry)
        self._write(entries)
        return entry

    def remove(self, root: str) -> None:
        resolved = str(Path(root).resolve())
        entries = [e for e in self._read() if e.get("path") != resolved]
        self._write(entries)