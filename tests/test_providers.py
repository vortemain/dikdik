import io
import json
import os
import unittest
from unittest.mock import patch

from dikdik.providers import NoRedirect, propose


ENV = {"DIKDIK_MODEL_URL": "https://provider.example/v1/chat/completions", "DIKDIK_MODEL": "configured-model", "DIKDIK_API_KEY": "private-key"}
EDITS = [{"path": "app.py", "before": "x=1", "after": "x=2"}]


class ProviderTests(unittest.TestCase):
    def test_offline_has_zero_network_calls(self):
        with patch("urllib.request.build_opener") as opener:
            edits, evidence = propose({"mode": "offline", "edits": EDITS}, {})
        self.assertEqual(edits, EDITS)
        self.assertEqual(evidence["model_calls"], 0)
        opener.assert_not_called()

    def test_external_requires_approval(self):
        with self.assertRaisesRegex(ValueError, "approval"):
            propose({"mode": "model"}, {})

    def test_requires_https_and_credentials(self):
        for env in [{}, {**ENV, "DIKDIK_MODEL_URL": "http://provider.example"}, {**ENV, "DIKDIK_MODEL_URL": "https://user:password@example.org"}]:
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), self.assertRaises(ValueError):
                propose({"mode": "model", "approve_external": True}, {})

    def test_structured_contract_and_safe_usage(self):
        response = {"choices": [{"message": {"content": json.dumps({"edits": EDITS})}}], "usage": {"total_tokens": 12, "credential": "private-key"}}
        with patch.dict(os.environ, ENV), patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
            edits, evidence = propose({"mode": "model", "approve_external": True, "objective": "Update x"}, {"app.py": "x=1"})
            req = opener.return_value.open.call_args.args[0]
        self.assertEqual(edits, EDITS)
        self.assertEqual(evidence["usage"], {"total_tokens": 12})
        self.assertNotIn("private-key", json.dumps(evidence))
        self.assertEqual(req.get_header("Authorization"), "Bearer private-key")
        self.assertEqual(json.loads(req.data)["max_tokens"], 4096)

    def test_context_limit_prevents_request(self):
        with patch.dict(os.environ, ENV), patch("urllib.request.build_opener") as opener, self.assertRaisesRegex(ValueError, "128KB"):
            propose({"mode": "model", "approve_external": True}, {"large.py": "x" * 128001})
        opener.assert_not_called()

    def test_malformed_response_is_rejected(self):
        with patch.dict(os.environ, ENV), patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(b'{"choices":[]}')
            with self.assertRaisesRegex(ValueError, "structured"):
                propose({"mode": "model", "approve_external": True, "objective": "x"}, {})

    def test_redirect_is_not_followed(self):
        with self.assertRaisesRegex(ValueError, "redirects"):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")

    def test_transport_validation_errors_do_not_expose_credentials(self):
        with patch.dict(os.environ, ENV), patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = ValueError("Invalid header value private-key")
            with self.assertRaises(ValueError) as caught:
                propose({"mode": "model", "approve_external": True, "objective": "x"}, {})
        self.assertNotIn("private-key", str(caught.exception))
        self.assertIn("request failed", str(caught.exception))
