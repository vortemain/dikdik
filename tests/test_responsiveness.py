"""Long command histories must not slow down unrelated UI/control requests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine
from dikdik.repository import workspace_text
from dikdik.store import Store


class StoreResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite3")
        self.store.bind_workspace(self.root / "project")
        self.task = self.store.create({"objective": "Inspect project", "mode": "agent"})
        self.store.state(self.task, "completed", {"summary": "Ready"})
        with self.store.connect() as db:
            db.executemany("INSERT INTO events(task_id,time,kind,data) VALUES(?,?,?,?)",
                           [(self.task, n, "terminal_output", json.dumps({"output": "x" * 4096})) for n in range(100)])

    def test_status_does_not_decode_request_result_or_history(self):
        with patch("dikdik.store.json.loads", side_effect=AssertionError("Unneeded JSON decode")):
            self.assertEqual(self.store.status(self.task), "completed")

    def test_metadata_reads_only_request_and_result(self):
        complete = self.store.get(self.task)
        with patch("dikdik.store.json.loads", wraps=json.loads) as decode:
            metadata = self.store.get(self.task, include_events=False)
        self.assertEqual(decode.call_count, 2)
        self.assertNotIn("events", metadata)
        self.assertEqual(metadata, {key: value for key, value in complete.items() if key != "events"})
        self.assertEqual(len(complete["events"]), 102)

    def test_lightweight_reads_keep_project_isolation(self):
        self.store.bind_workspace(self.root / "another-project")
        for read in (self.store.status, lambda task: self.store.get(task, include_events=False)):
            with self.subTest(read=read), self.assertRaises(KeyError):
                read(self.task)

    def test_sidebar_activity_uses_covering_index_and_preserves_max_time(self):
        with self.store.connect() as db:
            plan = db.execute("EXPLAIN QUERY PLAN SELECT MAX(time) FROM events WHERE task_id=?", (self.task,)).fetchall()
            db.execute("UPDATE events SET time=3000000000 WHERE task_id=? AND kind='completed'", (self.task,))
        self.assertTrue(any("COVERING INDEX events_task_time" in row["detail"] for row in plan), plan)
        self.assertEqual(self.store.list()[0]["updated"], 3000000000)
        # Indexes also migrate into an existing database without changing its history.
        reopened = Store(self.store.path)
        self.assertEqual(len(reopened.get(self.task)["events"]), 102)


class ControlResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "project"
        root.mkdir()
        self.engine = Engine(root, base / "data")
        self.addCleanup(self.engine.close)
        self.task = self.engine.store.create({"objective": "Inspect project", "mode": "agent"})
        self.engine.store.state(self.task, "completed")

    def test_follow_up_acceptance_does_not_fetch_full_history(self):
        request = {"objective": "Continue", "mode": "agent", "verification": "syntax", "approve_external": True}
        with patch.object(self.engine.store, "get", side_effect=AssertionError("History on request path")), \
                patch.object(self.engine, "_start") as start:
            self.assertFalse(self.engine.send(self.task, request))
        start.assert_called_once_with(self.task, request, follow_up=True)

    def test_planner_and_progress_navigation_do_not_fetch_full_history(self):
        with patch.object(self.engine.store, "get", side_effect=AssertionError("History on navigation path")):
            self.assertIsNone(self.engine.planning.get(self.task)["planner_id"])
            self.assertEqual(self.engine.planning.progress(self.task)["tasks"], [])


class EditorResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_bytes(b"value = 1\n")

    def test_opening_one_file_does_not_inventory_the_project(self):
        with patch("dikdik.repository._inventory_python", side_effect=AssertionError("Full project scan")):
            self.assertEqual(workspace_text(self.root, "src/app.py")["text"], "value = 1\n")

    def test_direct_preview_checks_updated_ancestor_ignore_rules(self):
        self.assertEqual(workspace_text(self.root, "src/app.py")["text"], "value = 1\n")
        (self.root / "src" / ".gitignore").write_bytes(b"app.py\n")
        with self.assertRaises(KeyError):
            workspace_text(self.root, "src/app.py")
        (self.root / "src" / ".gitignore").unlink()
        (self.root / ".dikdikignore").write_bytes(b"src/\n")
        with self.assertRaises(KeyError):
            workspace_text(self.root, "src/app.py")


if __name__ == "__main__":
    unittest.main()
