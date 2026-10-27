"""Events search/count, Git diff by commit, and file upload are keyed and bounded."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server import make_server


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "test-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def make_task(self):
        status, body = self.call("POST", "/api/tasks", {
            "objective": "Change x", "mode": "offline", "verification": "syntax",
            "approve_external": False, "approve_execution": False,
            "edits": [{"path": "app.py", "before": "1", "after": "2"}]})
        task = json.loads(body)["id"]
        # Wait for the task to reach a terminal state
        import time
        for _ in range(60):
            s, b = self.call("GET", f"/api/tasks/{task}")
            state = json.loads(b)["state"]
            if state in {"completed", "failed", "blocked", "cancelled", "interrupted"}:
                break
            time.sleep(0.3)
        return task

    def test_events_search_by_kind(self):
        task = self.make_task()
        self.call("POST", f"/api/tasks/{task}/apply", {"confirm": True})
        status, body = self.call("GET", f"/api/tasks/{task}/events?kind=inspected")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "completed")
        self.assertTrue(any(e["kind"] == "inspected" for e in data["events"]))

    def test_events_search_all(self):
        task = self.make_task()
        status, body = self.call("GET", f"/api/tasks/{task}/events")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertGreater(len(data["events"]), 0)

    def test_events_count(self):
        task = self.make_task()
        status, body = self.call("GET", f"/api/tasks/{task}/events/count")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertGreater(data["total"], 0)
        self.assertIn("by_kind", data)
        self.assertIn("inspected", data["by_kind"])

    def test_events_search_unknown_task_404(self):
        self.assertEqual(self.call("GET", "/api/tasks/missing/events")[0], 404)

    def test_events_batch(self):
        self.make_task()
        status, body = self.call("GET", "/api/events/batch")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertGreater(len(data["events"]), 0)
        self.assertIn("task", data["events"][0])
        self.assertIn("kind", data["events"][0])

    def test_events_batch_with_limit(self):
        self.make_task()
        status, body = self.call("GET", "/api/events/batch?limit=2")
        data = json.loads(body)
        self.assertLessEqual(len(data["events"]), 2)

    def test_git_diff_by_commit(self):
        import subprocess
        subprocess.run(["git", "init"], cwd=self.root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.root, capture_output=True)
        (self.root / "app.py").write_text("value = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "bump"], cwd=self.root, capture_output=True)
        status, body = self.call("GET", "/api/git")
        commits = json.loads(body)["commits"]
        self.assertGreaterEqual(len(commits), 1)
        head = commits[0]["hash"]
        status, body = self.call("GET", f"/api/git/{head}/diff")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("value = 2", data["diff"])

    def test_git_diff_unknown_commit(self):
        import subprocess
        subprocess.run(["git", "init"], cwd=self.root, capture_output=True, check=True)
        status, body = self.call("GET", "/api/git/does-not-exist/diff")
        self.assertEqual(status, 400)

    def test_file_upload(self):
        status, body = self.call("POST", "/api/files/upload", {
            "path": "uploaded.txt", "content": "hello world"})
        data = json.loads(body)
        self.assertEqual(status, 201)
        self.assertEqual(data["path"], "uploaded.txt")
        self.assertEqual((self.root / "uploaded.txt").read_text(encoding="utf-8"), "hello world")

    def test_file_upload_refuses_escape(self):
        status, body = self.call("POST", "/api/files/upload", {
            "path": "../../escape.txt", "content": "bad"})
        self.assertEqual(status, 400)

    def test_file_upload_refuses_missing_key(self):
        self.assertEqual(self.call("POST", "/api/files/upload", {"path": "x"})[0], 400)

    def test_file_download(self):
        (self.root / "app.py").write_text("value = 2\n", encoding="utf-8")
        status, body = self.call("GET", "/api/files/download?path=app.py")
        self.assertEqual(status, 200)
        self.assertIn(b"value = 2", body)

    def test_file_download_missing_file(self):
        self.assertEqual(self.call("GET", "/api/files/download?path=absent.py")[0], 404)

    def test_file_download_refuses_escape(self):
        status, body = self.call("GET", "/api/files/download?path=../../escape.txt")
        self.assertEqual(status, 400)

    def test_files_list(self):
        status, body = self.call("GET", "/api/files/list?path=.")
        data = json.loads(body)
        self.assertEqual(status, 200)
        names = [e["name"] for e in data["entries"]]
        self.assertIn("app.py", names)

    def test_git_search(self):
        import subprocess
        subprocess.run(["git", "init"], cwd=self.root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.root, capture_output=True)
        (self.root / "app.py").write_text("value = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "bump value"], cwd=self.root, capture_output=True)
        status, body = self.call("GET", "/api/git/search?q=bump")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["results"]), 1)
        self.assertIn("bump", data["results"][0]["subject"])

    def test_conversation_export(self):
        task = self.make_task()
        status, body = self.call("GET", f"/api/tasks/{task}/export")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["id"], task)
        self.assertIn("events", data)

    def test_settings_schema(self):
        status, body = self.call("GET", "/api/settings/schema")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("properties", data)
        self.assertIn("confirmation", data["properties"])
        self.assertIn("defaults", data)
        self.assertIn("event_retention_seconds", data["properties"])

    def test_file_delete(self):
        (self.root / "to-delete.txt").write_text("delete me", encoding="utf-8")
        status, body = self.call("POST", "/api/files/delete", {"path": "to-delete.txt"})
        self.assertEqual(status, 200)
        self.assertFalse((self.root / "to-delete.txt").exists())

    def test_file_delete_missing(self):
        status, body = self.call("POST", "/api/files/delete", {"path": "absent.txt"})
        self.assertEqual(status, 404)

    def test_file_delete_refuses_escape(self):
        status, body = self.call("POST", "/api/files/delete", {"path": "../../escape"})
        self.assertEqual(status, 400)

    def test_batch_archive_and_delete(self):
        task1 = self.make_task()
        task2 = self.make_task()
        # Archive both
        status, body = self.call("POST", "/api/tasks/batch", {"action": "archive", "ids": [task1, task2]})
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["updated"], 2)
        # Verify archived
        t1 = json.loads(self.call("GET", f"/api/tasks/{task1}")[1])
        self.assertTrue(t1["archived"])
        # Delete both
        status, body = self.call("POST", "/api/tasks/batch", {"action": "delete", "ids": [task1, task2]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["updated"], 2)
        # Verify deleted
        self.assertEqual(self.call("GET", f"/api/tasks/{task1}")[0], 404)

    def test_batch_invalid_action(self):
        status, body = self.call("POST", "/api/tasks/batch", {"action": "bad", "ids": ["x"]})
        self.assertEqual(status, 400)

    def test_batch_unknown_ids_skipped(self):
        status, body = self.call("POST", "/api/tasks/batch", {"action": "pin", "ids": ["nonexistent"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["updated"], 0)


if __name__ == "__main__":
    unittest.main()