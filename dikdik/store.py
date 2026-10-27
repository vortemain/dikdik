"""SQLite is authoritative for state and ordered event history."""

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


TAG = re.compile(r"[\w][\w -]{0,31}")


class Store:
    """One database for every project. Each conversation records the project folder it
    belongs to, and a store bound to a folder only ever sees that folder's conversations,
    so a candidate made for one project can never be read, changed or applied in another."""

    def __init__(self, path: Path):
        self.path = path
        self.workspace: str | None = None
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
            for name, kind in (("title", "TEXT"), ("pinned", "INTEGER NOT NULL DEFAULT 0"), ("archived", "INTEGER NOT NULL DEFAULT 0"),
                               ("folder", "TEXT"), ("tags", "TEXT"), ("workspace", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {kind}")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            # Before projects, a data directory served exactly one folder: its tasks belong there.
            bound = db.execute("SELECT value FROM settings WHERE key='workspace'").fetchone()
            if bound:
                db.execute("UPDATE tasks SET workspace=? WHERE workspace IS NULL", (bound[0],))

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
        """Serve one project folder from now on; earlier folders' conversations stay stored."""
        self.workspace = str(root)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO settings VALUES('workspace',?)", (self.workspace,))

    def _mine(self, alias: str = "") -> tuple[str, tuple]:
        """SQL condition (and its parameter) limiting tasks to the bound project."""
        if self.workspace is None:
            return "1=1", ()
        return f"{alias}workspace=?", (self.workspace,)

    def _owned(self, db, task):
        where, params = self._mine()
        row = db.execute(f"SELECT state FROM tasks WHERE id=? AND {where}", (task, *params)).fetchone()
        if row is None:
            raise KeyError(task)
        return row

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
            db.execute("INSERT INTO tasks(id,created,state,request,workspace) VALUES(?,?,?,?,?)",
                       (task, time.time(), "queued", json.dumps(request), self.workspace))
            db.execute("INSERT INTO events(task_id,time,kind,data) VALUES(?,?,?,?)", (task, time.time(), "queued", json.dumps({"objective": request["objective"]})))
        return task

    def event(self, task, kind, data):
        with self.connect() as db:
            db.execute("INSERT INTO events(task_id,time,kind,data) VALUES(?,?,?,?)", (task, time.time(), kind, json.dumps(data)))

    def state(self, task, state, result=None, *, details=None):
        with self.connect() as db:
            db.execute("UPDATE tasks SET state=?, result=? WHERE id=?", (state, json.dumps(result) if result is not None else None, task))
            db.execute("INSERT INTO events(task_id,time,kind,data) VALUES(?,?,?,?)", (task, time.time(), state, json.dumps(details if details is not None else result or {})))

    def get(self, task):
        with self.connect() as db:
            db.execute("BEGIN")
            where, params = self._mine()
            row = db.execute(f"SELECT * FROM tasks WHERE id=? AND {where}", (task, *params)).fetchone()
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
            where, params = self._mine()
            rows = db.execute("SELECT id,created,state,request,title,pinned,archived,folder,tags,"
                              "(SELECT MAX(time) FROM events WHERE task_id=tasks.id) AS updated "
                              f"FROM tasks WHERE {where} ORDER BY pinned DESC, updated DESC LIMIT 500", params)
            for row in rows:
                request = json.loads(row["request"])
                # The objective doubles as the conversation title until it is renamed.
                tasks.append({"id": row["id"], "created": row["created"], "updated": row["updated"] or row["created"],
                              "state": row["state"], "objective": str(request.get("objective", ""))[:200],
                              "title": row["title"] or "", "mode": request.get("mode"),
                              "pinned": bool(row["pinned"]), "archived": bool(row["archived"]),
                              "folder": row["folder"] or "", "tags": json.loads(row["tags"]) if row["tags"] else []})
            return tasks

    def events_after(self, task, seq: int, limit: int = 200):
        """New events for one task, for the live stream."""
        with self.connect() as db:
            state = self._owned(db, task)
            rows = db.execute("SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?", (task, seq, limit)).fetchall()
        return state["state"], [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def search_events(self, task: str, kind: str | None = None, limit: int = 200):
        """Search one task's events by kind, newest first."""
        with self.connect() as db:
            state = self._owned(db, task)
            if kind is not None:
                rows = db.execute("SELECT * FROM events WHERE task_id=? AND kind=? ORDER BY seq DESC LIMIT ?",
                                  (task, kind, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq DESC LIMIT ?",
                                  (task, limit)).fetchall()
            return state["state"], [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def count_events(self, task: str):
        """Count one task's events grouped by kind."""
        with self.connect() as db:
            state = self._owned(db, task)
            counts = {row["kind"]: row["count"] for row in
                      db.execute("SELECT kind, COUNT(*) AS count FROM events WHERE task_id=? GROUP BY kind", (task,))}
            total = db.execute("SELECT COUNT(*) AS n FROM events WHERE task_id=?", (task,)).fetchone()["n"]
        return state["state"], {"total": total, "by_kind": counts}

    def recent_events(self, limit: int = 100):
        """The latest events across all tasks, for a batch overview."""
        where, params = self._mine("t.")
        with self.connect() as db:
            rows = db.execute(
                "SELECT e.task_id, e.seq, e.time, e.kind, e.data "
                f"FROM events e JOIN tasks t ON e.task_id = t.id WHERE {where} "
                "ORDER BY e.time DESC LIMIT ?", (*params, min(limit, 500))).fetchall()
            return [{"task": r["task_id"], "seq": r["seq"], "time": r["time"],
                     "kind": r["kind"], "data": json.loads(r["data"])} for r in rows]

    def purge_old_events(self, max_age_seconds: float) -> int:
        """Delete events older than max_age_seconds from terminal tasks. Returns the count deleted."""
        if not isinstance(max_age_seconds, (int, float)) or max_age_seconds <= 0:
            return 0
        cutoff = time.time() - max_age_seconds
        with self.connect() as db:
            cursor = db.execute(
                "DELETE FROM events WHERE time < ? AND task_id IN "
                "(SELECT id FROM tasks WHERE state IN ('completed','failed','blocked','cancelled','interrupted','archived'))",
                (cutoff,))
            return cursor.rowcount

    def update_meta(self, task, changes: dict):
        """Rename, pin, archive, file or tag a conversation. Unknown keys and bad types are rejected."""
        if not isinstance(changes, dict) or not changes or set(changes) - {"title", "pinned", "archived", "folder", "tags"}:
            raise ValueError("Only title, pinned, archived, folder and tags can be changed")
        values = {}
        if "folder" in changes:
            folder = changes["folder"]
            if folder is not None and (not isinstance(folder, str) or len(folder) > 60 or any(ord(c) < 32 for c in folder)):
                raise ValueError("Folder must be at most 60 characters on one line")
            values["folder"] = (folder or "").strip() or None
        if "tags" in changes:
            tags = changes["tags"]
            if not isinstance(tags, list) or len(tags) > 10 or not all(isinstance(t, str) and TAG.fullmatch(t.strip().lower()) for t in tags):
                raise ValueError("Use up to 10 tags of 1–32 letters, digits, spaces, - or _")
            values["tags"] = json.dumps(sorted({t.strip().lower() for t in tags})) if tags else None
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
            self._owned(db, task)
            for key, value in values.items():
                db.execute(f"UPDATE tasks SET {key}=? WHERE id=?", (value, task))

    def delete(self, task):
        with self.connect() as db:
            self._owned(db, task)
            has_plans = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plans'").fetchone()
            identities = [task, *[row[0] for row in db.execute('SELECT planner_id FROM plans WHERE task_id=?', (task,))]] if has_plans else [task]
            for identity in identities:
                db.execute('DELETE FROM tasks WHERE id=?', (identity,))
                db.execute('DELETE FROM events WHERE task_id=?', (identity,))
                for table in ('bash_events', 'browser_snapshots', 'task_progress'):
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        db.execute(f'DELETE FROM {table} WHERE task_id=?', (identity,))
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='composer_drafts'").fetchone():
                    db.execute('DELETE FROM composer_drafts WHERE scope=?', (identity,))
                if has_plans:
                    db.execute('DELETE FROM plans WHERE task_id=? OR planner_id=?', (identity, identity))
