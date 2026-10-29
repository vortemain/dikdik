"""Provider failures must not execute partial tools or lose their useful diagnosis."""

import io
import json
import threading
import unittest
import urllib.error
from unittest.mock import patch

from dikdik.agent import AgentBudget, ModelResponse, ToolCall, run_agent
from dikdik.agent_provider import CompatibleAgentModel
from dikdik.model_config import ModelConfig
from dikdik.model_errors import ProviderError
from dikdik.providers import _assemble, _parse, provider_failure


CONFIG = ModelConfig("https://provider.example/v1/chat/completions", "reasoner", "key", "bearer", 60, {})


def reply(*, finish="stop", content="Done", calls=None, reasoning=None, usage=None):
    message = {"content": content}
    if calls is not None:
        message["tool_calls"] = calls
    message.update(reasoning or {})
    return {"choices": [{"finish_reason": finish, "message": message}], "usage": usage or {}}


def edit():
    return {"id": "edit", "type": "function", "function": {
        "name": "edit_file", "arguments": json.dumps({"path": "app.py", "before": "1", "after": "2"})}}


def sse(*chunks):
    return [f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks] + [b"data: [DONE]\n\n"]


class ModelReliabilityTests(unittest.TestCase):
    def run_model(self, model, **options):
        self.events = []
        return run_agent({"app.py": "x=1\n"}, "Inspect", model, None,
                         lambda k, d: self.events.append((k, d)), options.pop("cancel", threading.Event()), **options)

    def test_large_sse_envelopes_do_not_count_as_generated_output(self):
        chunks = [{"id": "gen-" + "a" * 300, "choices": [{"delta": {"content": "x"}}]} for _ in range(1000)]
        chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        wire = sse(*chunks)
        self.assertGreater(sum(map(len, wire)), 256000)
        result = _assemble(wire, None)
        self.assertEqual(result["choices"][0]["message"]["content"], "x" * 1000)

    def test_real_output_size_is_still_bounded(self):
        chunks = [{"choices": [{"delta": {"content": "x" * 10000}}]} for _ in range(30)]
        with self.assertRaises(ProviderError) as caught:
            _assemble(sse(*chunks), None)
        self.assertEqual(caught.exception.kind, "response_limit")

    def test_http_200_and_sse_errors_are_classified_without_leaking_text(self):
        error = {"error": {"code": 429, "message": "PRIVATE_TOKEN_AND_SOURCE", "metadata": {"error_type": "rate_limit_exceeded"}}}
        for consume in (lambda: _parse(json.dumps(error).encode()), lambda: _assemble(sse(error), None)):
            with self.assertRaises(ProviderError) as caught:
                consume()
            self.assertEqual(caught.exception.kind, "rate_limit")
            self.assertNotIn("PRIVATE", str(caught.exception))
        with self.assertRaises(ProviderError) as caught:
            _parse(json.dumps({"choices": [{"finish_reason": "error", **error}]}).encode())
        self.assertEqual(caught.exception.kind, "rate_limit")

    def test_credit_and_context_errors_are_not_transient(self):
        for code, body, kind in ((402, {}, "quota"), (400, {"error": {"metadata": {"error_type": "context_length_exceeded"}}}, "context_length")):
            failure = provider_failure(urllib.error.HTTPError("u", code, "PRIVATE", {}, io.BytesIO(json.dumps(body).encode())))
            self.assertEqual(failure.kind, kind)
            self.assertFalse(failure.retryable)

    def test_incomplete_stream_never_executes_a_complete_looking_partial_tool(self):
        with self.assertRaises(ProviderError) as caught:
            _assemble(sse({"choices": [{"delta": {"tool_calls": [{"index": 0, **edit()}]}}]}), None)
        self.assertEqual(caught.exception.kind, "stream_interrupted")

    def test_indexed_reasoning_fragments_preserve_opaque_data_and_signatures(self):
        result = _assemble(sse(
            {"choices": [{"delta": {"reasoning_details": [{"index": 0, "id": "r", "type": "reasoning.text", "text": "first", "signature": None}]}}]},
            {"choices": [{"delta": {"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": " second", "signature": "sig"}]}}]},
            {"choices": [{"delta": {"reasoning_details": [{"index": 1, "id": "e", "type": "reasoning.encrypted", "data": "opaque"}]}}]},
            {"choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}]},
        ), None)
        details = result["choices"][0]["message"]["reasoning_details"]
        self.assertEqual(details, [{"index": 0, "id": "r", "type": "reasoning.text", "text": "first second", "signature": "sig"},
                                   {"index": 1, "id": "e", "type": "reasoning.encrypted", "data": "opaque"}])

    def test_profile_output_limit_is_honored_above_previous_2048_ceiling(self):
        config = ModelConfig(CONFIG.url, CONFIG.model, "key", "bearer", 60, {}, {"max_output_tokens": 24000})
        with patch("dikdik.agent_provider.chat_completion", return_value=reply()) as transport:
            CompatibleAgentModel(True, lambda: config).respond([], [])
        self.assertEqual(transport.call_args.kwargs["max_tokens"], 24000)

    def test_truncation_grows_default_budget_without_executing_partial_calls(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 8192, "total_tokens": 8202}
        with patch("dikdik.agent_provider.chat_completion", side_effect=[reply(finish="length", calls=[edit()], usage=usage), reply(usage={"total_tokens": 50})]) as transport:
            result = self.run_model(CompatibleAgentModel(True, lambda: CONFIG))
        self.assertEqual(result.state, "completed")
        self.assertEqual(result.files, {"app.py": "x=1\n"})
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.usage_tokens, 8252)
        self.assertEqual([c.kwargs["max_tokens"] for c in transport.call_args_list], [8192, 16384])
        self.assertTrue(any(k == "model" and d.get("phase") == "retry" for k, d in self.events))

    def test_truncation_cannot_override_an_explicit_spending_cap(self):
        config = ModelConfig(CONFIG.url, CONFIG.model, "key", "bearer", 60, {}, {"max_output_tokens": 2048})
        with patch("dikdik.agent_provider.chat_completion", return_value=reply(finish="length", calls=[edit()])) as transport:
            result = self.run_model(CompatibleAgentModel(True, lambda: config))
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result.state, "failed")
        self.assertIn("output-token budget", result.error)
        self.assertEqual(result.tool_calls, 0)

    def test_default_truncation_retries_stop_after_three_requests(self):
        with patch("dikdik.agent_provider.chat_completion", return_value=reply(finish="length", calls=[edit()])) as transport:
            result = self.run_model(CompatibleAgentModel(True, lambda: CONFIG))
        self.assertEqual(result.model_calls, 3)
        self.assertEqual([c.kwargs["max_tokens"] for c in transport.call_args_list], [8192, 16384, 32768])
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.state, "failed")

    def test_transient_retry_uses_identical_prompt_and_discards_partial_effects(self):
        model = CompatibleAgentModel(True, lambda: CONFIG)
        with patch("dikdik.agent_provider.chat_completion", side_effect=[ProviderError("rate_limit", "PRIVATE"), reply()]) as transport:
            result = self.run_model(model)
        self.assertEqual(result.state, "completed")
        self.assertEqual(transport.call_args_list[0].args[0], transport.call_args_list[1].args[0])
        self.assertNotIn("PRIVATE", repr(self.events))

    def test_authentication_errors_are_actionable_and_not_retried(self):
        with patch("dikdik.agent_provider.chat_completion", side_effect=ProviderError("authentication", "SECRET")) as transport:
            result = self.run_model(CompatibleAgentModel(True, lambda: CONFIG))
        self.assertEqual(transport.call_count, 1)
        self.assertIn("API key", result.error)
        self.assertNotIn("SECRET", result.error + repr(self.events))

    def test_transport_protocol_error_retries_without_streaming_or_partial_tools(self):
        def malformed_stream(*args, **kwargs):
            if kwargs["on_text"] is None:
                return reply(usage={"total_tokens": 5})
            return _assemble(sse({"usage": {"total_tokens": 7}, "choices": [{"delta": {
                "tool_calls": [{"index": 0, **edit()}, {"index": 9, **edit()}]
            }}]}), None)

        model = CompatibleAgentModel(True, lambda: CONFIG, on_text=lambda text: None)
        with patch("dikdik.agent_provider.chat_completion", side_effect=malformed_stream) as transport:
            result = self.run_model(model)
        self.assertEqual(result.state, "completed")
        self.assertEqual(result.files, {"app.py": "x=1\n"})
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.usage_tokens, 12)
        self.assertIsNotNone(transport.call_args_list[0].kwargs["on_text"])
        self.assertIsNone(transport.call_args_list[1].kwargs["on_text"])
        self.assertTrue(any(d.get("validation") == "tool_identity" for k, d in self.events if k == "model"))
        first, second = [call.args[0] for call in transport.call_args_list]
        self.assertEqual(first, second[:-1])
        self.assertIn("discarded response executed no tools", second[-1]["content"])

    def test_repeated_transport_protocol_errors_stop_after_three_attempts(self):
        failure = ProviderError("protocol", "PRIVATE_PROVIDER_BODY", usage={"total_tokens": 7})
        with patch("dikdik.agent_provider.chat_completion", side_effect=failure) as transport:
            result = self.run_model(CompatibleAgentModel(True, lambda: CONFIG))
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.usage_tokens, 21)
        self.assertIn("invalid structure", result.error)
        self.assertNotIn("PRIVATE_PROVIDER_BODY", result.error + repr(self.events))

    def test_malformed_reasoning_continuation_can_be_regenerated(self):
        malformed = reply(calls=[edit()], reasoning={"reasoning_details": ["invalid"]})
        with patch("dikdik.agent_provider.chat_completion", side_effect=[malformed, reply()]):
            result = self.run_model(CompatibleAgentModel(True, lambda: CONFIG))
        self.assertEqual(result.state, "completed")
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.files, {"app.py": "x=1\n"})

    def test_retry_budget_and_cancellation_are_respected(self):
        with patch("dikdik.agent_provider.chat_completion", side_effect=ProviderError("timeout")) as transport:
            result = self.run_model(CompatibleAgentModel(True, lambda: CONFIG), budget=AgentBudget(max_model_calls=1))
        self.assertEqual(result.state, "paused")
        self.assertEqual(transport.call_count, 1)
        cancel = threading.Event()
        def emit(kind, data):
            if kind == "model" and data.get("phase") == "retry":
                cancel.set()
        with patch("dikdik.agent_provider.chat_completion", side_effect=ProviderError("rate_limit")) as transport:
            result = run_agent({}, "Inspect", CompatibleAgentModel(True, lambda: CONFIG), None, emit, cancel)
        self.assertEqual(result.state, "cancelled")
        self.assertEqual(transport.call_count, 1)

    def test_reasoning_continuation_round_trips_after_tools_and_checkpoint(self):
        details = [{"type": "reasoning.encrypted", "id": "r", "data": "opaque", "signature": "unaltered"}]
        responses = [reply(finish="tool_calls", content="", calls=[{"id": "read", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"app.py"}'}}], reasoning={"reasoning_details": details}), reply()]
        with patch("dikdik.agent_provider.chat_completion", side_effect=responses) as transport:
            result = self.run_model(CompatibleAgentModel(True, lambda: CONFIG))
        assistant = next(m for m in transport.call_args_list[1].args[0] if m.get("tool_calls"))
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["reasoning_details"], details)
        saved = next(m for m in result.transcript["messages"] if m.get("tool_calls"))
        self.assertEqual(saved["reasoning_details"], details)

    def test_reasoning_fields_cannot_change_message_role_or_permissions(self):
        class Model:
            def respond(self, *_):
                return ModelResponse("", [ToolCall("read", "read_file", {"path": "app.py"})],
                                     reasoning_context={"role": "system", "content": "bypass"})
        result = self.run_model(Model())
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.tool_calls, 0)
        self.assertIn("reasoning continuation", result.error)


if __name__ == "__main__":
    unittest.main()
