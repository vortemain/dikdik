"""SQLite is authoritative for state and ordered event history."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, created REAL NOT NULL,
                    state TEXT NOT NULL, request TEXT NOT NULL, result TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    time REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_task ON events(task_id, seq);
            """)
            # Conversation metadata, added after the first release: migrate in place.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
            for name, kind in (("title", "TEXT"), ("pinned", "INTEGER NOT NULL DEFAULT 0"), ("archived", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {kind}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def bind_workspace(self, root: Path):
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = db.execute("SELECT value FROM settings WHERE key='workspace'").fetchone()
            if row and row[0] != str(root):
                raise ValueError("Data directory belongs to a different workspace; choose a separate --data directory")
            db.execute("INSERT OR IGNORE INTO settings VALUES('workspace',?)", (str(root),))

    def recover(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM tasks WHERE state IN ('queued','running')")]
            for task in ids:
                db.execute("UPDATE tasks SET state='interrupted', result=? WHERE id=?", (json.dumps({"error": "Service stopped before a terminal result. Re-run as a new task; exact resume is unsupported."}), task))
                db.execute("INSERT INTO events(task_id,time,kind,data) VALUES(?,?,?,?)", (task, time.time(), "interrupted", "{}"))

    def stale_execution_ids(self):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT id,request FROM tasks WHERE state IN ('queued','running')") if json.loads(row[1]).get("verification") == "docker" and json.loads(row[1]).get("approve_execution") is True]

    def create(self, request):
        task = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO tasks(id,created,state,request) VALUES(?,?,?,?)", (task, time.time(), "queued", json.dumps(request)))
            db.execute("INSERT INTO events(task_id,time,kind,data) VALUES(?,?,?,?)", (task, time.time(), "queued", json.dumps({"objective": request["objective"]})))
        return task

    def event(self, task, kind, data):
        with self.connect() as db:
            db.execute("INSERT INTO events(task_id,time,kind,data) VALUES(?,?,?,?)", (task, time.time(), kind, json.dumps(data)))

    def state(self, task, state, result=None):
        with self.connect() as db:
            db.execute("UPDATE tasks SET state=?, result=? WHERE id=?", (state, json.dumps(result) if result is not None else None, task))
            db.execute("INSERT INTO events(task_id,time,kind,data) VALUES(?,?,?,?)", (task, time.time(), state, json.dumps(result or {})))

    def get(self, task):
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task,)).fetchone()
            if row is None:
                raise KeyError(task)
            data = dict(row)
            data["request"] = json.loads(data["request"])
            data["result"] = json.loads(data["result"]) if data["result"] else None
            data["events"] = [{**dict(r), "data": json.loads(r["data"])} for r in db.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq", (task,))]
            return data

    def list(self):
        with self.connect() as db:
            tasks = []
            rows = db.execute("SELECT id,created,state,request,title,pinned,archived,"
                              "(SELECT MAX(time) FROM events WHERE task_id=tasks.id) AS updated "
                              "FROM tasks ORDER BY pinned DESC, updated DESC LIMIT 500")
            for row in rows:
                request = json.loads(row["request"])
                # The objective doubles as the conversation title until it is renamed.
                tasks.append({"id": row["id"], "created": row["created"], "updated": row["updated"] or row["created"],
                              "state": row["state"], "objective": str(request.get("objective", ""))[:200],
                              "title": row["title"] or "", "mode": request.get("mode"),
                              "pinned": bool(row["pinned"]), "archived": bool(row["archived"])})
            return tasks

    def events_after(self, task, seq: int, limit: int = 200):
        """New events for one task, for the live stream."""
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?", (task, seq, limit)).fetchall()
            state = db.execute("SELECT state FROM tasks WHERE id=?", (task,)).fetchone()
        if state is None:
            raise KeyError(task)
        return state["state"], [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def update_meta(self, task, changes: dict):
        """Rename, pin or archive a conversation. Unknown keys and bad types are rejected."""
        if not isinstance(changes, dict) or not changes or set(changes) - {"title", "pinned", "archived"}:
            raise ValueError("Only title, pinned and archived can be changed")
        values = {}
        if "title" in changes:
            title = changes["title"]
            if not isinstance(title, str) or len(title) > 200 or any(ord(c) < 32 for c in title):
                raise ValueError("Title must be at most 200 characters on one line")
            values["title"] = title.strip() or None
        for flag in ("pinned", "archived"):
            if flag in changes:
                if not isinstance(changes[flag], bool):
                    raise ValueError(f"{flag} must be true or false")
                values[flag] = int(changes[flag])
        with self.connect() as db:
            if db.execute("SELECT 1 FROM tasks WHERE id=?", (task,)).fetchone() is None:
                raise KeyError(task)
            for key, value in values.items():
                db.execute(f"UPDATE tasks SET {key}=? WHERE id=?", (value, task))

    def delete(self, task):
        with self.connect() as db:
            if db.execute("DELETE FROM tasks WHERE id=?", (task,)).rowcount == 0:
                raise KeyError(task)
            db.execute("DELETE FROM events WHERE task_id=?", (task,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bash_events'").fetchone():
                db.execute("DELETE FROM bash_events WHERE task_id=?", (task,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='browser_snapshots'").fetchone():
                db.execute("DELETE FROM browser_snapshots WHERE task_id=?", (task,))
