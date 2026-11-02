"""A backup restores to a working data directory; damage and unsafe restores are refused."""

import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

from dikdik import backup
from dikdik.engine import Engine
from dikdik.locking import DataLock
from dikdik.secrets_store import SecretsStore


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "repo").mkdir()
        (self.base / "repo" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.data = self.base / "data"
        engine = Engine(self.base / "repo", self.data)
        self.task = engine.store.create({"objective": "remember me"})
        engine.store.event(self.task, "note", {"text": "kept"})
        engine.close()
        SecretsStore(self.data / "secrets.bin", self.data / "secrets.key").set("provider", b"sk-test-value")
        (self.data / "logs").mkdir(exist_ok=True)
        (self.data / "logs" / "dikdik.jsonl").write_text("{}\n", encoding="utf-8")
        (self.data / "state.sqlite3.bak-v0").write_bytes(b"old copy")

    def test_round_trip_restores_tasks_and_secrets(self):
        archive = self.base / "b.zip"
        made = backup.create(self.data, archive)
        names = zipfile.ZipFile(archive).namelist()
        self.assertIn("data/state.sqlite3", names)
        self.assertFalse(any("logs/" in n or ".bak-" in n or n.endswith("service.lock") for n in names))
        self.assertTrue(backup.verify(archive)["ok"])
        target = self.base / "restored"
        report = backup.restore(archive, target)
        self.assertEqual((report["files"], report["previous_moved_to"]), (made["files"], None))
        with closing(sqlite3.connect(target / "state.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE id=?", (self.task,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT data FROM events WHERE kind='note'").fetchone()[0], '{"text": "kept"}')
        self.assertEqual(SecretsStore(target / "secrets.bin", target / "secrets.key").reveal("provider"), b"sk-test-value")
        engine = Engine(self.base / "repo", target)  # the restored directory runs
        try:
            self.assertEqual(engine.store.get(self.task, include_events=False)["request"]["objective"], "remember me")
        finally:
            engine.close()

    def test_a_backup_taken_while_running_is_consistent(self):
        engine = Engine(self.base / "repo", self.data)
        try:
            engine.store.event(self.task, "note", {"text": "while running"})
            archive = self.base / "live.zip"
            backup.create(self.data, archive)
            self.assertTrue(backup.verify(archive)["ok"])
            with self.assertRaises(ValueError):  # the service holds the lock
                backup.restore(archive, self.data, force=True)
        finally:
            engine.close()

    def test_tampering_and_unsafe_paths_are_detected(self):
        archive = self.base / "b.zip"
        backup.create(self.data, archive)
        tampered = self.base / "t.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
            for item in source.infolist():
                data = source.read(item)
                target.writestr(item, data + b"x" if item.filename == "data/secrets.bin" else data)
        report = backup.verify(tampered)
        self.assertFalse(report["ok"])
        self.assertIn("secrets.bin does not match its recorded hash", report["problems"][0])
        with self.assertRaises(ValueError):
            backup.restore(tampered, self.base / "never")
        self.assertFalse((self.base / "never").exists())
        evil = self.base / "evil.zip"
        with zipfile.ZipFile(evil, "w") as target:
            target.writestr("manifest.json", json.dumps({"format": "dikdik-backup", "version": 1, "files": []}))
            target.writestr("../outside.txt", "x")
        with self.assertRaises(ValueError):
            backup.verify(evil)

    def test_restore_never_merges_and_force_moves_aside(self):
        archive = self.base / "b.zip"
        backup.create(self.data, archive)
        existing = self.base / "existing"
        existing.mkdir()
        (existing / "mine.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            backup.restore(archive, existing)
        report = backup.restore(archive, existing, force=True)
        self.assertEqual((Path(report["previous_moved_to"]) / "mine.txt").read_text(encoding="utf-8"), "keep")
        self.assertTrue((existing / "state.sqlite3").is_file())

    def test_backup_must_be_written_outside_the_data_directory(self):
        with self.assertRaises(ValueError):
            backup.create(self.data, self.data / "inside.zip")

    def test_cli(self):
        archive = self.base / "cli.zip"
        self.assertEqual(backup.main(["create", "--data", str(self.data), "--output", str(archive)]), 0)
        self.assertEqual(backup.main(["verify", str(archive)]), 0)
        self.assertEqual(backup.main(["restore", str(archive), "--data", str(self.data)]), 1)  # not empty


if __name__ == "__main__":
    unittest.main()
