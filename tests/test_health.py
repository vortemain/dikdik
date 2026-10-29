"""Health, readiness and server-info endpoints are unauthenticated and keyed."""

import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from dikdik import __version__
from dikdik.engine import Engine
from dikdik.server import make_server


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "repo"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n")
        self.engine = Engine(root, base / "data")
        self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def call(self, path, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Authorization": "Bearer test-token"} if auth else {}
        try:
            conn.request("GET", path, headers=headers)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def test_alive_and_health_without_key(self):
        for path in ("/alive", "/health"):
            with self.subTest(path=path):
                status, body = self.call(path, auth=False)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {"status": "ok"})

    def test_ready_without_key(self):
        status, body = self.call("/ready", auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ready")

    def test_protected_routes_still_require_auth(self):
        self.assertEqual(self.call("/api/tasks", auth=False)[0], 401)

    def test_server_info_without_key(self):
        status, body = self.call("/server_info", auth=False)
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["version"], __version__)
        self.assertIn("python", data)
        self.assertIn("platform", data)
        self.assertIn("workspace", data)
        self.assertIn("uptime", data)
        self.assertIn("idle_time", data)
        self.assertGreaterEqual(data["uptime"], 0)
        self.assertGreaterEqual(data["idle_time"], 0)

    def test_api_server_info_alias(self):
        status, body = self.call("/api/server-info")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], __version__)

    def test_server_info_does_not_probe_processor_details(self):
        with patch("dikdik.server.platform.platform", side_effect=AssertionError("Blocking CPU probe")):
            status, body = self.call("/api/server-info")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["platform"])

    def test_openapi_document(self):
        for path in ("/openapi.json", "/api/openapi"):
            with self.subTest(path=path):
                status, body = self.call(path, auth=False)
                self.assertEqual(status, 200)
                doc = json.loads(body)
                self.assertEqual(doc["openapi"], "3.1.0")
                self.assertIn("/api/tasks", doc["paths"])
                self.assertIn("/v1/chat/completions", doc["paths"])
                self.assertIn("/alive", doc["paths"])


if __name__ == "__main__":
    unittest.main()
