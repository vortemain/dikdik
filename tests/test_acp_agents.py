"""ACP agent profiles: presets, CRUD, availability and model discovery."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server import make_server


class AcpAgentTests(unittest.TestCase):
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

    def test_presets_listed(self):
        status, body = self.call("GET", "/api/acp-agents")
        data = json.loads(body)
        self.assertEqual(status, 200)
        presets = {p["id"] for p in data["presets"]}
        self.assertIn("claude-code", presets)
        self.assertIn("codex", presets)
        self.assertIn("gemini-cli", presets)

    def test_create_list_delete(self):
        status, body = self.call("POST", "/api/acp-agents", {
            "name": "My Claude", "command": "claude", "preset": "claude-code",
            "credentials": ["ANTHROPIC_API_KEY"]})
        data = json.loads(body)
        self.assertEqual(status, 201)
        self.assertEqual(data["name"], "My Claude")
        agent_id = data["id"]

        status, body = self.call("GET", "/api/acp-agents")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["agents"]), 1)

        status, body = self.call("DELETE", f"/api/acp-agents/{agent_id}")
        self.assertEqual(status, 200)

    def test_invalid_command_refused(self):
        status, body = self.call("POST", "/api/acp-agents", {"name": "bad", "command": "-dangerous"})
        self.assertEqual(status, 400)

    def test_missing_name_refused(self):
        status, body = self.call("POST", "/api/acp-agents", {"command": "claude"})
        self.assertEqual(status, 400)

    def test_unknown_preset_refused(self):
        status, body = self.call("POST", "/api/acp-agents",
                                  {"name": "bad", "command": "x", "preset": "nonexistent"})
        self.assertEqual(status, 400)

    def test_update_agent(self):
        status, body = self.call("POST", "/api/acp-agents", {"name": "Test", "command": "python"})
        agent_id = json.loads(body)["id"]
        status, body = self.call("PATCH", f"/api/acp-agents/{agent_id}", {"enabled": True, "model": "claude-3"})
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["enabled"], 1)
        self.assertEqual(data["model"], "claude-3")

    def test_availability_check(self):
        status, body = self.call("POST", "/api/acp-agents", {"name": "Test", "command": "python"})
        agent_id = json.loads(body)["id"]
        status, body = self.call("GET", f"/api/acp-agents/{agent_id}/available")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["available"])  # python is always available

    def test_model_discovery(self):
        status, body = self.call("POST", "/api/acp-agents", {"name": "Test", "command": "python"})
        agent_id = json.loads(body)["id"]
        status, body = self.call("GET", f"/api/acp-agents/{agent_id}/models")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIsInstance(data["models"], list)

    def test_delete_unknown_404(self):
        status, body = self.call("DELETE", "/api/acp-agents/nonexistent")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()