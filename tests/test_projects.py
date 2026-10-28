"""Projects: one server, many folders; conversations stay with their own folder."""

import http.client
import json
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote
from pathlib import Path

from dikdik.engine import Engine
from dikdik.projects import check_clone_url, list_folders, pick_folder
from dikdik.server import make_server
from dikdik.store import Store


def edit_request(before="1", after="2"):
    return {"objective": "Change x", "mode": "offline", "verification": "syntax",
            "edits": [{"path": "app.py", "before": before, "after": after}]}


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.a, self.b = self.base / "alpha", self.base / "beta"
        for root, value in ((self.a, "1"), (self.b, "1")):
            root.mkdir()
            (root / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
        self.data = self.base / "data"
        self.server = make_server(Engine(self.a, self.data), 0, "test-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: self.server.projects.engine.close())
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=60)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Authorization": "Bearer test-token", "Content-Type": "application/json"})
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, json.loads(raw) if raw else None

    def finished_task(self):
        task = self.call("POST", "/api/tasks", edit_request())[1]["id"]
        for _ in range(100):
            if self.call("GET", f"/api/tasks/{task}")[1]["state"] not in {"queued", "running"}:
                return task
            time.sleep(0.1)
        self.fail("task did not finish")

    def test_folder_browser_lists_directories_without_switching_projects(self):
        status, listing = self.call("GET", "/api/projects/folders?path=" + quote(str(self.base)))
        self.assertEqual(status, 200)
        self.assertEqual({item["name"] for item in listing["folders"]}, {"alpha", "beta", "data"})
        self.assertEqual(listing["path"], str(self.base.resolve()))
        self.assertEqual(self.call("GET", "/api/projects")[1]["current"]["name"], "alpha")
        self.assertEqual(self.call("GET", "/api/projects/folders")[1]["path"], str(self.base.resolve()))
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/projects/folders")
        self.assertEqual(conn.getresponse().status, 401)

    def test_switching_keeps_each_projects_conversations_apart(self):
        task = self.finished_task()
        self.assertEqual(self.call("GET", "/api/projects")[1]["current"]["name"], "alpha")
        status, described = self.call("POST", "/api/projects/open", {"path": str(self.b)})
        self.assertEqual(status, 200)
        self.assertEqual(described["current"]["path"], str(self.b.resolve()))
        self.assertEqual({p["name"] for p in described["recent"]}, {"alpha", "beta"})
        self.assertEqual(self.call("GET", "/api/project")[1]["name"], "beta")
        self.assertEqual(self.call("GET", "/api/tasks")[1], [])
        self.assertEqual(self.call("GET", f"/api/tasks/{task}")[0], 404)
        # Alpha's candidate can never be written into beta.
        self.assertEqual(self.call("POST", f"/api/tasks/{task}/apply", {"confirm": True})[0], 404)
        self.assertEqual((self.b / "app.py").read_text(encoding="utf-8"), "value = 1\n")
        self.call("POST", "/api/projects/open", {"path": str(self.a)})
        self.assertEqual([t["id"] for t in self.call("GET", "/api/tasks")[1]], [task])

    def test_keys_and_settings_are_shared_across_projects(self):
        self.assertEqual(self.call("POST", "/api/secrets", {"ref": "shared-key", "plaintext": "value-123"})[0], 201)
        self.call("PATCH", "/api/settings/app", {"sounds": True})
        self.call("POST", "/api/projects/open", {"path": str(self.b)})
        self.assertIn("shared-key", [item["ref"] for item in self.call("GET", "/api/secrets")[1]])
        self.assertTrue(self.call("GET", "/api/settings/app")[1]["sounds"])

    def test_no_switch_while_a_task_runs(self):
        engine = self.server.projects.engine
        with engine.lock:
            engine.active["busy"] = threading.Event()
        try:
            status, body = self.call("POST", "/api/projects/open", {"path": str(self.b)})
        finally:
            with engine.lock:
                engine.active.pop("busy")
        self.assertEqual(status, 400)
        self.assertIn("still running", body["error"])
        self.assertEqual(self.call("GET", "/api/projects")[1]["current"]["name"], "alpha")

    def test_bad_folders_are_refused_and_the_project_stays_open(self):
        for path in (str(self.base / "missing"), str(self.data), str(self.base), ""):
            with self.subTest(path=path):
                self.assertEqual(self.call("POST", "/api/projects/open", {"path": path})[0], 400)
        self.assertEqual(self.call("GET", "/api/projects")[1]["current"]["name"], "alpha")
        self.assertEqual(self.call("GET", "/api/tasks")[0], 200)

    def test_new_project_with_git(self):
        status, described = self.call("POST", "/api/projects/new", {"parent": str(self.base), "name": "fresh", "git": True})
        self.assertEqual(status, 201)
        self.assertEqual(described["current"]["name"], "fresh")
        self.assertTrue((self.base / "fresh" / ".git").is_dir())
        self.assertEqual(self.call("POST", "/api/projects/new", {"parent": str(self.base), "name": "fresh"})[0], 400)
        self.assertEqual(self.call("POST", "/api/projects/new", {"parent": str(self.base), "name": "../escape"})[0], 400)

    def test_clone_a_repository(self):
        source = self.base / "source"
        source.mkdir()
        (source / "readme.md").write_text("hello\n", encoding="utf-8")
        git = ["git", "-c", "user.name=T", "-c", "user.email=t@t", "-C", str(source)]
        subprocess.run(["git", "init", "--quiet", str(source)], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "--quiet", "-m", "first"], check=True)
        target_parent = self.base / "clones"
        target_parent.mkdir()
        status, described = self.call("POST", "/api/projects/clone", {"url": source.as_uri(), "parent": str(target_parent)})
        self.assertEqual(status, 201, described)
        self.assertEqual(described["current"]["name"], "source")
        self.assertEqual((target_parent / "source" / "readme.md").read_text(encoding="utf-8"), "hello\n")
        status, body = self.call("POST", "/api/projects/clone", {"url": (self.base / "nope").as_uri(), "parent": str(target_parent)})
        self.assertEqual(status, 400)
        self.assertTrue(body["error"].startswith("Git:"))
        self.assertFalse((target_parent / "nope").exists())

    def test_clone_urls(self):
        for good in ("https://github.com/owner/repo", "git@github.com:owner/repo.git", "ssh://git@host/x.git", "file:///C:/x"):
            self.assertEqual(check_clone_url(good), good)
        for bad in ("http://github.com/x", "https://user:pw@github.com/x", "https://token@github.com/x", "-uhack", "ext::sh -c x", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                check_clone_url(bad)


class FolderChooserTests(unittest.TestCase):
    def test_folders_only_unicode_sorting_and_parent_navigation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("zeta", "Alpha", "café"):
                (root / name).mkdir()
            (root / "file.txt").write_text("not a folder")
            result = list_folders(str(root))
            self.assertEqual([item["name"] for item in result["folders"]], ["Alpha", "café", "zeta"])
            self.assertEqual(result["parent"], str(root.parent))
            empty = list_folders(str(root / "café"))
            self.assertEqual((empty["folders"], empty["truncated"]), ([], False))
            self.assertIn(str(Path.home()), [item["path"] for item in empty["locations"]])
            self.assertIsNone(list_folders(root.anchor)["parent"])
            with patch("dikdik.projects.FOLDER_LIMIT", 2):
                limited = list_folders(str(root))
            self.assertTrue(limited["truncated"])
            self.assertEqual(len(limited["folders"]), 2)

    def test_bad_paths_and_access_failures_are_actionable(self):
        for path in (None, "", "relative/folder", "nul\0path", "x" * 1001):
            with self.subTest(path=path), self.assertRaises(ValueError):
                list_folders(path)
        with tempfile.TemporaryDirectory() as temp:
            with patch("dikdik.projects.os.scandir", side_effect=PermissionError("private details")):
                with self.assertRaisesRegex(ValueError, "Check the path and your access") as failure:
                    list_folders(temp)
            self.assertNotIn("private details", str(failure.exception))

    def test_native_failure_is_not_reported_as_user_cancellation(self):
        with patch("dikdik.projects.sys.platform", "win32"), patch("dikdik.projects.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "native failure")
            with self.assertRaisesRegex(ValueError, "could not be shown"):
                pick_folder("Elijah's folder")
            self.assertIn("Elijah''s folder", run.call_args.args[0][-1])
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            self.assertIsNone(pick_folder())


class SharedStoreTests(unittest.TestCase):
    def test_older_data_directories_keep_their_conversations(self):
        """A database from before projects (bound to one folder, no workspace column)
        keeps its conversations with that folder."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.sqlite3"
            import sqlite3
            db = sqlite3.connect(path)
            db.executescript("""CREATE TABLE tasks (id TEXT PRIMARY KEY, created REAL NOT NULL, state TEXT NOT NULL, request TEXT NOT NULL, result TEXT);
                                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                                INSERT INTO tasks VALUES('t1', 1, 'completed', '{"objective": "x"}', NULL);""")
            db.execute("INSERT INTO settings VALUES('workspace', ?)", (str(Path("C:\\old")),))
            db.commit()
            db.close()
            store = Store(path)
            store.bind_workspace(Path("C:\\old"))
            self.assertEqual([t["id"] for t in store.list()], ["t1"])
            store.bind_workspace(Path("C:\\new"))
            self.assertEqual(store.list(), [])
            with self.assertRaises(KeyError):
                store.get("t1")


if __name__ == "__main__":
    unittest.main()
