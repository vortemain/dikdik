"""Schema migrations are versioned, atomic, backed up and refuse newer databases."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from dikdik import migrations
from dikdik.migrations import LATEST, MIGRATIONS, SchemaTooNew, migrate, version
from dikdik.store import Store


def legacy_database(path: Path) -> None:
    """The v0.2.0-era shape: no conversation metadata columns and no schema version."""
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE tasks (id TEXT PRIMARY KEY, created REAL NOT NULL, state TEXT NOT NULL, request TEXT NOT NULL, result TEXT);
            CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, time REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings VALUES ('workspace', 'C:/old/project');
            INSERT INTO tasks VALUES ('t1', 1.0, 'completed', '{"objective": "x"}', '{}');
            INSERT INTO events (task_id, time, kind, data) VALUES ('t1', 1.0, 'inspected', '{}');
        """)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"

    def test_fresh_database_reaches_latest_without_a_backup(self):
        report = migrate(self.path)
        self.assertEqual((report["from"], report["to"], report["backup"]), (0, LATEST, None))
        self.assertEqual(version(self.path), LATEST)
        self.assertEqual(migrate(self.path)["applied"], [])  # idempotent

    def test_legacy_database_keeps_its_data_and_is_backed_up(self):
        legacy_database(self.path)
        store = Store(self.path)
        self.assertEqual(store.schema["from"], 0)
        self.assertEqual(version(self.path), LATEST)
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute("SELECT id, state, workspace, pinned FROM tasks").fetchone()
            self.assertEqual(row, ("t1", "completed", "C:/old/project", 0))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        backup = Path(store.schema["backup"])
        self.assertEqual(backup.name, "state.sqlite3.bak-v0")
        with closing(sqlite3.connect(backup)) as db, db:  # the copy is the untouched pre-upgrade database
            self.assertNotIn("workspace", {r[1] for r in db.execute("PRAGMA table_info(tasks)")})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_a_newer_database_is_refused_untouched(self):
        migrate(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(f"PRAGMA user_version={LATEST + 1}")
        before = self.path.read_bytes()
        with self.assertRaises(SchemaTooNew):
            Store(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_failing_migration_rolls_back_completely(self):
        migrate(self.path, MIGRATIONS[:1])
        def broken(db):
            db.execute("CREATE TABLE half_done (x)")
            raise RuntimeError("disk full")
        with self.assertRaises(RuntimeError):
            migrate(self.path, MIGRATIONS[:1] + [(2, "broken", broken)])
        self.assertEqual(version(self.path), 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='half_done'").fetchone())
        self.assertEqual(migrate(self.path)["to"], LATEST)  # the real migration still applies afterwards

    def test_migrations_must_be_contiguous(self):
        with self.assertRaises(ValueError):
            migrate(self.path, [(1, "a", lambda db: None), (3, "c", lambda db: None)])

    def test_every_migration_is_idempotent_on_its_own_output(self):
        # v1 adopts unversioned databases by re-running; it must not fail on an already-current schema.
        migrate(self.path)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db, db:
            for _, _, step in MIGRATIONS:
                db.execute("BEGIN")
                step(db)
                db.execute("ROLLBACK")
        self.assertTrue(callable(migrations._v1_core))


class ModuleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"
        migrate(self.path)

    def test_baseline_adopts_a_pre_versioning_table_and_keeps_rows(self):
        from dikdik.routing import STEPS
        with closing(sqlite3.connect(self.path)) as db, db:  # a router table from before the extra columns
            db.execute("CREATE TABLE routers (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, strategy TEXT NOT NULL, "
                       "strong_profile_id TEXT NOT NULL, fast_profile_id TEXT NOT NULL, threshold INTEGER, created REAL NOT NULL)")
            db.execute("INSERT INTO routers VALUES ('r', 'n', 'length', 'a', 'b', 10, 0)")
        report = migrations.ensure(self.path, "routers", STEPS)
        self.assertEqual((report["from"], report["to"], report["backup"]), (0, 1, None))
        with closing(sqlite3.connect(self.path)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(routers)")}
            self.assertTrue({"prompt_template", "model_table"} <= columns)
            self.assertEqual(db.execute("SELECT name FROM routers").fetchone()[0], "n")
        self.assertEqual(migrations.module_versions(self.path)["routers"], 1)
        self.assertEqual(migrations.ensure(self.path, "routers", STEPS)["applied"], [])

    def test_upgrade_is_atomic_backed_up_and_newer_versions_are_refused(self):
        v1 = [(1, "create", migrations.script("CREATE TABLE notes (id TEXT PRIMARY KEY);"))]
        migrations.ensure(self.path, "notes", v1)
        def broken(db):
            db.execute("ALTER TABLE notes ADD COLUMN body TEXT")
            raise RuntimeError("interrupted")
        with self.assertRaises(RuntimeError):
            migrations.ensure(self.path, "notes", v1 + [(2, "broken", broken)])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertNotIn("body", {row[1] for row in db.execute("PRAGMA table_info(notes)")})  # rolled back
        self.assertEqual(migrations.module_versions(self.path)["notes"], 1)
        v2 = v1 + [(2, "body", lambda db: db.execute("ALTER TABLE notes ADD COLUMN body TEXT"))]
        report = migrations.ensure(self.path, "notes", v2)
        self.assertEqual((report["from"], report["to"]), (1, 2))
        self.assertTrue(Path(report["backup"]).name.endswith(".bak-notes-v1"))
        with self.assertRaises(SchemaTooNew):
            migrations.ensure(self.path, "notes", v1)  # an older Dikdik must not touch a v2 module

    def test_script_steps_reject_incomplete_sql(self):
        with self.assertRaises(ValueError):
            migrations.ensure(self.path, "bad", [(1, "bad", migrations.script("CREATE TABLE x (a"))])
        self.assertNotIn("bad", migrations.module_versions(self.path))

    def test_the_engine_versions_every_module_it_owns(self):
        from dikdik.engine import Engine
        root = Path(self.temp.name) / "repo"
        root.mkdir()
        engine = Engine(root, Path(self.temp.name) / "data")
        try:
            from dikdik.server_routes import build_routes
            build_routes(engine)  # route-owned stores (profiles, drafts, connections, skills)
            versions = migrations.module_versions(engine.store.path)
        finally:
            engine.close()
        expected = {"agent_profiles", "automations", "mcp_servers", "profiles", "routers", "acp_agents", "backends",
                    "webhooks", "planning", "connections", "marketplaces", "composer_drafts", "attachments", "engine",
                    "bash_events", "browser_snapshots"}
        self.assertTrue(expected <= set(versions), expected - set(versions))
        self.assertTrue(all(version >= 1 for version in versions.values()))


if __name__ == "__main__":
    unittest.main()
