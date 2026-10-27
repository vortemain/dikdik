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

    def test_check_contacts_the_backend(self):
        """A health check must really ask the server: a live Dikdik is ok, a dead port is not."""
        live = json.loads(self.call("POST", "/api/backends", {"name": "self", "url": f"http://127.0.0.1:{self.server.server_port}"})[1])
        status, body = self.call("POST", f"/api/backends/{live['id']}/check")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["last_ok"], 1)
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        dead = json.loads(self.call("POST", "/api/backends", {"name": "dead", "url": f"http://127.0.0.1:{dead_port}"})[1])
        data = json.loads(self.call("POST", f"/api/backends/{dead['id']}/check")[1])
        self.assertEqual(data["last_ok"], 0)
        self.assertEqual(data["detail"], "It did not answer")
        self.assertIsNotNone(data["last_checked"])

    def test_check_unknown_404(self):
        self.assertEqual(self.call("POST", "/api/backends/nope/check")[0], 404)

    def test_delete_unknown_404(self):
        status, body = self.call("DELETE", "/api/backends/nonexistent")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()