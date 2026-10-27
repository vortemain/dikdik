"""Multiple API keys with scopes for the Dikdik server.

The server starts with one session token; this module adds a store for
additional API keys with scopes (read, write, admin) that can be used
instead of the session token for programmatic access. Keys are stored
in the data directory, hashed (never plaintext), and never returned by
the API after creation.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path


SCOPES = {"read", "write", "admin"}


class ApiKeyStore:
    def __init__(self, data: Path):
        self.path = data / "api_keys.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []

    def _write(self, entries: list[dict]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def list(self) -> list[dict]:
        return [{"id": e["id"], "name": e["name"], "scopes": e["scopes"],
                 "created": e["created"], "last_used": e.get("last_used")}
                for e in self._read()]

    def create(self, name: str, scopes: list[str]) -> str:
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("Key name must contain 1-100 characters")
        if not isinstance(scopes, list) or not scopes or len(scopes) > 10:
            raise ValueError("Scopes must be a list of 1-10 entries")
        for scope in scopes:
            if scope not in SCOPES:
                raise ValueError(f"Scope must be one of {sorted(SCOPES)}")
        token = "dikdik-" + secrets.token_hex(24)
        entry = {"id": secrets.token_hex(8), "name": name.strip(),
                 "hash": self._hash(token), "scopes": scopes,
                 "created": time.time(), "last_used": None}
        entries = self._read()
        entries.append(entry)
        self._write(entries)
        return token

    def delete(self, key_id: str) -> None:
        entries = self._read()
        new = [e for e in entries if e["id"] != key_id]
        if len(new) == len(entries):
            raise KeyError(key_id)
        self._write(new)

    def verify(self, token: str) -> dict | None:
        """Verify a token, returning the key entry with scopes or None."""
        if not isinstance(token, str) or not token:
            return None
        hashed = self._hash(token)
        for entry in self._read():
            if entry["hash"] == hashed:
                # Update last_used (best effort, never blocks)
                try:
                    entries = self._read()
                    for e in entries:
                        if e["id"] == entry["id"]:
                            e["last_used"] = time.time()
                    self._write(entries)
                except Exception:
                    pass
                return {"id": entry["id"], "name": entry["name"],
                        "scopes": entry["scopes"]}
        return None