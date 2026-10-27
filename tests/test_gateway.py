"""The /v1 gateway answers chat-style requests through one ordinary Dikdik task."""

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


def reply(content="Nothing to change; the value is already 1.", usage=True):
    body = {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return body


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "repo"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n")
        self._env = {**os.environ, "DIKDIK_MODEL_URL": "https://stub.example/v1",
                     "DIKDIK_MODEL": "stub", "DIKDIK_API_KEY": "sk-stub"}
        self._patch = patch.dict(os.environ, self._env, clear=False)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.engine = Engine(root, base / "data")
        self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def call(self, method, path, body=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=40)
        headers = {"Authorization": "Bearer test-token"} if auth else {}
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def chat(self, **extra):
        return self.call("POST", "/v1/chat/completions", {
            "model": "dikdik-default", "messages": [{"role": "user", "content": "What is the value?"}],
            "dikdik": {"approve_external": True}, **extra})

    def test_models_lists_saved_profiles_and_stays_keyed(self):
        self.assertEqual(self.call("GET", "/v1/models", auth=False)[0], 401)
        status, body = self.call("GET", "/v1/models")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["object"], "list")
        self.assertEqual(data["data"], [])  # no saved profiles in this store

    def test_chat_creates_one_task_and_answers(self):
        with patch("dikdik.agent_provider.chat_completion", return_value=reply()):
            status, body = self.chat()
        data = json.loads(body)
        self.assertEqual(status, 200, data)
        choice = data["choices"][0]
        self.assertEqual(data["object"], "chat.completion")
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertEqual(choice["message"]["content"], "Nothing to change; the value is already 1.")
        self.assertEqual(data["dikdik"]["state"], "completed")
        self.assertEqual(data["usage"]["total_tokens"], 15)
        tasks = json.loads(self.call("GET", "/api/tasks")[1])
        self.assertEqual(len(tasks), 1)

    def test_consent_is_explicit_and_per_request(self):
        status, body = self.call("POST", "/v1/chat/completions",
                                 {"model": "dikdik-default", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "Explicit external-code disclosure approval required")

    def test_unknown_model_names_the_saved_profiles(self):
        status, body = self.call("POST", "/v1/chat/completions", {
            "model": "does-not-exist", "messages": [{"role": "user", "content": "hi"}],
            "dikdik": {"approve_external": True}})
        self.assertEqual(status, 400)
        self.assertIn("GET /v1/models", json.loads(body)["error"])

    def test_streaming_refused(self):
        status, body = self.chat(stream=True)
        self.assertEqual(status, 400)
        self.assertIn("Streaming", json.loads(body)["error"])

    def test_missing_messages_refused(self):
        status, body = self.call("POST", "/v1/chat/completions", {"model": "dikdik-default", "dikdik": {"approve_external": True}})
        self.assertEqual(status, 400)

    def test_empty_messages_refused(self):
        status, body = self.call("POST", "/v1/chat/completions", {"model": "dikdik-default", "messages": [], "dikdik": {"approve_external": True}})
        self.assertEqual(status, 400)

    def test_bad_role_refused(self):
        status, body = self.call("POST", "/v1/chat/completions", {"model": "dikdik-default", "messages": [{"role": "tool", "content": "x"}], "dikdik": {"approve_external": True}})
        self.assertEqual(status, 400)

    def test_responses_store_refused(self):
        status, body = self.call("POST", "/v1/responses", {"model": "dikdik-default", "input": "hi", "store": True, "dikdik": {"approve_external": True}})
        self.assertEqual(status, 400)

    def test_responses_previous_id_refused(self):
        status, body = self.call("POST", "/v1/responses", {"model": "dikdik-default", "input": "hi", "previous_response_id": "r1", "dikdik": {"approve_external": True}})
        self.assertEqual(status, 400)

    def test_responses_basic(self):
        with patch("dikdik.agent_provider.chat_completion", return_value=reply()):
            status, body = self.call("POST", "/v1/responses", {
                "model": "dikdik-default", "input": "Summarise the repository.",
                "dikdik": {"mode": "agent", "approve_external": True}})
        data = json.loads(body)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["object"], "response")
        self.assertEqual(data["output_text"], "Nothing to change; the value is already 1.")
        self.assertEqual(data["output"][0]["content"][0]["type"], "output_text")

    def test_source_never_changes_through_the_gateway(self):
        with patch("dikdik.agent_provider.chat_completion", return_value=reply()) as transport:
            self.chat()
            self.assertGreaterEqual(len(transport.call_args_list), 1)
        self.assertEqual((Path(self.temp.name) / "repo" / "app.py").read_text(), "value = 1\n")


if __name__ == "__main__":
    unittest.main()
