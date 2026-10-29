import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine, TERMINAL
from dikdik.model_config import ModelResolver
from dikdik.profiles import ProfileStore
from dikdik.providers import chat_completion
from dikdik.secrets_store import SecretsStore
from dikdik.server_routes import build_routes

KEY = "sk-profile-secret-0123456789"
ENV = {"DIKDIK_MODEL_URL": "https://env.example/v1/chat/completions", "DIKDIK_MODEL": "env-model", "DIKDIK_API_KEY": "env-key"}
EDITS = [{"path": "calculator.py", "before": "a - b", "after": "a + b"}]


def reply(content):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 9}}).encode())


class ModelResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
        self.data = base / "data"
        self.engine = Engine(self.root, self.data)
        self.addCleanup(self.engine.close)
        self.profiles = ProfileStore(self.engine.store)
        self.secrets = SecretsStore(self.data / "secrets.bin", self.data / "secrets.key")
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def profile(self, **fields):
        body = {"name": "Main", "provider": "openai", "model": "gpt-test", "credential_secret_ref": "openai-key", **fields}
        return self.profiles.create(body)[1]["id"]

    def test_nothing_configured_explains_both_ways_to_set_up(self):
        info = self.engine.models.describe()
        self.assertFalse(info["configured"])
        self.assertIn("Settings → Models", info["problem"])
        with self.assertRaisesRegex(ValueError, "DIKDIK_MODEL_URL"):
            self.engine.models.resolve()

    def test_environment_is_the_fallback_without_profiles(self):
        with patch.dict(os.environ, ENV):
            config = self.engine.models.resolve()
            info = self.engine.models.describe()
        self.assertEqual((config.url, config.model, config.key), (ENV["DIKDIK_MODEL_URL"], "env-model", "env-key"))
        self.assertEqual((info["configured"], info["source"]), (True, "environment"))

    def test_nvidia_environment_key_uses_default_free_developer_model(self):
        from dikdik.model_catalog import NVIDIA_DEFAULT_MODEL
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-nvidia-key"}):
            config = self.engine.models.resolve()
            info = self.engine.models.describe()
        self.assertEqual((config.model, config.auth, config.key), (NVIDIA_DEFAULT_MODEL, "bearer", "test-nvidia-key"))
        self.assertEqual(config.url, "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertEqual((info["configured"], info["provider_name"]), (True, "NVIDIA"))
        self.assertNotIn("test-nvidia-key", json.dumps(info))
        with patch.dict(os.environ, {**ENV, "NVIDIA_API_KEY": "test-nvidia-key"}):
            self.assertEqual(self.engine.models.resolve().model, "env-model")

    def test_default_profile_beats_environment_and_decrypts_its_key(self):
        self.secrets.set("openai-key", KEY.encode())
        self.profile(timeout_seconds=45)
        with patch.dict(os.environ, ENV):
            config = self.engine.models.resolve()
        self.assertEqual(config.url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual((config.model, config.key, config.auth, config.timeout), ("gpt-test", KEY, "bearer", 45))
        self.assertNotIn(KEY, repr(config))
        self.assertNotIn(KEY, json.dumps(config.public))
        info = self.engine.models.describe()
        self.assertEqual((info["configured"], info["profile"], info["provider_name"], info["model"]), (True, "Main", "OpenAI", "gpt-test"))

    def test_switching_the_default_profile_switches_the_model(self):
        self.secrets.set("openai-key", KEY.encode())
        self.secrets.set("anthropic-key", b"sk-ant-other")
        self.profile()
        second = self.profile(name="Claude", provider="anthropic", model="claude-test", credential_secret_ref="anthropic-key")
        self.assertEqual(self.engine.models.resolve().model, "gpt-test")
        self.profiles.patch(second, {"is_default": True})
        config = self.engine.models.resolve()
        self.assertEqual((config.url, config.model, config.key), ("https://api.anthropic.com/v1/chat/completions", "claude-test", "sk-ant-other"))

    def test_missing_or_unbound_key_is_a_clear_error(self):
        self.profile()
        self.assertIn("is not stored", self.engine.models.describe()["problem"])
        with self.assertRaisesRegex(ValueError, "Settings → API keys"):
            self.engine.models.resolve()
        self.profiles.create({"name": "No key", "provider": "openai", "model": "m"})
        with self.engine.store.connect() as db:
            db.execute("UPDATE profiles SET is_default=(name='No key')")
        self.assertIn("has no API key", self.engine.models.describe()["problem"])
        with self.assertRaisesRegex(ValueError, "has no API key"):
            self.engine.models.resolve()

    def test_local_server_needs_no_key_and_may_use_http_on_loopback(self):
        self.profile(provider="local", endpoint="http://127.0.0.1:11434/v1", credential_secret_ref=None)
        config = self.engine.models.resolve()
        self.assertEqual((config.url, config.auth, config.key), ("http://127.0.0.1:11434/v1/chat/completions", "none", ""))
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = reply("{}")
            chat_completion([{"role": "user", "content": "hi"}], config=config)
            sent = opener.return_value.open.call_args.args[0]
        self.assertIsNone(sent.get_header("Authorization"))

    def test_explicit_missing_key_on_local_profile_still_reports_error(self):
        self.profile(provider="local", endpoint="http://localhost:11434/v1")
        self.assertFalse(self.engine.models.describe()["configured"])
        with self.assertRaisesRegex(ValueError, "is not stored"):
            self.engine.models.resolve()

    def test_azure_sends_api_key_header_and_needs_an_endpoint(self):
        self.secrets.set("azure-key", b"azure-secret")
        self.profile(provider="azure", credential_secret_ref="azure-key")
        with self.assertRaisesRegex(ValueError, "needs an endpoint"):
            self.engine.models.resolve()
        self.profiles.patch(self.profiles.list_profiles()[1][0]["id"], {"endpoint": "http://localhost:8080/openai"})
        config = self.engine.models.resolve()
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = reply("{}")
            chat_completion([], config=config)
            sent = opener.return_value.open.call_args.args[0]
        self.assertEqual(sent.get_header("Api-key"), "azure-secret")
        self.assertIsNone(sent.get_header("Authorization"))

    def test_model_task_uses_profile_and_never_stores_the_key(self):
        self.secrets.set("openai-key", KEY.encode())
        self.profile(timeout_seconds=20)
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = reply(json.dumps({"edits": EDITS}))
            task = self.engine.submit({"objective": "Fix addition", "mode": "model", "verification": "syntax", "approve_external": True})
            self.engine.pool.submit(lambda: None).result(timeout=20)
            sent = opener.return_value.open.call_args
        result = self.engine.store.get(task)
        self.assertEqual(result["state"], "completed", result)
        self.assertEqual(sent.args[0].full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(sent.args[0].get_header("Authorization"), "Bearer " + KEY)
        self.assertEqual(sent.kwargs["timeout"], 20)
        self.assertEqual(json.loads(sent.args[0].data)["model"], "gpt-test")
        proposal = next(event for event in result["events"] if event["kind"] == "proposal")
        self.assertEqual(proposal["data"]["model"]["profile"], "Main")
        self.assertNotIn(KEY, json.dumps(result))
        self.assertNotIn(KEY.encode(), (self.data / "state.sqlite3").read_bytes())

    def test_agent_start_records_which_model_without_the_key(self):
        self.secrets.set("openai-key", KEY.encode())
        self.profile()
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = OSError("offline")
            task = self.engine.submit({"objective": "Fix addition", "mode": "agent", "verification": "docker", "approve_external": True, "approve_execution": True})
            self.engine.pool.submit(lambda: None).result(timeout=20)
        result = self.engine.store.get(task)
        self.assertIn(result["state"], TERMINAL)
        started = next(event for event in result["events"] if event["kind"] == "agent_started")
        self.assertEqual((started["data"]["model"]["profile"], started["data"]["model"]["model"]), ("Main", "gpt-test"))
        self.assertNotIn(KEY, json.dumps(result))

    def test_reveal_is_internal_only(self):
        self.secrets.set("openai-key", KEY.encode())
        self.assertEqual(self.secrets.reveal("openai-key"), KEY.encode())
        with self.assertRaises(KeyError):
            self.secrets.reveal("missing")
        routes = build_routes(self.engine)
        for path in ("/api/secrets", "/api/secrets/openai-key", "/api/profiles"):
            self.assertNotIn(KEY, json.dumps(routes("GET", path, {})))
