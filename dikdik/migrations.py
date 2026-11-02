"""Versioned, atomic schema migrations for the core state database.

The schema version lives in SQLite's own header (`PRAGMA user_version`). Each migration runs
in one `BEGIN IMMEDIATE` transaction together with its version bump, so a crash or error leaves
the database at the previous version with nothing half-applied. Before an existing database is
upgraded, a consistent copy is written next to it (`<name>.bak-v<old>`) with SQLite's backup
API. A database written by a newer Dikdik is refused rather than opened and damaged.

Version 1 is the schema as first shipped plus every in-place column added before versioning;
its statements are idempotent, so unversioned databases from earlier releases adopt it safely.
Module-owned tables (profiles, automations, MCP servers, ...) still add their columns
idempotently in their own modules.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable


class SchemaTooNew(RuntimeError):
    """The database was written by a newer Dikdik than this one."""


def _v1_core(db: sqlite3.Connection) -> None:
    # One execute() per statement: executescript() would commit the open migration transaction.
    for statement in (
        """CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, created REAL NOT NULL,
               state TEXT NOT NULL, request TEXT NOT NULL, result TEXT)""",
        """CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
               time REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS events_task ON events(task_id, seq)",
        "CREATE INDEX IF NOT EXISTS events_task_time ON events(task_id, time)",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    ):
        db.execute(statement)
    columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
    for name, kind in (("title", "TEXT"), ("pinned", "INTEGER NOT NULL DEFAULT 0"), ("archived", "INTEGER NOT NULL DEFAULT 0"),
                       ("folder", "TEXT"), ("tags", "TEXT"), ("workspace", "TEXT")):
        if name not in columns:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {kind}")  # dikdik-scan: ignore[PY008] constant column names
    # Before projects, a data directory served exactly one folder: its tasks belong there.
    bound = db.execute("SELECT value FROM settings WHERE key='workspace'").fetchone()
    if bound:
        db.execute("UPDATE tasks SET workspace=? WHERE workspace IS NULL", (bound[0],))
    db.execute("CREATE INDEX IF NOT EXISTS tasks_workspace ON tasks(workspace)")


def _v2_requirements(db: sqlite3.Connection) -> None:
    # Traceable requirements per conversation (see dikdik/engineering.py).
    db.execute("""CREATE TABLE IF NOT EXISTS requirements (
        task_id TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
        updated REAL NOT NULL, PRIMARY KEY (task_id, id))""")


Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]
MIGRATIONS: list[Migration] = [
    (1, "core tasks, events and settings", _v1_core),
    (2, "requirements traceability", _v2_requirements),
]
LATEST = MIGRATIONS[-1][0]


def script(text: str) -> Callable[[sqlite3.Connection], None]:
    """A migration step that runs a multi-statement schema script inside the step's transaction
    (sqlite3's executescript() would commit first, so statements run one at a time)."""
    def step(db: sqlite3.Connection) -> None:
        pending = ""
        for line in text.splitlines(keepends=True):
            pending += line
            if sqlite3.complete_statement(pending):
                if pending.strip().rstrip(";").strip():
                    db.execute(pending)
                pending = ""
        if pending.strip():
            raise ValueError("Schema script ends with an incomplete statement")
    return step


def add_column(db: sqlite3.Connection, table: str, column: str, statement: str) -> None:
    """Run a literal `ALTER TABLE ... ADD COLUMN` only if the column is missing, so a baseline
    step can adopt databases that gained some columns before schemas were versioned."""
    if column not in {row[0] for row in db.execute("SELECT name FROM pragma_table_info(?)", (table,))}:
        db.execute(statement)


def ensure(path: Path, module: str, steps: list[Migration]) -> dict:
    """Version one module's own tables (recorded in `schema_versions`), with the same guarantees
    as the core schema: atomic steps, refusal of newer versions, and a backup before an upgrade
    of an already-versioned module. A first adoption (version 0) runs idempotent baseline steps
    on existing tables, so it needs no backup."""
    numbers = [number for number, _, _ in steps]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("Migrations must be numbered 1, 2, 3, ... without gaps")
    latest = numbers[-1]
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("CREATE TABLE IF NOT EXISTS schema_versions (module TEXT PRIMARY KEY, version INTEGER NOT NULL, updated REAL NOT NULL)")
        def current_version() -> int:
            row = db.execute("SELECT version FROM schema_versions WHERE module=?", (module,)).fetchone()
            return row[0] if row else 0
        current = current_version()
        if current > latest:
            raise SchemaTooNew(f"The {module} data was written by a newer Dikdik (schema {current}; this version reads up to "
                               f"{latest}). Update Dikdik instead of opening it with an older release.")
        backup = None
        if 0 < current < latest:
            backup = path.with_name(f"{path.name}.bak-{module}-v{current}")
            with closing(sqlite3.connect(backup)) as copy:
                db.backup(copy)
        applied = []
        for number, name, step in steps:
            if number <= current:
                continue
            db.execute("BEGIN IMMEDIATE")
            try:
                if current_version() >= number:
                    db.execute("ROLLBACK")
                    continue
                step(db)
                db.execute("INSERT INTO schema_versions(module, version, updated) VALUES(?,?,strftime('%s','now')) "
                           "ON CONFLICT(module) DO UPDATE SET version=excluded.version, updated=excluded.updated", (module, number))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            applied.append(name)
        return {"module": module, "from": current, "to": max(current, latest), "applied": applied,
                "backup": str(backup) if backup else None}
    finally:
        db.close()


def module_versions(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_versions'").fetchone():
            return {}
        return dict(db.execute("SELECT module, version FROM schema_versions").fetchall())


def version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def _has_tables(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone() is not None


def migrate(path: Path, migrations: list[Migration] = MIGRATIONS) -> dict:
    """Bring the database at `path` to the newest version. Returns {from, to, applied, backup}."""
    numbers = [number for number, _, _ in migrations]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("Migrations must be numbered 1, 2, 3, ... without gaps")
    latest = numbers[-1]
    db = sqlite3.connect(path, timeout=30, isolation_level=None)  # explicit transactions below
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")  # cannot run inside a transaction
        current = db.execute("PRAGMA user_version").fetchone()[0]
        if current > latest:
            raise SchemaTooNew(f"This data was written by a newer Dikdik (schema {current}; this version reads up to "
                               f"{latest}). Update Dikdik instead of opening it with an older release.")
        backup = None
        if current < latest and _has_tables(db):
            backup = path.with_name(f"{path.name}.bak-v{current}")
            with closing(sqlite3.connect(backup)) as copy:
                db.backup(copy)
        applied = []
        for number, name, step in migrations:
            if number <= current:
                continue
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("PRAGMA user_version").fetchone()[0] >= number:
                    db.execute("ROLLBACK")  # another process migrated meanwhile
                    continue
                step(db)
                db.execute(f"PRAGMA user_version={int(number)}")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            applied.append(name)
        return {"from": current, "to": max(current, latest), "applied": applied, "backup": str(backup) if backup else None}
    finally:
        db.close()
