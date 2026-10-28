"""Provider metadata contracts. Stand-ins, not live provider/account validation."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from dikdik.model_discovery import discover_models, normalize_model


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


class ModelDiscoveryTests(unittest.TestCase):
    def discover(self, provider, pages, key="test-private-key", base="https://provider.example/v1", **extra):
        requests = []
        def fetch(request, **kwargs):
            requests.append(request)
            value = pages[len(requests) - 1]
            if isinstance(value, Exception):
                raise value
            return Response(value)
        with patch("dikdik.model_discovery.urllib.request.build_opener") as factory:
            factory.return_value.open.side_effect = fetch
            result = discover_models({"id": provider, "base": base, **extra}, key)
        self.assertNotIn(key or "unused-key", json.dumps(result))
        return result, requests

    def test_anthropic_follows_every_page_and_uses_only_its_auth_header(self):
        listed, calls = self.discover("anthropic", [
            {"data": [{"id": "one", "display_name": "First", "max_input_tokens": 200000}], "has_more": True, "last_id": "one"},
            {"data": [{"id": "two"}], "has_more": False},
        ])
        self.assertTrue(listed["complete"])
        self.assertEqual(listed["pages"], 2)
        self.assertEqual(len(listed["models"]), 2)
        self.assertEqual(calls[0].get_header("X-api-key"), "test-private-key")
        self.assertIsNone(calls[0].get_header("Authorization"))
        self.assertEqual(parse_qs(urlsplit(calls[1].full_url).query)["after_id"], ["one"])

    def test_native_fireworks_schema_and_page_tokens(self):
        listed, calls = self.discover("fireworks", [
            {"models": [{"name": "accounts/fireworks/models/one", "displayName": "First", "contextLength": 128000}], "nextPageToken": "page +/two"},
            {"models": [{"name": "accounts/fireworks/models/two"}]},
        ], base="https://api.fireworks.ai/inference/v1")
        self.assertEqual(urlsplit(calls[0].full_url).path, "/v1/accounts/fireworks/models")
        self.assertEqual(parse_qs(urlsplit(calls[1].full_url).query)["pageToken"], ["page +/two"])
        self.assertEqual(listed["models"][0]["name"], "First")

    def test_native_gemini_names_context_auth_and_pagination(self):
        listing, calls = self.discover("google", [
            {"models": [{"name": "models/gemini-test", "displayName": "Gemini Test", "inputTokenLimit": 1000000}], "nextPageToken": "next"},
            {"models": [{"name": "models/embed-test", "supportedGenerationMethods": ["embedContent"]}]},
        ], base="https://generativelanguage.googleapis.com/v1beta/openai")
        self.assertEqual(calls[0].get_header("X-goog-api-key"), "test-private-key")
        self.assertIsNone(calls[0].get_header("Authorization"))
        self.assertEqual(urlsplit(calls[0].full_url).path, "/v1beta/models")
        self.assertEqual(parse_qs(urlsplit(calls[1].full_url).query)["pageToken"], ["next"])
        models = {model["id"]: model for model in listing["models"]}
        self.assertEqual(models["gemini-test"]["context"], 1000000)
        self.assertFalse(models["embed-test"]["chat_supported"])

    def test_cerebras_has_a_real_public_endpoint_without_credentials(self):
        listed, calls = self.discover("cerebras", [{"data": [{"id": "one", "limits": {"max_context_length": 131072}}]}], key=None, base="https://api.cerebras.ai/v1")
        self.assertEqual(calls[0].full_url, "https://api.cerebras.ai/public/v1/models")
        self.assertIsNone(calls[0].get_header("Authorization"))
        self.assertEqual(listed["models"][0]["context"], 131072)

    def test_generic_openai_compatible_provider_formats_and_azure_header(self):
        for provider in ("openai", "google", "deepseek", "xai", "mistral", "groq", "together", "moonshot", "zai", "qwen", "perplexity-orchestrator", "ollama", "lmstudio", "custom"):
            with self.subTest(provider=provider):
                result, calls = self.discover(provider, [{"data": [{"id": "one"}]}])
                self.assertTrue(result["complete"])
                self.assertEqual(calls[0].get_header("Authorization"), "Bearer test-private-key")
        _, calls = self.discover("azure", [{"data": []}], kind="azure")
        self.assertEqual(calls[0].get_header("Api-key"), "test-private-key")
        self.assertIsNone(calls[0].get_header("Authorization"))

    def test_large_list_is_not_silently_truncated_and_deduplicates_ids(self):
        result, _ = self.discover("openrouter", [{"data": [{"id": f"model-{i}"} for i in range(2401)] + [{"id": "model-0"}]}])
        self.assertEqual(len(result["models"]), 2401)

    def test_invalid_shapes_and_repeated_cursors_do_not_claim_a_complete_catalog(self):
        for pages in ([{"unexpected": []}], [{"data": [], "has_more": True}],
                      [{"data": [{"id": "one"}], "has_more": True, "last_id": "one"}] * 2,
                      [{"data": [], "next": "https://evil.example/key-collector"}]):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                self.discover("anthropic", pages)

    def test_page_urls_never_come_from_response_data(self):
        result, calls = self.discover("anthropic", [
            {"data": [{"id": "one"}], "has_more": True, "last_id": "https://evil.example/?secret=1"},
            {"data": [{"id": "two"}], "has_more": False},
        ])
        self.assertEqual(urlsplit(calls[1].full_url).hostname, "provider.example")

    def test_http_errors_do_not_expose_provider_response_bodies_or_keys(self):
        for status in (401, 403, 404, 429, 302, 500):
            error = HTTPError("https://provider.example/v1/models", status, "test-private-key", {}, io.BytesIO(b"test-private-key"))
            with self.subTest(status=status), self.assertRaises(ValueError) as caught:
                self.discover("custom", [error])
            self.assertNotIn("test-private-key", str(caught.exception))

    def test_normalizes_native_metadata_and_explicit_non_chat_models(self):
        self.assertEqual(normalize_model({"name": "models/gemini", "displayName": "Gemini", "supportedGenerationMethods": ["embedContent"]}, "google")["chat_supported"], False)
        self.assertEqual(normalize_model({"id": "m", "max_context_length": 32000, "capabilities": {"completion_chat": True}}, "mistral")["context"], 32000)
        self.assertIsNone(normalize_model({"id": "bad\nname"}, "custom"))
