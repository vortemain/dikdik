"""Event retention purges old events from terminal tasks."""

import time
import unittest
from pathlib import Path
import tempfile

from dikdik.engine import Engine
from dikdik.store import Store


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.store = Store(base / "state.sqlite3")
        self.store.bind_workspace(self.root)

    def make_task(self, state="completed"):
        task = self.store.create({"objective": "test", "mode": "offline", "verification": "syntax", "approve_external": False})
        self.store.state(task, state)
        return task

    def test_purge_removes_old_events_from_terminal_tasks(self):
        task = self.make_task("completed")
        # Insert an old event (2 hours ago)
        with self.store.connect() as db:
            db.execute("UPDATE events SET time=? WHERE task_id=?", (time.time() - 7200, task))
        count_before = self.store.count_events(task)[1]["total"]
        deleted = self.store.purge_old_events(3600)  # keep last 1 hour
        count_after = self.store.count_events(task)[1]["total"]
        self.assertGreater(deleted, 0)
        self.assertLess(count_after, count_before)

    def test_purge_keeps_events_from_active_tasks(self):
        task = self.store.create({"objective": "test", "mode": "offline", "verification": "syntax", "approve_external": False})
        # Task is still "queued" (active), so its events should not be purged
        with self.store.connect() as db:
            db.execute("UPDATE events SET time=? WHERE task_id=?", (time.time() - 7200, task))
        deleted = self.store.purge_old_events(3600)
        self.assertEqual(deleted, 0)

    def test_purge_zero_or_negative_does_nothing(self):
        self.assertEqual(self.store.purge_old_events(0), 0)
        self.assertEqual(self.store.purge_old_events(-1), 0)

    def test_purge_keeps_recent_events(self):
        task = self.make_task("completed")
        # Events are recent (just created), so nothing should be purged
        deleted = self.store.purge_old_events(3600)
        self.assertEqual(deleted, 0)


if __name__ == "__main__":
    unittest.main()