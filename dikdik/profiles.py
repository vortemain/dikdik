"""Model profiles: SQLite-backed CRUD with contract validation.

Credentials never live here; only an opaque `credential_secret_ref` pointing
into the secrets store is accepted, and it is never returned by reads.
"""

import time
import urllib.parse
import uuid

from .model_catalog import approved_endpoints

PROVIDERS = {"openai", "anthropic", "azure", "local"}
FIXED_ENDPOINTS = {
    "https://api.openai.com/v1",
    "https://api.anthropic.com/v1",
    "https://mojave.cloud/v1",
}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
MAX_PROFILES = 50

# Additive, defensive hook for the agent-layer whitelist; profiles carry no
# permission semantics, but a shared referral prevents accidental key reuse.
NON_PATCHABLE_KEYS = {"id"}
PATCHABLE = {"name", "provider", "model", "endpoint", "timeout_seconds", "is_default", "credential_secret_ref",
             "connection_id", "temperature", "top_p", "max_output_tokens"}
# Columns added after the first release; migrated in place.
EXTRA_COLUMNS = (("connection_id", "TEXT"), ("temperature", "REAL"), ("top_p", "REAL"), ("max_output_tokens", "INTEGER"))


class ApiError(Exception):
    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status = status
        self.error = error


def _ref_valid(ref) -> bool:
    return isinstance(ref, str) and len(ref) <= 64 and len(ref) >= 1 and all((c.isalnum() or c in "_.-") for c in ref) and not ref.startswith(".")


def _endpoint_valid(endpoint, approved: set[str] = frozenset()) -> bool:
    """Loopback, a catalog provider's base URL, or a custom provider the user registered."""
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 400:
        return False
    if endpoint in FIXED_ENDPOINTS or endpoint.rstrip("/") in approved:
        return True
    parts = urllib.parse.urlsplit(endpoint)
    if parts.scheme not in {"http", "https"}:
        return False
    host = parts.hostname
    return host is not None and host.lower() in {"localhost", "127.0.0.1", "::1"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
    model TEXT NOT NULL, endpoint TEXT, timeout_seconds INTEGER,
    credential_secret_ref TEXT, is_default INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL
);
"""


def migrate(db) -> None:
    db.execute(SCHEMA)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(profiles)")}
    for name, kind in EXTRA_COLUMNS:
        if name not in columns:
            db.execute(f"ALTER TABLE profiles ADD COLUMN {name} {kind}")


class ProfileStore:
    """All state lives in the shared Store; instances are stateless."""

    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            migrate(db)

    # --- validation ----------------------------------------------------
    def _validate(self, body, *, require_core: bool):
        if not isinstance(body, dict):
            raise ApiError(400, "Body must be a JSON object")
        out = {}
        if require_core or "name" in body:
            name = body.get("name")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(ord(c) < 32 for c in name):
                raise ApiError(400, "Name must contain 1–64 characters on one line")
            out["name"] = name.strip()
        if require_core or "provider" in body:
            provider = body.get("provider")
            if provider not in PROVIDERS:
                raise ApiError(400, "Provider must be one of openai, anthropic, azure, local")
            out["provider"] = provider
        if require_core or "model" in body:
            model = body.get("model")
            if not isinstance(model, str) or not 1 <= len(model.strip()) <= 128:
                raise ApiError(400, "Model must contain 1–128 characters")
            out["model"] = model.strip()
        for key, low, high, label in (("temperature", 0, 2, "Temperature must be 0–2"), ("top_p", 0, 1, "Top P must be 0–1")):
            if key in body:
                value = body[key]
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high):
                    raise ApiError(400, label)
                out[key] = value
        if "max_output_tokens" in body:
            value = body["max_output_tokens"]
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 32000):
                raise ApiError(400, "Max output tokens must be a whole number of 1–32000")
            out["max_output_tokens"] = value
        if "connection_id" in body:
            connection = body["connection_id"] or None
            if connection is not None:
                with self.store.connect() as db:
                    db.execute("CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, provider TEXT NOT NULL, endpoint TEXT, created REAL NOT NULL)")
                    if not isinstance(connection, str) or db.execute("SELECT 1 FROM connections WHERE id=?", (connection,)).fetchone() is None:
                        raise ApiError(400, "That provider connection no longer exists")
            out["connection_id"] = connection
        if "endpoint" in body:
            endpoint = body["endpoint"]
            if endpoint is not None and not _endpoint_valid(endpoint, approved_endpoints(self.store)):
                raise ApiError(400, "Endpoint must be an http(s) loopback URL or an approved public provider URL")
            out["endpoint"] = endpoint
        if "timeout_seconds" in body:
            value = body["timeout_seconds"]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 600:
                raise ApiError(400, "Timeout must be an integer of 1–600 seconds")
            out["timeout_seconds"] = value
        if "credential_secret_ref" in body:
            ref = body["credential_secret_ref"]
            if ref is not None and not _ref_valid(ref):
                raise ApiError(400, "Credential secret reference must be 1–64 characters of letters, digits, '.', '_' or '-'")
            out["credential_secret_ref"] = ref
        if "is_default" in body:
            if not isinstance(body["is_default"], bool):
                raise ApiError(400, "Default flag must be boolean")
            out["is_default"] = body["is_default"]
        return out

    # --- CRUD ----------------------------------------------------------
    def create(self, body):
        fields = self._validate(body, require_core=True)
        profile_id = uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute(SCHEMA)
            row = db.execute("SELECT 1 FROM profiles WHERE name=?", (fields["name"],)).fetchone()
            if row:
                raise ApiError(400, "A profile with this name already exists")
            if db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] >= MAX_PROFILES:
                raise ApiError(409, f"Profile limit reached ({MAX_PROFILES}). Delete one before adding another")
            default = 0 if db.execute("SELECT 1 FROM profiles LIMIT 1").fetchone() else 1
            db.execute(
                "INSERT INTO profiles(id,name,provider,model,endpoint,timeout_seconds,credential_secret_ref,is_default,created,connection_id,temperature,top_p,max_output_tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (profile_id, fields["name"], fields["provider"], fields["model"], fields.get("endpoint"), fields.get("timeout_seconds"), fields.get("credential_secret_ref"), default, time.time(),
                 fields.get("connection_id"), fields.get("temperature"), fields.get("top_p"), fields.get("max_output_tokens")),
            )
        return 201, {"id": profile_id}

    def duplicate(self, profile_id: str):
        """A copy named `<name>-copy` (then `-copy-1`, …) with the same model, key and settings; never the default."""
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
            if row is None:
                raise ApiError(404, "Profile not found")
            names = {r[0] for r in db.execute("SELECT name FROM profiles")}
            if len(names) >= MAX_PROFILES:
                raise ApiError(409, f"Profile limit reached ({MAX_PROFILES}). Delete one before adding another")
            base = row["name"][:58]
            name, n = f"{base}-copy", 0
            while name in names:
                n += 1
                name = f"{base}-copy-{n}"
            new = uuid.uuid4().hex
            db.execute("INSERT INTO profiles(id,name,provider,model,endpoint,timeout_seconds,credential_secret_ref,is_default,created,connection_id,temperature,top_p,max_output_tokens) VALUES(?,?,?,?,?,?,?,0,?,?,?,?,?)",
                       (new, name, row["provider"], row["model"], row["endpoint"], row["timeout_seconds"], row["credential_secret_ref"], time.time(),
                        row["connection_id"], row["temperature"], row["top_p"], row["max_output_tokens"]))
        return 201, {"id": new, "name": name}

    def list_profiles(self):
        with self.store.connect() as db:
            db.execute(SCHEMA)
            rows = db.execute("SELECT * FROM profiles ORDER BY created").fetchall()
        # Normalize nulls to the contract's documented defaults so the response shape
        # is stable: endpoint defaults to "" (no override), timeout to 60s.
        return 200, [{"id": r["id"], "name": r["name"], "provider": r["provider"], "model": r["model"], "endpoint": r["endpoint"] or "", "timeout_seconds": r["timeout_seconds"] if r["timeout_seconds"] is not None else 60, "is_default": bool(r["is_default"]),
                     "connection_id": r["connection_id"] or "", "key_ref": r["credential_secret_ref"] or "",
                     "temperature": r["temperature"], "top_p": r["top_p"], "max_output_tokens": r["max_output_tokens"]} for r in rows]

    def patch(self, profile_id: str, body):
        if not isinstance(body, dict):
            raise ApiError(400, "Body must be a JSON object")
        with self.store.connect() as db:
            db.execute(SCHEMA)
            row = db.execute("SELECT id FROM profiles WHERE id=?", (profile_id,)).fetchone()
            if row is None:
                raise ApiError(404, "Profile not found")
        unknown = set(body) - PATCHABLE
        if unknown:
            raise ApiError(400, "Unknown profile fields: " + ", ".join(sorted(unknown)))
        fields = self._validate(body, require_core=False)
        if not fields:
            raise ApiError(400, "Nothing to update")
        if "is_default" in fields and not isinstance(fields["is_default"], bool):
            raise ApiError(400, "Default flag must be boolean")
        with self.store.connect() as db:
            row = db.execute("SELECT id FROM profiles WHERE id=?", (profile_id,)).fetchone()
            if row is None:
                raise ApiError(404, "Profile not found")
            if "name" in fields and db.execute("SELECT 1 FROM profiles WHERE name=? AND id<>?", (fields["name"], profile_id)).fetchone():
                raise ApiError(409, f"Profile '{fields['name']}' already exists")
            for key, value in fields.items():
                db.execute(f"UPDATE profiles SET {key}=? WHERE id=?", (int(value) if key == "is_default" else value, profile_id))
            if fields.get("is_default"):
                db.execute("UPDATE profiles SET is_default=0 WHERE id<>?", (profile_id,))
        return 200, {"id": profile_id, **{k: (v if k != "is_default" else bool(v)) for k, v in fields.items()}}

    def delete(self, profile_id: str, confirm_default: bool):
        with self.store.connect() as db:
            db.execute(SCHEMA)
            row = db.execute("SELECT is_default FROM profiles WHERE id=?", (profile_id,)).fetchone()
            if row is None:
                raise ApiError(404, "Profile not found")
            if row["is_default"] and not confirm_default:
                raise ApiError(409, "Deleting the default profile requires explicit confirmation (confirm_default=1)")
            db.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
            remaining = db.execute("SELECT id FROM profiles ORDER BY created LIMIT 1").fetchall()
            if remaining:
                db.execute("UPDATE profiles SET is_default=1 WHERE id=?", (remaining[0]["id"],))
        return 200, {"deleted": profile_id}
