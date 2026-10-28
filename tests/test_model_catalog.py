"""The provider catalog: connect with a key, list models live, use one, add your own."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server_routes import build_routes

KEY = "sk-catalog-test-123"


class StandInProvider(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        StandInProvider.seen.append(self.headers.get("Authorization"))
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            self.send_response(401); self.end_headers(); return
        body = json.dumps({"data": [{"id": "old-model", "created": 1}, {"id": "new-model", "created": 9, "context_length": 128000,
                                                                          "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        (base / "repo").mkdir()
        (base / "repo" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.engine = Engine(base / "repo", base / "data")
        self.addCleanup(self.engine.close)
        self.routes = build_routes(self.engine)
        self.provider = HTTPServer(("127.0.0.1", 0), StandInProvider)
        threading.Thread(target=self.provider.serve_forever, daemon=True).start()
        self.addCleanup(self.provider.server_close)
        self.addCleanup(self.provider.shutdown)

    def call(self, method, path, body=None):
        return self.routes(method, path, body or {})

    def test_catalog_lists_providers_with_their_models(self):
        status, data = self.call("GET", "/api/model-catalog")
        self.assertEqual(status, 200)
        for provider in ("openrouter", "anthropic", "openai", "google", "deepseek", "groq", "mistral", "ollama", "azure"):
            self.assertIn(provider, data["providers"])
        self.assertEqual(data["providers"]["openrouter"]["base"], "https://openrouter.ai/api/v1")
        self.assertIn("z-ai/glm-5.2", data["providers"]["openrouter"]["models"])
        self.assertIsNone(data["providers"]["openrouter"]["connection"])

    def test_connect_then_use_a_model_as_the_default(self):
        self.assertEqual(self.call("POST", "/api/model-catalog/openrouter/connect", {})[0], 400)  # a key is needed
        status, provider = self.call("POST", "/api/model-catalog/openrouter/connect", {"api_key": KEY})
        self.assertEqual(status, 200)
        self.assertTrue(provider["connection"] and provider["key_set"])
        self.assertNotIn(KEY, json.dumps(self.call("GET", "/api/model-catalog")[1]))
        self.assertNotIn(KEY, json.dumps(self.call("GET", "/api/connections")[1]))
        status, used = self.call("POST", "/api/model-catalog/openrouter/use", {"model": "z-ai/glm-5.2"})
        self.assertEqual((status, used["name"]), (200, "OpenRouter · z-ai/glm-5.2"))
        described = self.engine.models.describe()
        self.assertEqual((described["configured"], described["model"], described["host"]), (True, "z-ai/glm-5.2", "openrouter.ai"))
        config = self.engine.models.resolve()
        self.assertEqual(config.url, "https://openrouter.ai/api/v1/chat/completions")
        # Using it again does not duplicate the profile.
        self.call("POST", "/api/model-catalog/openrouter/use", {"model": "z-ai/glm-5.2"})
        self.assertEqual(len(self.call("GET", "/api/profiles")[1]), 1)

    def test_custom_provider_and_its_live_model_list(self):
        base = f"http://127.0.0.1:{self.provider.server_port}/v1"
        status, custom = self.call("POST", "/api/model-catalog/custom", {"name": "My server", "base_url": base + "/chat/completions", "api_key": KEY})
        self.assertEqual(status, 201)
        self.assertEqual((custom["base"], custom["key_set"]), (base, True))
        status, listed = self.call("GET", f"/api/model-catalog/{custom['id']}/models")
        self.assertEqual(status, 200)
        self.assertEqual([m["id"] for m in listed["models"]], ["new-model", "old-model"])  # newest first
        self.assertEqual(listed["models"][0]["context"], 128000)
        self.assertIn(f"Bearer {KEY}", StandInProvider.seen)
        self.assertEqual(self.call("POST", f"/api/model-catalog/{custom['id']}/use", {"model": "new-model"})[0], 200)
        self.assertEqual(self.engine.models.describe()["model"], "new-model")

    def test_unregistered_endpoints_stay_refused(self):
        self.assertEqual(self.call("POST", "/api/connections", {"name": "x", "provider": "openai", "endpoint": "https://example.com/v1", "api_key": KEY})[0], 400)
        for bad in ("http://example.com/v1", "https://user:pw@example.com/v1", "ftp://x", ""):
            with self.subTest(bad=bad):
                self.assertEqual(self.call("POST", "/api/model-catalog/custom", {"name": "Bad", "base_url": bad})[0], 400)
        self.call("POST", "/api/model-catalog/custom", {"name": "Mine", "base_url": "https://example.com/v1"})
        self.assertEqual(self.call("POST", "/api/connections", {"name": "y", "provider": "openai", "endpoint": "https://example.com/v1", "api_key": KEY})[0], 201)

    def test_rejected_key_is_reported_plainly(self):
        base = f"http://127.0.0.1:{self.provider.server_port}/v1"
        _, custom = self.call("POST", "/api/model-catalog/custom", {"name": "Wrong key", "base_url": base, "api_key": "sk-wrong"})
        status, body = self.call("GET", f"/api/model-catalog/{custom['id']}/models")
        self.assertEqual(status, 400)
        self.assertIn("rejected the key", body["error"])


if __name__ == "__main__":
    unittest.main()
