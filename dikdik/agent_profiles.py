"""Agent profiles: named instructions, model, limits and tool access for agent tasks.

The instructions are the user's own words for the agent. Limits can only be set
within the built-in bounds, and tool switches can only narrow what a task's
approvals already allow, never widen it.
"""

import time
import uuid

from .profiles import SCHEMA as MODEL_PROFILES

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_profiles (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, instructions TEXT NOT NULL DEFAULT '',
    model_profile_id TEXT, max_model_calls INTEGER, max_tool_calls INTEGER, deadline_seconds INTEGER,
    allow_shell INTEGER NOT NULL DEFAULT 1, use_mcp INTEGER NOT NULL DEFAULT 1, use_skills INTEGER NOT NULL DEFAULT 1,
    created REAL NOT NULL
);
"""
LIMITS = {"max_model_calls": (1, 60), "max_tool_calls": (1, 200), "deadline_seconds": (30, 1800)}
FLAGS = ("allow_shell", "allow_browser", "use_mcp", "use_skills")
MAX_PROFILES = 50


class AgentProfiles:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(agent_profiles)")}
            if "allow_browser" not in columns:
                db.execute("ALTER TABLE agent_profiles ADD COLUMN allow_browser INTEGER NOT NULL DEFAULT 1")

    @staticmethod
    def _row(row) -> dict:
        return {"id": row["id"], "name": row["name"], "instructions": row["instructions"], "model_profile_id": row["model_profile_id"] or "",
                **{key: row[key] for key in LIMITS}, **{flag: bool(row[flag]) for flag in FLAGS}}

    def _validate(self, body: dict, creating: bool) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Body must be a JSON object")
        unknown = set(body) - {"name", "instructions", "model_profile_id", *LIMITS, *FLAGS}
        if unknown:
            raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")
        out = {}
        if creating or "name" in body:
            name = body.get("name")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or name.strip().lower() == "default":
                raise ValueError("Name must contain 1–64 characters and not be “Default”")
            out["name"] = name.strip()
        if "instructions" in body:
            text = body["instructions"]
            if not isinstance(text, str) or len(text.encode()) > 16384:
                raise ValueError("Instructions must be text of at most 16KB")
            out["instructions"] = text.strip()
        if "model_profile_id" in body:
            ref = body["model_profile_id"]
            if ref not in (None, ""):
                with self.store.connect() as db:
                    db.execute(MODEL_PROFILES)
                    if not isinstance(ref, str) or db.execute("SELECT 1 FROM profiles WHERE id=?", (ref,)).fetchone() is None:
                        raise ValueError("Choose a model profile from Settings → Models")
            out["model_profile_id"] = ref or None
        for key, (low, high) in LIMITS.items():
            if key in body and body[key] is not None:
                value = body[key]
                if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                    raise ValueError(f"{key.replace('_', ' ')} must be a whole number from {low} to {high}")
                out[key] = value
            elif key in body:
                out[key] = None
        for flag in FLAGS:
            if flag in body:
                if not isinstance(body[flag], bool):
                    raise ValueError(f"{flag.replace('_', ' ')} must be true or false")
                out[flag] = int(body[flag])
        return out

    def list(self) -> list[dict]:
        with self.store.connect() as db:
            return [self._row(r) for r in db.execute("SELECT * FROM agent_profiles ORDER BY created")]

    def get(self, profile_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM agent_profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return self._row(row)

    def create(self, body) -> dict:
        fields = self._validate(body, creating=True)
        with self.store.connect() as db:
            if db.execute("SELECT COUNT(*) FROM agent_profiles").fetchone()[0] >= MAX_PROFILES:
                raise ValueError(f"At most {MAX_PROFILES} agent profiles")
            if db.execute("SELECT 1 FROM agent_profiles WHERE name=?", (fields["name"],)).fetchone():
                raise ValueError("An agent profile with this name already exists")
            profile_id = uuid.uuid4().hex
            columns = ["id", "created", *fields]
            db.execute(f"INSERT INTO agent_profiles({','.join(columns)}) VALUES({','.join('?' * len(columns))})", (profile_id, time.time(), *fields.values()))
        return {"id": profile_id}

    def update(self, profile_id: str, body) -> dict:
        fields = self._validate(body, creating=False)
        if not fields:
            raise ValueError("Nothing to update")
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM agent_profiles WHERE id=?", (profile_id,)).fetchone() is None:
                raise KeyError(profile_id)
            if "name" in fields and db.execute("SELECT 1 FROM agent_profiles WHERE name=? AND id<>?", (fields["name"], profile_id)).fetchone():
                raise ValueError("An agent profile with this name already exists")
            for key, value in fields.items():
                db.execute(f"UPDATE agent_profiles SET {key}=? WHERE id=?", (value, profile_id))
        return self.get(profile_id)

    def delete(self, profile_id: str) -> dict:
        with self.store.connect() as db:
            if db.execute("DELETE FROM agent_profiles WHERE id=?", (profile_id,)).rowcount == 0:
                raise KeyError(profile_id)
        return {"deleted": profile_id}
