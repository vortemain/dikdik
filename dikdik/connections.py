"""Provider connections: one provider + API key that several model profiles share.

The key lives in the encrypted secrets store under `conn.<id>`; this table only
holds the name, provider and optional endpoint. Keys are never returned.
"""

import time
import uuid

from .model_catalog import approved_endpoints
from .profiles import PROVIDERS, ApiError, _endpoint_valid

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
    endpoint TEXT, created REAL NOT NULL
);
"""


MAX_CONNECTIONS = 64


def secret_ref(connection_id: str) -> str:
    return f"conn.{connection_id}"


class ConnectionStore:
    def __init__(self, store, secrets):
        self.store, self.secrets = store, secrets
        with store.connect() as db:
            db.execute(SCHEMA)

    @staticmethod
    def _name(value) -> str:
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 128 or any(ord(c) < 32 for c in value):
            raise ApiError(400, "Name must contain 1–128 characters on one line")
        return value.strip()

    @staticmethod
    def _key(value) -> bytes:
        if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value.strip()):
            raise ApiError(400, "API key must be printable text without spaces")
        return value.strip().encode()

    def list(self):
        stored = {item["ref"] for item in self.secrets.list()}
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM connections ORDER BY created").fetchall()
            counts = {}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='profiles'").fetchone():
                columns = {r["name"] for r in db.execute("PRAGMA table_info(profiles)")}
                if "connection_id" in columns:
                    counts = {r[0]: r[1] for r in db.execute("SELECT connection_id, COUNT(*) FROM profiles WHERE connection_id IS NOT NULL GROUP BY connection_id")}
        return [{"id": r["id"], "name": r["name"], "provider": r["provider"], "endpoint": r["endpoint"] or "",
                 "models": counts.get(r["id"], 0), "key_set": secret_ref(r["id"]) in stored} for r in rows]

    def get(self, connection_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise ApiError(404, "Connection not found")
        return dict(row)

    def create(self, body):
        if not isinstance(body, dict):
            raise ApiError(400, "Body must be a JSON object")
        name = self._name(body.get("name"))
        if body.get("provider") not in PROVIDERS:
            raise ApiError(400, "Provider must be one of openai, anthropic, azure, local")
        endpoint = body.get("endpoint") or None
        if endpoint is not None and not _endpoint_valid(endpoint, approved_endpoints(self.store)):
            raise ApiError(400, "Endpoint must be an http(s) loopback URL or an approved public provider URL")
        key = self._key(body.get("api_key")) if body.get("provider") != "local" or body.get("api_key") else None
        connection_id = uuid.uuid4().hex
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM connections WHERE name=?", (name,)).fetchone():
                raise ApiError(400, f"A connection called “{name}” already exists")
            if db.execute("SELECT COUNT(*) FROM connections").fetchone()[0] >= MAX_CONNECTIONS:
                raise ApiError(409, f"Provider connection limit reached ({MAX_CONNECTIONS}). Delete one before adding another")
        if key:
            self.secrets.set(secret_ref(connection_id), key)  # sealed first: no row ever points at a missing key
        with self.store.connect() as db:
            db.execute("INSERT INTO connections(id,name,provider,endpoint,created) VALUES(?,?,?,?,?)",
                       (connection_id, name, body["provider"], endpoint, time.time()))
        return {"id": connection_id}

    def update(self, connection_id: str, body):
        if not isinstance(body, dict) or not body or set(body) - {"name", "api_key", "endpoint"}:
            raise ApiError(400, "Only name, api_key and endpoint can be changed")
        self.get(connection_id)
        changes = {}
        if "name" in body:
            changes["name"] = self._name(body["name"])
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM connections WHERE name=? AND id<>?", (changes["name"], connection_id)).fetchone():
                    raise ApiError(400, f"A connection called “{changes['name']}” already exists")
        if "endpoint" in body:
            endpoint = body["endpoint"] or None
            if endpoint is not None and not _endpoint_valid(endpoint, approved_endpoints(self.store)):
                raise ApiError(400, "Endpoint must be an http(s) loopback URL or an approved public provider URL")
            changes["endpoint"] = endpoint
        if body.get("api_key"):  # blank keeps the current key
            self.secrets.set(secret_ref(connection_id), self._key(body["api_key"]))
        with self.store.connect() as db:
            for column, value in changes.items():
                db.execute(f"UPDATE connections SET {column}=? WHERE id=?", (value, connection_id))
        return {"id": connection_id, "rotated": bool(body.get("api_key"))}

    def delete(self, connection_id: str):
        self.get(connection_id)
        with self.store.connect() as db:
            users = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='profiles'").fetchone():
                columns = {r["name"] for r in db.execute("PRAGMA table_info(profiles)")}
                if "connection_id" in columns:
                    users = [r[0] for r in db.execute("SELECT name FROM profiles WHERE connection_id=? ORDER BY name", (connection_id,))]
            if users:
                raise ApiError(409, "Used by model profile" + ("s " if len(users) > 1 else " ") + ", ".join(f"“{u}”" for u in users) + ". Unlink or delete them first.")
            db.execute("DELETE FROM connections WHERE id=?", (connection_id,))
        try:
            self.secrets.delete(secret_ref(connection_id))
        except KeyError:
            pass
        return {"deleted": connection_id}
