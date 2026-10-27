"""ChatGPT subscription login via the OpenAI device flow."""

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server import make_server


class ChatGptLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "repo"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.engine = Engine(root, base / "data")
        # Override the client_id for testing
        self.engine.chatgpt.client_id = "test-client-id"
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

    def test_status_not_connected(self):
        status, body = self.call("GET", "/api/chatgpt/status")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(data["connected"])
        self.assertIsNone(data["account_email"])

    def test_device_start_returns_codes(self):
        status, body = self.call("POST", "/api/chatgpt/device/start")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("device_code", data)
        self.assertIn("user_code", data)
        self.assertIn("verification_uri", data)
        self.assertGreater(data["expires_in"], 0)

    def test_device_poll_pending(self):
        status, body = self.call("POST", "/api/chatgpt/device/start")
        device_code = json.loads(body)["device_code"]
        status, body = self.call("POST", "/api/chatgpt/device/poll", {"device_code": device_code})
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(data["connected"])

    def test_device_poll_unknown_code_404(self):
        status, body = self.call("POST", "/api/chatgpt/device/poll", {"device_code": "unknown"})
        self.assertEqual(status, 404)

    def test_complete_login_and_status(self):
        # Start device flow
        status, body = self.call("POST", "/api/chatgpt/device/start")
        device_code = json.loads(body)["device_code"]
        # Complete the login directly (simulating an OAuth callback)
        self.engine.chatgpt.complete_login(device_code, {
            "access_token": "test-token", "refresh_token": "test-refresh",
            "account_email": "user@example.com", "expires_in": 3600})
        # Status should show connected
        status, body = self.call("GET", "/api/chatgpt/status")
        data = json.loads(body)
        self.assertTrue(data["connected"])
        self.assertEqual(data["account_email"], "user@example.com")
        self.assertIsNotNone(data["expires_at"])

    def test_logout(self):
        status, body = self.call("POST", "/api/chatgpt/device/start")
        device_code = json.loads(body)["device_code"]
        self.engine.chatgpt.complete_login(device_code, {
            "access_token": "t", "refresh_token": "r",
            "account_email": "u@e.com", "expires_in": 3600})
        status, body = self.call("POST", "/api/chatgpt/logout")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(data["connected"])
        # Status should show not connected
        status, body = self.call("GET", "/api/chatgpt/status")
        self.assertFalse(json.loads(body)["connected"])

    def test_device_start_without_client_id_400(self):
        self.engine.chatgpt.client_id = ""
        status, body = self.call("POST", "/api/chatgpt/device/start")
        self.assertEqual(status, 400)

    def test_credentials_persist_across_instances(self):
        status, body = self.call("POST", "/api/chatgpt/device/start")
        device_code = json.loads(body)["device_code"]
        self.engine.chatgpt.complete_login(device_code, {
            "access_token": "t", "refresh_token": "r",
            "account_email": "persist@test.com", "expires_in": 3600})
        # Create a new ChatGptLogin with the same data directory
        from dikdik.chatgpt_login import ChatGptLogin
        new_login = ChatGptLogin(self.engine.data)
        status = new_login.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["account_email"], "persist@test.com")


if __name__ == "__main__":
    unittest.main()