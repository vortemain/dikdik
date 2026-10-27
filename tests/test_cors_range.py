"""CORS for configured origins and static file range requests."""

import http.client
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine
from dikdik.server import make_server


class CorsRangeTests(unittest.TestCase):
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

    def call(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        h = {"Authorization": "Bearer test-token"}
        if headers:
            h.update(headers)
        conn.request(method, path, headers=h)
        r = conn.getresponse()
        body = r.read()
        resp_headers = dict(r.getheaders())
        conn.close()
        return r.status, body, resp_headers

    def test_options_without_cors_config(self):
        status, _, _ = self.call("OPTIONS", "/api/tasks")
        self.assertEqual(status, 403)

    def test_cors_not_active_for_unconfigured_origin(self):
        status, body, headers = self.call("GET", "/api/tasks", {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_range_request_static_file(self):
        status, body, headers = self.call("GET", "/app.js", {"Range": "bytes=0-9"})
        self.assertEqual(status, 206)
        self.assertTrue(headers.get("Content-Range", "").startswith("bytes 0-9/"))
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(len(body), 10)

    def test_range_full_file(self):
        status, body, headers = self.call("GET", "/app.js", {"Range": "bytes=0-"})
        self.assertEqual(status, 206)

    def test_range_out_of_bounds(self):
        status, body, headers = self.call("GET", "/app.js", {"Range": "bytes=999999-"})
        self.assertEqual(status, 416)

    def test_no_range_returns_full_file(self):
        status, body, headers = self.call("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Range", headers)


class CorsConfiguredTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "repo"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.engine = Engine(root, base / "data")
        self.addCleanup(self.engine.close)
        self._patch = patch.dict(os.environ, {"DIKDIK_CORS_ORIGINS": "http://allowed.example,http://also.allowed"})
        self._patch.start()
        self.addCleanup(self._patch.stop)
        # Reimport to pick up the CORS_ORIGINS
        import importlib
        import dikdik.server as svr
        importlib.reload(svr)
        self.server = svr.make_server(self.engine, 0, "test-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        h = {"Authorization": "Bearer test-token"}
        if headers:
            h.update(headers)
        conn.request(method, path, headers=h)
        r = conn.getresponse()
        body = r.read()
        resp_headers = dict(r.getheaders())
        conn.close()
        return r.status, body, resp_headers

    def test_options_with_cors_config(self):
        status, _, headers = self.call("OPTIONS", "/api/tasks", {"Origin": "http://allowed.example"})
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "http://allowed.example")

    def test_cors_origin_allowed(self):
        # With CORS configured, a request from an allowed origin should get CORS headers
        # but still needs auth for API routes
        status, _, headers = self.call("GET", "/alive", {"Origin": "http://allowed.example"})
        self.assertEqual(status, 200)

    def test_cors_origin_not_allowed(self):
        status, _, _ = self.call("OPTIONS", "/api/tasks", {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()