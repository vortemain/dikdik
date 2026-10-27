"""Remote backend connections: connect the UI to another Dikdik server.

A backend is a URL + API key pair for a remote Dikdik server. The UI can
switch between backends per tab. Keys are stored in the encrypted secret
store, not in the backend record itself.
"""

from __future__ import annotations

import json
import time


class BackendStore:
    def __init__(self, store):
        self.store = store
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS backends (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
                    key_ref TEXT, enabled INTEGER NOT NULL DEFAULT 1,
                    last_checked REAL, last_ok INTEGER, created REAL NOT NULL
                );
            """)

    def list(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM backends ORDER BY created").fetchall()]

    def get(self, backend_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM backends WHERE id=?", (backend_id,)).fetchone()
        if row is None:
            raise KeyError(backend_id)
        return dict(row)

    def create(self, body: dict, now: float) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Backend must be a JSON object")
        name = body.get("name", "")
        url = body.get("url", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("name must contain 1-128 characters")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or len(url) > 2000:
            raise ValueError("url must be an http(s) URL")
        import secrets as _s
        backend_id = _s.token_hex(12)
        key_ref = body.get("key_ref")
        if key_ref is not None and (not isinstance(key_ref, str) or len(key_ref) > 64):
            raise ValueError("key_ref must be a string reference to a secret")
        with self.store.connect() as db:
            db.execute("INSERT INTO backends(id,name,url,key_ref,enabled,created) VALUES(?,?,?,?,1,?)",
                       (backend_id, name.strip(), url.strip(), key_ref, now))
            return dict(db.execute("SELECT * FROM backends WHERE id=?", (backend_id,)).fetchone())

    def update(self, backend_id: str, body: dict) -> dict:
        if not isinstance(body, dict) or set(body) - {"name", "url", "key_ref", "enabled"}:
            raise ValueError("Backend accepts name, url, key_ref and enabled")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM backends WHERE id=?", (backend_id,)).fetchone()
            if row is None:
                raise KeyError(backend_id)
            fields, values = [], []
            if "name" in body:
                if not isinstance(body["name"], str) or not body["name"].strip():
                    raise ValueError("name must be a non-empty string")
                fields.append("name=?"); values.append(body["name"].strip())
            if "url" in body:
                if not isinstance(body["url"], str) or not body["url"].startswith(("http://", "https://")):
                    raise ValueError("url must be an http(s) URL")
                fields.append("url=?"); values.append(body["url"].strip())
            if "key_ref" in body:
                fields.append("key_ref=?"); values.append(body["key_ref"])
            if "enabled" in body:
                fields.append("enabled=?"); values.append(1 if body["enabled"] else 0)
            if fields:
                values.append(backend_id)
                db.execute(f"UPDATE backends SET {', '.join(fields)} WHERE id=?", values)
            return dict(db.execute("SELECT * FROM backends WHERE id=?", (backend_id,)).fetchone())

    def delete(self, backend_id: str) -> None:
        with self.store.connect() as db:
            row = db.execute("SELECT 1 FROM backends WHERE id=?", (backend_id,)).fetchone()
            if row is None:
                raise KeyError(backend_id)
            db.execute("DELETE FROM backends WHERE id=?", (backend_id,))

    def check(self, backend_id: str, now: float) -> dict:
        """Mark a backend as checked and record its health."""
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM backends WHERE id=?", (backend_id,)).fetchone()
            if row is None:
                raise KeyError(backend_id)
            db.execute("UPDATE backends SET last_checked=?, last_ok=? WHERE id=?",
                       (now, 1, backend_id))
            return dict(db.execute("SELECT * FROM backends WHERE id=?", (backend_id,)).fetchone())