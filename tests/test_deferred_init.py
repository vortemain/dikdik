"""Deferred initialization: dormant server gates API routes until /api/init."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine
from dikdik.server import make_server


class DeferredInitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)

    def start_server(self, dormant=False, init_key=""):
        env = {"DIKDIK_DORMANT": "1"} if dormant else {}
        if init_key:
            env["DIKDIK_INIT_KEY"] = init_key
        with patch.dict(os.environ, env, clear=False):
            from dikdik.server_state import ServerState
            state = ServerState(ready=not dormant)
            # We can't easily inject state into make_server, so test the state directly
            return state

    def test_ready_server_has_ready_state(self):
        state = self.start_server(dormant=False)
        self.assertTrue(state.ready)
        self.assertFalse(state.dormant)

    def test_dormant_server_is_not_ready(self):
        state = self.start_server(dormant=True)
        self.assertFalse(state.ready)
        self.assertTrue(state.dormant)

    def test_initialize_flips_dormant_to_ready(self):
        state = self.start_server(dormant=True)
        result = state.initialize({"workspace": str(self.root)}, self.engine)
        self.assertTrue(result["ready"])
        self.assertTrue(state.ready)

    def test_initialize_twice_refused(self):
        state = self.start_server(dormant=True)
        state.initialize({"workspace": str(self.root)}, self.engine)
        with self.assertRaises(ValueError):
            state.initialize({"workspace": str(self.root)}, self.engine)

    def test_init_key_checked(self):
        state = self.start_server(dormant=True, init_key="secret-init-key")
        with self.assertRaises(ValueError):
            state.initialize({"workspace": str(self.root), "init_key": "wrong"}, self.engine)
        result = state.initialize({"workspace": str(self.root), "init_key": "secret-init-key"}, self.engine)
        self.assertTrue(result["ready"])

    def test_init_wrong_workspace_refused(self):
        state = self.start_server(dormant=True)
        with self.assertRaises(ValueError):
            state.initialize({"workspace": "/different/workspace"}, self.engine)

    def test_info_reports_state(self):
        state = self.start_server(dormant=True)
        info = state.info()
        self.assertFalse(info["ready"])
        self.assertTrue(info["dormant"])
        state.initialize({"workspace": str(self.root)}, self.engine)
        info = state.info()
        self.assertTrue(info["ready"])
        self.assertIn("initialized_at", info)


class DormantHttpTests(unittest.TestCase):
    """A dormant server must refuse every method, not only GET, until /api/init."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        with patch.dict(os.environ, {"DIKDIK_DORMANT": "1"}):
            self.server = make_server(self.engine, 0, "test-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Authorization": "Bearer test-token", "Content-Type": "application/json"})
        response = conn.getresponse()
        response.read()
        conn.close()
        return response.status

    def test_every_method_waits_for_init(self):
        self.assertEqual(self.call("GET", "/api/tasks"), 503)
        self.assertEqual(self.call("POST", "/api/keys", {"name": "x", "scopes": ["read"]}), 503)
        self.assertEqual(self.call("PATCH", "/api/settings/app", {"sounds": True}), 503)
        self.assertEqual(self.call("DELETE", "/api/keys/abc"), 503)
        self.assertEqual(self.call("POST", "/api/init", {"workspace": str(self.root)}), 200)
        self.assertEqual(self.call("GET", "/api/tasks"), 200)


if __name__ == "__main__":
    unittest.main()