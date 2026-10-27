"""Remote backend connections: CRUD with URL and key validation."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server import make_server


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "repo"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.engine = Engine(root, base / "data")
        self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "test-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def test_create_list_delete(self):
        status, body = self.call("POST", "/api/backends", {"name": "Remote", "url": "https://remote.example"})
        data = json.loads(body)
        self.assertEqual(status, 201)
        self.assertEqual(data["name"], "Remote")
        bid = data["id"]

        status, body = self.call("GET", "/api/backends")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)), 1)

        status, body = self.call("DELETE", f"/api/backends/{bid}")
        self.assertEqual(status, 200)

        status, body = self.call("GET", "/api/backends")
        self.assertEqual(len(json.loads(body)), 0)

    def test_invalid_url_refused(self):
        status, body = self.call("POST", "/api/backends", {"name": "bad", "url": "ftp://bad"})
        self.assertEqual(status, 400)

    def test_missing_name_refused(self):
        status, body = self.call("POST", "/api/backends", {"url": "https://ok.example"})
        self.assertEqual(status, 400)

    def test_update_backend(self):
        status, body = self.call("POST", "/api/backends", {"name": "Old", "url": "https://old.example"})
        bid = json.loads(body)["id"]
        status, body = self.call("PATCH", f"/api/backends/{bid}", {"name": "New", "enabled": False})
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], "New")
        self.assertEqual(data["enabled"], 0)

    def test_delete_unknown_404(self):
        status, body = self.call("DELETE", "/api/backends/nonexistent")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()