"""Model routers: pick a model profile per message.

A router names two model profiles and a rule. The rule is applied to each turn
and the choice is recorded in the conversation, so the user can compare results;
nothing here claims one model is better than another.
"""

import time
import uuid

STRATEGIES = {
    "first_turn": "The first message of a conversation goes to the strong model; follow-ups to the fast one",
    "long_message": "Messages longer than the threshold (characters) go to the strong model; shorter ones to the fast one",
    "agent_mode": "Agent turns go to the strong model; planning and single proposals to the fast one",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS routers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, strategy TEXT NOT NULL,
    strong_profile_id TEXT NOT NULL, fast_profile_id TEXT NOT NULL, threshold INTEGER,
    created REAL NOT NULL
);
"""


class RouterStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute(SCHEMA)

    def _profiles(self) -> dict[str, str]:
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='profiles'").fetchone():
                return {}
            return {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM profiles")}

    def _validate(self, body, current=None) -> dict:
        if not isinstance(body, dict) or set(body) - {"name", "strategy", "strong_profile_id", "fast_profile_id", "threshold"}:
            raise ValueError("Unknown router fields")
        merged = {**(current or {}), **body}
        name = merged.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(ord(c) < 32 for c in name):
            raise ValueError("Name must contain 1–64 characters on one line")
        if merged.get("strategy") not in STRATEGIES:
            raise ValueError("Strategy must be one of " + ", ".join(STRATEGIES))
        profiles = self._profiles()
        for key in ("strong_profile_id", "fast_profile_id"):
            if merged.get(key) not in profiles:
                raise ValueError("Pick two existing model profiles")
        threshold = merged.get("threshold")
        if merged["strategy"] == "long_message":
            if isinstance(threshold, bool) or not isinstance(threshold, int) or not 20 <= threshold <= 4000:
                raise ValueError("The length threshold must be 20–4000 characters")
        else:
            threshold = None
        return {"name": name.strip(), "strategy": merged["strategy"], "strong_profile_id": merged["strong_profile_id"],
                "fast_profile_id": merged["fast_profile_id"], "threshold": threshold}

    def list(self):
        names = self._profiles()
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM routers ORDER BY created").fetchall()
        return [{**{k: r[k] for k in ("id", "name", "strategy", "strong_profile_id", "fast_profile_id", "threshold")},
                 "strong_name": names.get(r["strong_profile_id"], ""), "fast_name": names.get(r["fast_profile_id"], ""),
                 "broken": r["strong_profile_id"] not in names or r["fast_profile_id"] not in names} for r in rows]

    def get(self, router_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM routers WHERE id=?", (router_id,)).fetchone()
        if row is None:
            raise KeyError(router_id)
        return dict(row)

    def create(self, body):
        fields = self._validate(body)
        router_id = uuid.uuid4().hex
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM routers WHERE name=?", (fields["name"],)).fetchone():
                raise ValueError("A router with that name already exists")
            db.execute("INSERT INTO routers(id,name,strategy,strong_profile_id,fast_profile_id,threshold,created) VALUES(?,?,?,?,?,?,?)",
                       (router_id, *fields.values(), time.time()))
        return {"id": router_id}

    def update(self, router_id: str, body):
        current = self.get(router_id)
        fields = self._validate(body, {k: current[k] for k in ("name", "strategy", "strong_profile_id", "fast_profile_id", "threshold")})
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM routers WHERE name=? AND id<>?", (fields["name"], router_id)).fetchone():
                raise ValueError("A router with that name already exists")
            db.execute("UPDATE routers SET name=?, strategy=?, strong_profile_id=?, fast_profile_id=?, threshold=? WHERE id=?",
                       (*fields.values(), router_id))
        return {"id": router_id}

    def delete(self, router_id: str):
        self.get(router_id)
        with self.store.connect() as db:
            db.execute("DELETE FROM routers WHERE id=?", (router_id,))
        return {"deleted": router_id}


def choose(router: dict, objective: str, mode: str, follow_up: bool) -> tuple[str, str]:
    """(model profile id, reason) for one turn."""
    strong, fast = router["strong_profile_id"], router["fast_profile_id"]
    if router["strategy"] == "first_turn":
        return (fast, "follow-up message") if follow_up else (strong, "first message")
    if router["strategy"] == "long_message":
        long = len(objective) > (router["threshold"] or 0)
        return (strong, f"message longer than {router['threshold']} characters") if long else (fast, f"message of {router['threshold']} characters or fewer")
    return (strong, "agent turn") if mode == "agent" else (fast, f"{mode} turn")
