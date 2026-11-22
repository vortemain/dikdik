import json
import unittest
from unittest.mock import patch

from dikdik.agent_provider import CompatibleAgentModel
from dikdik.model_config import ModelConfig

# These tests replace the network call, so the model itself only needs to resolve.
CONFIG = ModelConfig("https://provider.example/v1/chat/completions", "m", "k", "bearer", 30, {})


def configured_model():
    return CompatibleAgentModel(True, lambda: CONFIG)


def reply(calls=None, **changes):
    return {"choices": [{"message": {"content": None, "tool_calls": calls or []}, "finish_reason": "tool_calls", **changes}], "usage": {"total_tokens": 17}}


def tool(identity="call-1", name="read_file", arguments=None):
    return {"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments or {"path": "app.py"})}}


class AgentProviderTests(unittest.TestCase):
    def test_approval_required(self):
        for approval in [False, "true", 1, None]:
            with self.subTest(approval=approval), self.assertRaises(ValueError):
                CompatibleAgentModel(approval)

    def test_normalizes_public_tool_call_protocol_and_usage(self):
        with patch("dikdik.agent_provider.chat_completion", return_value=reply([tool()])) as transport:
            model = configured_model()
            response = model.respond([{"role": "system", "content": "policy"}], [{"type": "function"}])
        self.assertEqual(response.tool_calls[0].name, "read_file")
        self.assertEqual(response.tool_calls[0].arguments, {"path": "app.py"})
        self.assertEqual(response.usage_tokens, 17)
        self.assertEqual(transport.call_args.kwargs["max_tokens"], 8192)

    def test_keeps_a_tool_calls_provider_signature_and_rejects_a_malformed_one(self):
        signature = {"google": {"thought_signature": "EnAKbgFpFH0T"}}
        with patch("dikdik.agent_provider.chat_completion", return_value=reply([{**tool(), "extra_content": signature}])):
            response = configured_model().respond([], [{"type": "function"}])
        self.assertEqual(response.tool_calls[0].extra_content, signature)
        with patch("dikdik.agent_provider.chat_completion", return_value=reply([tool()])):
            self.assertIsNone(configured_model().respond([], [{"type": "function"}]).tool_calls[0].extra_content)
        for bad in ("not a dict", {"google": {"thought_signature": "x" * 70000}}):
            with self.subTest(bad=str(bad)[:20]), patch("dikdik.agent_provider.chat_completion", return_value=reply([{**tool(), "extra_content": bad}])), \
                    self.assertRaisesRegex(ValueError, "invalid or incomplete"):
                configured_model().respond([], [])

    def test_rejects_incomplete_malformed_and_duplicate_calls(self):
        bad = [
            reply([tool(), tool()]),
            reply([{"id": "x", "function": {"name": "read_file", "arguments": "not JSON"}}]),
            {"choices": []},
            reply([tool(str(i)) for i in range(9)]),
        ]
        for data in bad:
            with self.subTest(data=data), patch("dikdik.agent_provider.chat_completion", return_value=data), self.assertRaisesRegex(ValueError, "invalid or incomplete"):
                configured_model().respond([], [])

    def test_an_oversized_response_is_retried_with_a_write_in_parts_hint(self):
        from dikdik.model_errors import ProviderError
        model = configured_model()
        with patch("dikdik.agent_provider.chat_completion",
                   side_effect=[ProviderError("response_limit"), reply([tool()])]) as transport:
            with self.assertRaises(ProviderError) as caught:
                model.respond([{"role": "user", "content": "Write chapters"}], [])
            self.assertTrue(caught.exception.retryable)
            model.respond([{"role": "user", "content": "Write chapters"}], [])
        hint = transport.call_args_list[1].args[0][-1]["content"]
        self.assertIn("append", hint)
        self.assertIn("source", hint)

    def test_repeated_stream_drops_ask_for_the_work_in_smaller_pieces(self):
        from dikdik.model_errors import ProviderError
        model = configured_model()
        with patch("dikdik.agent_provider.chat_completion",
                   side_effect=[ProviderError("stream_interrupted"), ProviderError("stream_interrupted"), reply([tool()])]) as transport:
            for _ in range(2):
                with self.assertRaises(ProviderError) as caught:
                    model.respond([{"role": "user", "content": "Write chapters"}], [])
                self.assertTrue(caught.exception.retryable)
            model.respond([{"role": "user", "content": "Write chapters"}], [])
        first, second, third = (call.args[0] for call in transport.call_args_list)
        self.assertEqual(len(first), len(second))  # one drop is retried as it was
        self.assertIn("source", third[-1]["content"])  # a second drop asks for smaller pieces

    def test_cannot_smuggle_provider_metadata_into_usage(self):
        data = reply([tool()])
        data["usage"] = {"total_tokens": -1, "private": "credential"}
        with patch("dikdik.agent_provider.chat_completion", return_value=data):
            response = configured_model().respond([], [])
        self.assertEqual(response.usage_tokens, 0)

    def test_component_usage_is_counted_when_total_is_missing_or_too_small(self):
        for total in [None, 1]:
            data = reply([tool()])
            data["usage"] = {"prompt_tokens": 40000, "completion_tokens": 2000}
            if total is not None:
                data["usage"]["total_tokens"] = total
            with self.subTest(total=total), patch("dikdik.agent_provider.chat_completion", return_value=data):
                response = configured_model().respond([], [])
            self.assertEqual(response.usage_tokens, 42000)
