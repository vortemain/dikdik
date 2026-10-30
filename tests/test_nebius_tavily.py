import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch, Mock

from dikdik import research, session_credentials
from dikdik.secrets_store import SecretsStore
from dikdik.engine import Engine
from dikdik.server_routes import build_routes
from dikdik.acp_agents import credential_pairs
from dikdik.agent import ToolCall
from dikdik.control import permission_rule_effect


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        root = self.base / "repo"
        root.mkdir()
        (root / "a.py").write_text("x = 1\n")
        self.engine = Engine(root, self.base / "data")
        self.addCleanup(self.engine.close)
        self.routes = build_routes(self.engine)

    def test_nebius_key_shared_with_resolver_but_not_written_and_disconnect(self):
        status, provider = self.routes("POST", "/api/model-catalog/nebius/connect", {"api_key": "test-nebius-key"})
        self.assertEqual(status, 200)
        self.assertTrue(provider["key_set"])
        self.assertEqual(self.routes("POST", "/api/model-catalog/nebius/use", {"model": "nvidia/test-nemotron"})[0], 200)
        model = self.engine.models.resolve()
        self.assertEqual(model.url, "https://api.tokenfactory.nebius.com/v1/chat/completions")
        self.assertEqual(model.key, "test-nebius-key")
        self.assertFalse((self.base / "data" / "secrets.bin").exists())
        self.assertNotIn("test-nebius-key", repr(model))
        self.routes("POST", "/api/model-catalog/nebius/disconnect", {})
        self.assertFalse(self.engine.models.describe()["configured"])

    def test_expiry_and_process_shutdown_clear_credentials(self):
        self.routes("POST", "/api/research", {"api_key": "test-tavily-key"})
        self.assertTrue(research.configured(self.engine.models.secrets))
        with patch("dikdik.session_credentials.time.monotonic", return_value=10**15):
            self.assertFalse(research.configured(self.engine.models.secrets))

    def test_research_routes_validate_and_hide_keys(self):
        self.assertEqual(self.routes("POST", "/api/research", {"api_key": "bad\nkey"})[0], 400)
        self.assertEqual(self.routes("POST", "/api/research", {"api_key": "test-tavily-key"})[0], 200)
        self.assertEqual(self.routes("GET", "/api/secrets", {})[1], [])
        self.assertNotIn("test-tavily-key", json.dumps(self.routes("GET", "/api/research", {})[1]))
        self.routes("DELETE", "/api/research", {})
        self.assertFalse(research.configured(self.engine.models.secrets))

    def test_session_credentials_are_isolated_between_private_data_directories(self):
        store = self.engine.models.secrets
        store.set_session("research.tavily", b"private-session-key")
        other = SecretsStore(self.base / "other-data" / "secrets.bin")
        with self.assertRaises(KeyError):
            other.reveal("research.tavily")
        self.assertEqual(other.list(), [])

    def test_tavily_request_is_bounded_and_keeps_sources(self):
        self.routes("POST", "/api/research", {"api_key": "test-tavily-key"})
        data = {"results": [{"title": "Docs", "url": "https://example.org/docs", "content": "x" * 6000},
                            {"title": "Unsafe", "url": "javascript:alert(1)", "content": "bad"}]}
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(json.dumps(data).encode()))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch("dikdik.research.urllib.request.build_opener", return_value=opener):
            result = research.search(self.engine.models.secrets, {"query": "public API docs"})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.tavily.com/search")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-tavily-key")
        self.assertNotIn("test-tavily-key", request.data.decode())
        self.assertTrue(result["untrusted"])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(len(result["results"][0]["content"]), 4000)
        self.assertEqual(result["results"][0]["url"], "https://example.org/docs")

    def test_tavily_failure_never_exposes_provider_body(self):
        self.routes("POST", "/api/research", {"api_key": "test-tavily-key"})
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://api.tavily.com/search", 432, "test-tavily-key", {}, None)
        with patch("dikdik.research.urllib.request.build_opener", return_value=opener):
            result = research.search(self.engine.models.secrets, {"query": "docs"})
        self.assertIn("allowance", result["error"])
        self.assertNotIn("test-tavily-key", json.dumps(result))
        with self.assertRaises(ValueError):
            research.search(self.engine.models.secrets, {"query": "docs", "max_results": True})

    def test_replacing_saved_key_removes_persistent_record(self):
        store = self.engine.models.secrets
        store.set("conn.example", b"old-persistent-key")
        store.set_session("conn.example", b"new-memory-key")
        self.assertEqual(SecretsStore(store.path, store.keyfile).reveal("conn.example"), b"new-memory-key")
        self.assertNotIn("conn.example", json.loads(store.path.read_text())["records"])
        session_credentials.clear(store.path)
        with self.assertRaises(KeyError):
            store.reveal("conn.example")


class ResearchPolicyTests(unittest.TestCase):
    def test_session_credentials_cannot_be_borrowed_by_external_agents(self):
        for ref in ("conn.provider", "research.tavily"):
            with self.assertRaises(ValueError):
                credential_pairs(["API_KEY=" + ref])

    def test_browser_deny_rule_also_blocks_tavily_search(self):
        call = ToolCall("call-1", "web_search", {"query": "public docs"})
        from dikdik.control import parse_rules
        rules = parse_rules([{"action": "browse", "resource": "*", "effect": "deny"}])
        self.assertEqual(permission_rule_effect(rules, call), "deny")

    def test_authentication_is_never_forwarded_to_redirect_targets(self):
        redirect = research.NoRedirect()
        request = urllib.request.Request("https://api.tavily.com/search", headers={"Authorization": "Bearer secret"})
        self.assertIsNone(redirect.redirect_request(request, None, 302, "redirect", {}, "https://another.example/"))
