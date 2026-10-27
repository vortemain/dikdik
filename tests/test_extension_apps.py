"""Extension apps: install, list, enable/disable, bundle serving, delete."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server import make_server


def make_app_src(base: Path, name: str = "test-app") -> Path:
    """Create a minimal app source directory with an app.json manifest."""
    src = base / "src" / name
    src.mkdir(parents=True)
    (src / "app.json").write_text(json.dumps({
        "name": name, "version": "1.0.0",
        "description": "A test app", "entrypoint": "index.html"}), encoding="utf-8")
    (src / "index.html").write_text("<html><body>Test app</body></html>", encoding="utf-8")
    return src


class ExtensionAppTests(unittest.TestCase):
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
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.src = make_app_src(base)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def test_install_list_delete(self):
        status, body = self.call("POST", "/api/apps", {"name": "test-app", "source": str(self.src)})
        data = json.loads(body)
        self.assertEqual(status, 201)
        self.assertEqual(data["name"], "test-app")
        self.assertEqual(data["version"], "1.0.0")
        self.assertFalse(data["enabled"])

        status, body = self.call("GET", "/api/apps")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["apps"]), 1)

        status, body = self.call("DELETE", "/api/apps/test-app")
        self.assertEqual(status, 200)

        status, body = self.call("GET", "/api/apps")
        self.assertEqual(len(json.loads(body)["apps"]), 0)

    def test_enable_disable(self):
        self.call("POST", "/api/apps", {"name": "test-app", "source": str(self.src)})
        status, body = self.call("POST", "/api/apps/test-app/enable")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["enabled"])

        status, body = self.call("POST", "/api/apps/test-app/disable")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(data["enabled"])

    def test_bundle_served(self):
        self.call("POST", "/api/apps", {"name": "test-app", "source": str(self.src)})
        status, body = self.call("GET", "/api/apps/test-app/bundle")
        self.assertEqual(status, 200)
        self.assertIn(b"Test app", body)

    def test_install_duplicate_refused(self):
        self.call("POST", "/api/apps", {"name": "test-app", "source": str(self.src)})
        status, body = self.call("POST", "/api/apps", {"name": "test-app", "source": str(self.src)})
        self.assertEqual(status, 400)

    def test_install_duplicate_force(self):
        self.call("POST", "/api/apps", {"name": "test-app", "source": str(self.src)})
        status, body = self.call("POST", "/api/apps", {"name": "test-app", "source": str(self.src), "force": True})
        self.assertEqual(status, 201)

    def test_delete_unknown_404(self):
        status, body = self.call("DELETE", "/api/apps/nonexistent")
        self.assertEqual(status, 404)

    def test_enable_unknown_404(self):
        status, body = self.call("POST", "/api/apps/nonexistent/enable")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()