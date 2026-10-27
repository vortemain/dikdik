"""Outgoing webhooks are opt-in, keyed, and never break a task."""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server import make_server


class WebhookTests(unittest.TestCase):
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

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def test_create_list_update_delete(self):
        status, body = self.call("POST", "/api/webhooks", {"url": "https://example.com/hook"})
        data = json.loads(body)
        self.assertEqual(status, 201)
        self.assertTrue(data["url"].startswith("https://"))
        wid = data["id"]

        status, body = self.call("GET", "/api/webhooks")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)), 1)

        status, body = self.call("PATCH", f"/api/webhooks/{wid}", {"enabled": False})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["enabled"], 0)

        status, body = self.call("DELETE", f"/api/webhooks/{wid}")
        self.assertEqual(status, 200)
        status, body = self.call("GET", "/api/webhooks")
        self.assertEqual(len(json.loads(body)), 0)

    def test_invalid_url_refused(self):
        status, body = self.call("POST", "/api/webhooks", {"url": "ftp://bad"})
        self.assertEqual(status, 400)

    def test_fires_on_terminal_state(self):
        received = []

        class CatchHandler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received.append({"body": self.rfile.read(length), "event": self.headers.get("X-Dikdik-Event"),
                                 "signature": self.headers.get("X-Dikdik-Signature")})
                self.send_response(200)
                self.end_headers()

        catch = HTTPServer(("127.0.0.1", 0), CatchHandler)
        port = catch.server_port
        threading.Thread(target=catch.serve_forever, daemon=True).start()
        self.addCleanup(catch.shutdown)
        self.addCleanup(catch.server_close)

        self.call("POST", "/api/webhooks", {"url": f"http://127.0.0.1:{port}/hook"})

        # Submit and complete a task
        status, body = self.call("POST", "/api/tasks", {
            "objective": "Change x", "mode": "offline", "verification": "syntax",
            "approve_external": False, "approve_execution": False,
            "edits": [{"path": "app.py", "before": "1", "after": "2"}]})
        task = json.loads(body)["id"]

        # Wait for the webhook to fire
        import time
        for _ in range(30):
            if received:
                break
            time.sleep(0.5)

        self.assertTrue(received)
        self.assertEqual(received[0]["event"], "completed")
        payload = json.loads(received[0]["body"])
        self.assertEqual(payload["task"], task)
        self.assertEqual(payload["state"], "completed")

    def test_disabled_webhook_does_not_fire(self):
        received = []

        class CatchHandler2(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                received.append(True)
                self.send_response(200)
                self.end_headers()

        catch = HTTPServer(("127.0.0.1", 0), CatchHandler2)
        port = catch.server_port
        threading.Thread(target=catch.serve_forever, daemon=True).start()
        self.addCleanup(catch.shutdown)
        self.addCleanup(catch.server_close)

        status, body = self.call("POST", "/api/webhooks", {"url": f"http://127.0.0.1:{port}/hook"})
        wid = json.loads(body)["id"]
        self.call("PATCH", f"/api/webhooks/{wid}", {"enabled": False})

        self.call("POST", "/api/tasks", {
            "objective": "Change x", "mode": "offline", "verification": "syntax",
            "approve_external": False, "approve_execution": False,
            "edits": [{"path": "app.py", "before": "1", "after": "2"}]})

        import time
        time.sleep(2)
        self.assertFalse(received)  # disabled webhook did not fire


    def test_signature_is_an_hmac_not_the_secret(self):
        import hashlib, hmac, time
        received = []

        class CatchHandler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                received.append((body, self.headers.get("X-Dikdik-Signature")))
                self.send_response(200)
                self.end_headers()

        catch = HTTPServer(("127.0.0.1", 0), CatchHandler)
        threading.Thread(target=catch.serve_forever, daemon=True).start()
        self.addCleanup(catch.server_close)
        self.addCleanup(catch.shutdown)
        self.assertEqual(self.call("POST", "/api/secrets", {"ref": "hook-secret", "plaintext": "s3cret-value"})[0], 201)
        self.call("POST", "/api/webhooks", {"url": f"http://127.0.0.1:{catch.server_port}/hook", "secret_ref": "hook-secret"})
        self.call("POST", "/api/tasks", {
            "objective": "Change x", "mode": "offline", "verification": "syntax",
            "approve_external": False, "approve_execution": False,
            "edits": [{"path": "app.py", "before": "1", "after": "2"}]})
        for _ in range(30):
            if received:
                break
            time.sleep(0.5)
        body, sent = received[0]
        self.assertNotIn("s3cret-value", sent)
        self.assertEqual(sent, "sha256=" + hmac.new(b"s3cret-value", body, hashlib.sha256).hexdigest())


if __name__ == "__main__":
    unittest.main()