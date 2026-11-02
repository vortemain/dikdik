"""Operational logs are structured JSON lines and never contain secrets."""

import http.client
import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik import logs
from dikdik.engine import Engine
from dikdik.server import make_server

AWS = "AKIA" + "Q3EGJ7RZ5XN2LW4P"


class LogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.path = logs.configure(self.data)
        self.addCleanup(self.detach)

    def detach(self):
        logger = logging.getLogger(logs.LOGGER)
        for handler in list(logger.handlers):
            if getattr(handler, "dikdik_path", None) == str(self.path):
                handler.close()
                logger.removeHandler(handler)
        self.temp.cleanup()

    def lines(self):
        for handler in logging.getLogger(logs.LOGGER).handlers:
            handler.flush()
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_entries_are_json_with_fields(self):
        logs.log("server_started", port=8787, remote=False)
        entry = self.lines()[-1]
        self.assertEqual((entry["event"], entry["level"], entry["port"], entry["remote"]), ("server_started", "info", 8787, False))
        self.assertTrue(entry["ts"].endswith("Z"))

    def test_secrets_are_masked_in_messages_fields_and_tracebacks(self):
        logs.register_secret("owner-session-token-123")
        try:
            raise RuntimeError(f"provider said no for key {AWS} with owner-session-token-123")
        except RuntimeError:
            logs.log("task_failed", logging.ERROR, exc_info=True, task="t1",
                     headers={"Authorization": "Bearer abcdefghijklmnop", "Accept": "json"},
                     note="sent Bearer zyxwvutsrqponmlk upstream", api_key="plain")
        raw = self.path.read_text(encoding="utf-8")
        for secret in (AWS, "owner-session-token-123", "abcdefghijklmnop", "zyxwvutsrqponmlk", '"plain"'):
            self.assertNotIn(secret, raw)
        entry = self.lines()[-1]
        self.assertEqual(entry["headers"], {"Authorization": "[REDACTED]", "Accept": "json"})
        self.assertEqual(entry["api_key"], "[REDACTED]")
        self.assertIn("[REDACTED:SEC001]", entry["exception"])
        self.assertIn("RuntimeError", entry["exception"])

    def test_usage_counts_are_not_mistaken_for_credentials(self):
        logs.log("usage", input_tokens=120, total_tokens=150, access_token="should-hide")
        entry = self.lines()[-1]
        self.assertEqual((entry["input_tokens"], entry["total_tokens"], entry["access_token"]), (120, 150, "[REDACTED]"))

    def test_configure_is_idempotent(self):
        logs.configure(self.data)
        logs.log("once")
        self.assertEqual(sum(e["event"] == "once" for e in self.lines()), 1)

    def test_failed_sign_in_is_logged_without_the_attempted_secret(self):
        (self.data / "repo").mkdir()
        engine = Engine(self.data / "repo", self.data / "state")
        server = make_server(engine, 0, "the-real-owner-token")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            conn.request("POST", "/api/session", body=json.dumps({"token": "guessed-wrong-value"}),
                         headers={"Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 401)
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            engine.close()
        events = [e for e in self.lines() if e["event"] == "sign_in_failed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["route"], "/api/session")
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("guessed-wrong-value", raw)
        self.assertNotIn("the-real-owner-token", raw)


if __name__ == "__main__":
    unittest.main()
