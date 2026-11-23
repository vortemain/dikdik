"""The native Anthropic Messages transport, against a local fake server (no live API call)."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from dikdik import documents, tool_output
from dikdik.agent import ModelResponse, ToolCall
from dikdik.agent_provider import CompatibleAgentModel
from dikdik.anthropic_messages import model_rules, request_body, translate
from dikdik.model_config import ModelConfig
from dikdik.model_errors import ProviderError
from dikdik.providers import chat_completion, prompt_cache_key, usage_breakdown

TOOLS = [{"type": "function", "function": {"name": "read_file", "description": "Read a file",
                                           "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
         {"type": "function", "function": {"name": "finish", "description": "Finish", "parameters": {"type": "object"}}}]
MESSAGES = [{"role": "system", "content": "You are Dikdik."}, {"role": "user", "content": "Read a.py"}]


def events(*items):
    return b"".join(f"event: {item['type']}\ndata: {json.dumps(item)}\n\n".encode() for item in items)


def streamed_reply(*blocks, stop="end_turn", usage=None):
    """A Messages stream: thinking, text and tool_use blocks, the way the API sends them."""
    items = [{"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "content": [],
                                                   "usage": usage or {"input_tokens": 5, "output_tokens": 1}}},
             {"type": "ping"}]
    for index, block in enumerate(blocks):
        if block["type"] == "thinking":
            items += [{"type": "content_block_start", "index": index, "content_block": {"type": "thinking", "thinking": ""}},
                      {"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": block["thinking"]}},
                      {"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": block["signature"]}}]
        elif block["type"] == "text":
            items.append({"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
            for piece in (block["text"][:3], block["text"][3:]):
                items.append({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": piece}})
        else:
            arguments = json.dumps(block["input"])
            items += [{"type": "content_block_start", "index": index, "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}},
                      {"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": arguments[:5]}},
                      {"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": arguments[5:]}}]
        items.append({"type": "content_block_stop", "index": index})
    items += [{"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 40}},
              {"type": "message_stop"}]
    return 200, {"Content-Type": "text/event-stream"}, events(*items)


def error(status, kind, message="nope", headers=None):
    return status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(
        {"type": "error", "error": {"type": kind, "message": message}}).encode()


class FakeAnthropic:
    """A loopback server that records each request and plays back scripted replies."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                reply = owner.replies.pop(0)
                if callable(reply):
                    reply(self)
                    return
                status, headers, data = reply
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def config(self, model="claude-sonnet-5-5", **options):
        return ModelConfig(f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions", model, "sk-ant-test", "bearer", 10,
                           {"provider": "anthropic", "model": model}, {"reasoning_effort": "high", **options})

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TransportTestCase(unittest.TestCase):
    def serve(self, *replies):
        fake = FakeAnthropic(*replies)
        self.addCleanup(fake.close)
        return fake


class RequestShapeTests(TransportTestCase):
    def test_request_has_native_headers_cache_breakpoints_thinking_and_effort(self):
        fake = self.serve(streamed_reply({"type": "text", "text": "Hello there"}))
        chat_completion(MESSAGES, TOOLS, max_tokens=8192, config=fake.config(temperature=0.2), on_text=lambda text: None)
        sent = fake.requests[0]
        body, headers = sent["body"], sent["headers"]
        self.assertEqual(sent["path"], "/v1/messages")
        self.assertEqual(headers["x-api-key"], "sk-ant-test")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(headers["anthropic-beta"], "thinking-binding-controls-2026-08-01")
        self.assertNotIn("authorization", headers)
        self.assertEqual(body["system"], [{"type": "text", "text": "You are Dikdik.", "cache_control": {"type": "ephemeral"}}])
        self.assertEqual([tool["name"] for tool in body["tools"]], ["read_file", "finish"])
        self.assertEqual(body["tools"][0]["input_schema"]["properties"], {"path": {"type": "string"}})
        self.assertNotIn("cache_control", body["tools"][0])
        self.assertEqual(body["tools"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["messages"], [{"role": "user", "content": [
            {"type": "text", "text": "Read a.py", "cache_control": {"type": "ephemeral"}}]}])
        self.assertEqual(body["thinking"], {"type": "adaptive", "display": "summarized",
                                            "block_binding": {"prefix_mismatch_behavior": "drop_block"}})
        self.assertEqual(body["output_config"], {"effort": "high"})
        self.assertTrue(body["stream"])
        self.assertGreaterEqual(body["max_tokens"], 32000)  # adaptive thinking shares the output budget
        for absent in ("temperature", "top_p", "tool_choice", "budget_tokens", "reasoning_effort", "stream_options"):
            self.assertNotIn(absent, body)

    def test_model_rules_follow_each_generation(self):
        self.assertEqual(model_rules("claude-opus-5-5"), {"adaptive": True, "sampling": False, "effort": True, "binding": True})
        self.assertEqual(model_rules("claude-opus-4-7"), {"adaptive": True, "sampling": False, "effort": True, "binding": False})
        self.assertEqual(model_rules("claude-sonnet-4-6"), {"adaptive": True, "sampling": True, "effort": True, "binding": False})
        self.assertEqual(model_rules("claude-fable-5-2")["binding"], True)
        self.assertEqual(model_rules("claude-haiku-4-5-20251001"), {"adaptive": False, "sampling": True, "effort": False, "binding": False})
        self.assertEqual(model_rules("claude-opus-4-20250514")["adaptive"], False)
        self.assertEqual(model_rules("claude-3-5-sonnet-20241022")["adaptive"], False)

    def test_older_models_get_sampling_and_no_thinking_and_a_profile_cap_is_kept(self):
        config = ModelConfig("https://api.anthropic.com/v1/chat/completions", "claude-haiku-4-5", "sk-ant-test", "bearer", 10,
                             {}, {"temperature": 0.3, "top_p": 0.9, "reasoning_effort": "low", "max_output_tokens": 2000})
        body, headers = request_body(MESSAGES, None, 2000, config, stream=False)
        self.assertEqual((body["temperature"], body["max_tokens"], headers), (0.3, 2000, {}))
        for absent in ("top_p", "thinking", "output_config", "tools", "stream"):
            self.assertNotIn(absent, body)
        minimal = ModelConfig("https://api.anthropic.com/v1/chat/completions", "claude-opus-5-5", "sk-ant-test", "bearer", 10,
                              {}, {"reasoning_effort": "minimal"})
        self.assertEqual(request_body(MESSAGES, None, 100, minimal, stream=False)[0]["output_config"], {"effort": "low"})

    def test_rolling_breakpoints_mark_the_two_newest_turns_and_never_a_thinking_block(self):
        history = [*MESSAGES,
                   {"role": "assistant", "content": "", "tool_calls": [{"id": "toolu_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}],
                    "reasoning_details": [{"type": "anthropic.thinking", "thinking": "Look first.", "signature": "sig-1"}]},
                   {"role": "tool", "tool_call_id": "toolu_1", "content": "print(1)"}]
        body, _ = request_body(history, TOOLS, 1000, self.serve().config(), stream=True)
        marks = [(turn["role"], [block["type"] for block in turn["content"] if "cache_control" in block]) for turn in body["messages"]]
        self.assertEqual(marks, [("user", []), ("assistant", ["tool_use"]), ("user", ["tool_result"])])
        self.assertLessEqual(json.dumps(body).count("cache_control"), 4)


class TranslationTests(unittest.TestCase):
    def test_tool_calls_results_images_and_late_system_notes(self):
        system, turns = translate([
            {"role": "system", "content": "Rules."},
            {"role": "user", "content": [{"type": "text", "text": "What is this?"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]},
            {"role": "assistant", "content": "Checking.", "tool_calls": [
                {"id": "toolu_a", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a\"}"}},
                {"id": "toolu_b", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"b\"}"}}],
             "reasoning_details": [{"type": "anthropic.thinking", "thinking": "Two files.", "signature": "s1"},
                                   {"type": "anthropic.redacted_thinking", "data": "opaque"},
                                   {"type": "reasoning.text", "text": "from another provider"}]},
            {"role": "tool", "tool_call_id": "toolu_a", "content": "A"},
            {"role": "tool", "tool_call_id": "toolu_b", "content": "B"},
            {"role": "user", "content": "Now compare."},
            {"role": "system", "content": "Be brief."},
        ])
        self.assertEqual(system, [{"type": "text", "text": "Rules."}])
        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant", "user"])
        self.assertEqual(turns[0]["content"][1], {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}})
        self.assertEqual(turns[1]["content"], [
            {"type": "thinking", "thinking": "Two files.", "signature": "s1"},
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "toolu_a", "name": "read_file", "input": {"path": "a"}},
            {"type": "tool_use", "id": "toolu_b", "name": "read_file", "input": {"path": "b"}}])
        self.assertEqual([block["type"] for block in turns[2]["content"]], ["tool_result", "tool_result", "text", "text"])
        self.assertEqual(turns[2]["content"][0], {"type": "tool_result", "tool_use_id": "toolu_a", "content": "A"})
        self.assertEqual(turns[2]["content"][3]["text"], "[Dikdik runtime note]\nBe brief.")

    def test_a_conversation_never_starts_with_the_assistant(self):
        _, turns = translate([{"role": "assistant", "content": "Earlier reply."}, {"role": "user", "content": "Go on"}])
        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant", "user"])


class StreamingTests(TransportTestCase):
    def test_stream_types_live_and_returns_tool_calls_thinking_and_cached_usage(self):
        fake = self.serve(streamed_reply(
            {"type": "thinking", "thinking": "The user wants a.py.", "signature": "EqQBCkgIBRAB"},
            {"type": "text", "text": "Reading it."},
            {"type": "tool_use", "id": "toolu_01", "name": "read_file", "input": {"path": "a.py"}},
            stop="tool_use", usage={"input_tokens": 12, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 200, "output_tokens": 1}))
        typed = []
        data = chat_completion(MESSAGES, TOOLS, config=fake.config(), on_text=typed.append)
        self.assertEqual(typed, ["Rea", "Reading it."])
        choice = data["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["content"], "Reading it.")
        self.assertEqual(choice["message"]["tool_calls"], [{"id": "toolu_01", "type": "function",
                                                            "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}])
        self.assertEqual(choice["message"]["reasoning_content"], "The user wants a.py.")
        self.assertEqual(choice["message"]["reasoning_details"],
                         [{"type": "anthropic.thinking", "thinking": "The user wants a.py.", "signature": "EqQBCkgIBRAB"}])
        self.assertEqual(usage_breakdown(data), {"input_tokens": 3212, "output_tokens": 40, "cache_read_tokens": 3000,
                                                 "cache_write_tokens": 200, "total_tokens": 3252})

    def test_tool_round_trip_replays_thinking_unchanged_through_the_agent_adapter(self):
        fake = self.serve(
            streamed_reply({"type": "thinking", "thinking": "Need the file.", "signature": "sig-AAA"},
                           {"type": "tool_use", "id": "toolu_01", "name": "read_file", "input": {"path": "a.py"}}, stop="tool_use"),
            streamed_reply({"type": "text", "text": "It prints 1."}))
        live = []
        model = CompatibleAgentModel(True, fake.config, on_text=live.append)
        first = model.respond(MESSAGES, TOOLS)
        self.assertIsInstance(first, ModelResponse)
        self.assertEqual(first.tool_calls, [ToolCall("toolu_01", "read_file", {"path": "a.py"})])
        self.assertEqual(first.reasoning, "Need the file.")
        self.assertEqual(first.reasoning_context, {"reasoning_details": [
            {"type": "anthropic.thinking", "thinking": "Need the file.", "signature": "sig-AAA"}]})
        assistant = {"role": "assistant", "content": first.content, "tool_calls": [
            {"id": "toolu_01", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"a.py\"}"}}],
            **first.reasoning_context}
        second = model.respond([*MESSAGES, assistant, {"role": "tool", "tool_call_id": "toolu_01", "content": "print(1)"}], TOOLS)
        self.assertEqual(second.content, "It prints 1.")
        replayed = fake.requests[1]["body"]["messages"]
        self.assertEqual(replayed[1]["content"][0], {"type": "thinking", "thinking": "Need the file.", "signature": "sig-AAA"})
        self.assertEqual(replayed[1]["content"][1]["type"], "tool_use")
        self.assertEqual(replayed[2]["content"][0]["type"], "tool_result")
        self.assertIn("It prints 1.", live)

    def test_plain_request_without_live_text(self):
        reply = {"id": "msg_1", "type": "message", "role": "assistant", "stop_reason": "end_turn",
                 "content": [{"type": "text", "text": "OK"}], "usage": {"input_tokens": 9, "output_tokens": 1}}
        fake = self.serve((200, {"Content-Type": "application/json"}, json.dumps(reply).encode()))
        data = chat_completion([{"role": "user", "content": "Reply OK"}], None, max_tokens=16, config=fake.config())
        self.assertEqual(data["choices"][0]["message"]["content"], "OK")
        self.assertNotIn("stream", fake.requests[0]["body"])
        self.assertNotIn("tools", fake.requests[0]["body"])

    def test_pause_turn_is_resumed_with_the_partial_reply(self):
        fake = self.serve(streamed_reply({"type": "text", "text": "Part one. "}, stop="pause_turn"),
                          streamed_reply({"type": "text", "text": "Part two."}))
        typed = []
        data = chat_completion(MESSAGES, None, config=fake.config(), on_text=typed.append)
        self.assertEqual(data["choices"][0]["message"]["content"], "Part one. Part two.")
        self.assertEqual(typed[-1], "Part one. Part two.")
        self.assertEqual(fake.requests[1]["body"]["messages"][-1]["role"], "assistant")
        self.assertEqual(data["usage"]["completion_tokens"], 80)

    def test_cancel_stops_a_silent_stream(self):
        started = threading.Event()

        def stall(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(events({"type": "message_start", "message": {"content": [], "usage": {}}}))
            handler.wfile.flush()
            started.set()
            time.sleep(3)
        fake = self.serve(stall)
        cancel = threading.Event()
        threading.Thread(target=lambda: (started.wait(5), cancel.set()), daemon=True).start()
        begun = time.monotonic()
        with self.assertRaises(ProviderError) as caught:
            chat_completion(MESSAGES, None, config=fake.config(), on_text=lambda text: None, cancel=cancel)
        self.assertEqual(caught.exception.kind, "cancelled")
        self.assertLess(time.monotonic() - begun, 2.5)


class StopAndErrorTests(TransportTestCase):
    def test_max_tokens_is_an_output_limit_and_refusal_a_content_filter(self):
        fake = self.serve(streamed_reply({"type": "text", "text": "Cut off"}, stop="max_tokens"),
                          streamed_reply({"type": "text", "text": "No."}, stop="refusal"))
        model = CompatibleAgentModel(True, fake.config)
        with self.assertRaises(ProviderError) as cut:
            model.respond(MESSAGES, TOOLS)
        self.assertEqual(cut.exception.kind, "output_limit")
        with self.assertRaises(ProviderError) as refused:
            chat_completion(MESSAGES, None, config=fake.config(), on_text=lambda text: None)
        self.assertEqual(refused.exception.kind, "content_filter")

    def test_http_errors_map_to_provider_error_kinds(self):
        cases = [(error(401, "authentication_error"), "authentication"), (error(403, "permission_error"), "authentication"),
                 (error(404, "not_found_error"), "not_found"), (error(429, "rate_limit_error", headers={"Retry-After": "2"}), "rate_limit"),
                 (error(529, "overloaded_error"), "provider"), (error(500, "api_error"), "provider"),
                 (error(413, "request_too_large"), "context_length"),
                 (error(400, "invalid_request_error", "prompt is too long: 1200000 tokens > 1000000 maximum"), "context_length"),
                 (error(400, "invalid_request_error", "max_tokens: bad"), "bad_request"),
                 (error(400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API."), "quota")]
        for reply, kind in cases:
            with self.subTest(kind=kind, status=reply[0]):
                fake = self.serve(reply)
                with self.assertRaises(ProviderError) as caught:
                    chat_completion(MESSAGES, None, config=fake.config(), on_text=lambda text: None)
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(len(fake.requests), 1)  # no fallback to an unstreamed retry
                self.assertNotIn("sk-ant-test", str(caught.exception))

    def test_an_error_event_mid_stream_is_a_transient_provider_error(self):
        fake = self.serve((200, {"Content-Type": "text/event-stream"}, events(
            {"type": "message_start", "message": {"content": [], "usage": {}}},
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})))
        with self.assertRaises(ProviderError) as caught:
            chat_completion(MESSAGES, None, config=fake.config(), on_text=lambda text: None)
        self.assertEqual((caught.exception.kind, caught.exception.retryable), ("provider", True))

    def test_a_stream_without_message_stop_is_interrupted(self):
        fake = self.serve((200, {"Content-Type": "text/event-stream"}, events(
            {"type": "message_start", "message": {"content": [], "usage": {}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Half"}})))
        with self.assertRaises(ProviderError) as caught:
            chat_completion(MESSAGES, None, config=fake.config(), on_text=lambda text: None)
        self.assertEqual(caught.exception.kind, "stream_interrupted")

    def test_a_rejected_thinking_signature_is_stripped_and_retried_once(self):
        fake = self.serve(error(400, "invalid_request_error", "messages.1.content.0: Invalid `signature` in `thinking` block."),
                          streamed_reply({"type": "text", "text": "Recovered."}))
        history = [*MESSAGES, {"role": "assistant", "content": "", "reasoning_details": [
            {"type": "anthropic.thinking", "thinking": "x", "signature": "stale"}], "tool_calls": [
            {"id": "toolu_1", "type": "function", "function": {"name": "finish", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "toolu_1", "content": "ok"}]
        data = chat_completion(history, TOOLS, config=fake.config(), on_text=lambda text: None)
        self.assertEqual(data["choices"][0]["message"]["content"], "Recovered.")
        self.assertIn("thinking", json.dumps(fake.requests[0]["body"]["messages"]))
        self.assertNotIn("\"thinking\"", json.dumps(fake.requests[1]["body"]["messages"]))


class OtherProviderTests(unittest.TestCase):
    def test_prompt_cache_key_only_for_openai_and_stable_per_prefix(self):
        openai = ModelConfig("https://api.openai.com/v1/chat/completions", "gpt-5", "sk-test", "bearer", 10, {})
        other = ModelConfig("https://integrate.api.nvidia.com/v1/chat/completions", "m", "nv-test", "bearer", 10, {})
        key = prompt_cache_key(openai, MESSAGES, TOOLS)
        self.assertTrue(key.startswith("dikdik-"))
        self.assertEqual(key, prompt_cache_key(openai, [*MESSAGES, {"role": "user", "content": "more"}], TOOLS))
        self.assertNotEqual(key, prompt_cache_key(openai, [{"role": "system", "content": "Other."}], TOOLS))
        self.assertIsNone(prompt_cache_key(other, MESSAGES, TOOLS))
        self.assertNotIn("Dikdik", key)

    def test_openai_compatible_providers_keep_the_chat_completions_path(self):
        reply = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}

        class Response:
            headers = {"Content-Type": "application/json"}

            def read(self, size=-1):
                return json.dumps(reply).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        for url, cached in (("https://api.openai.com/v1/chat/completions", True), ("https://openrouter.ai/api/v1/chat/completions", False)):
            config = ModelConfig(url, "anthropic/claude-sonnet-5.5" if "openrouter" in url else "gpt-5", "sk-test", "bearer", 10, {})
            with self.subTest(url=url), patch("urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = Response()
                chat_completion(MESSAGES, TOOLS, config=config)
                request = opener.return_value.open.call_args.args[0]
                body = json.loads(request.data)
                self.assertTrue(request.full_url.endswith("/chat/completions"))
                self.assertEqual("prompt_cache_key" in body, cached)
                self.assertEqual(body["tool_choice"], "auto")
                if not cached:
                    self.assertEqual(body["messages"][0]["content"][0]["cache_control"], {"type": "ephemeral"})


class ToolOutputTests(unittest.TestCase):
    def test_small_results_are_unchanged(self):
        self.assertEqual(tool_output.content({"a": 1}, "{\"a\":1}"), "{\"a\":1}")

    def test_large_results_keep_head_and_tail_and_the_saved_file_reopens_with_read_document(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            page = "".join(f"line {number}: {'é' * 40}\n" for number in range(3000))
            result = {"status": "ok", "output": page}
            text = tool_output.content(result, json.dumps(result), tool_output.saver(root))
            self.assertLess(len(text.encode()), tool_output.INLINE_LIMIT)
            self.assertTrue(text.startswith("[Tool output was "))
            self.assertIn("line 0: ", text)
            self.assertIn("line 2999: ", text)
            self.assertNotIn("line 1500: ", text)
            self.assertIn("é" * 40, text)  # readable, not JSON-escaped
            path = text.split("`")[1]
            self.assertTrue(path.startswith("tool-output/") and path.endswith(".txt"))
            self.assertEqual(tool_output.content(result, json.dumps(result), tool_output.saver(root)), text)  # same hash, same file
            found = documents.read(root, {"path": path, "find": "line 1500:"})
            self.assertEqual(found["matches"], 1)
            paged = documents.read(root, {"path": path, "offset": 0, "max_chars": 20000})
            self.assertIsNotNone(paged["next_offset"])

    def test_without_a_folder_the_stub_says_the_middle_was_not_kept(self):
        text = tool_output.bounded("x" * 100000)
        self.assertIn("The middle was not kept", text)
        self.assertLess(len(text), 30000)

    def test_a_read_document_page_fits_inline(self):
        self.assertLessEqual(documents.READ_LIMIT + 4000, tool_output.INLINE_LIMIT)


class ChatPromptTests(unittest.TestCase):
    def setUp(self):
        from dikdik.engine import Engine
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        (self.base / "project").mkdir()
        self.engine = Engine(self.base / "project", self.base / "data")
        self.addCleanup(self.engine.close)

    def run_chat(self, model, objective="Summarise the report"):
        from dikdik.engine import TERMINAL
        from unittest.mock import Mock
        adapter = Mock()
        adapter.respond.side_effect = model
        with patch("dikdik.engine.CompatibleAgentModel", return_value=adapter):
            task = self.engine.submit({"objective": objective, "mode": "chat", "verification": "syntax", "approve_external": True})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                data = self.engine.store.get(task)
                if data["state"] in TERMINAL and not self.engine.busy(task):
                    return task, data, adapter.respond.call_args_list
                time.sleep(0.02)
        self.fail("chat did not finish")

    def test_the_date_moves_from_the_system_prompt_to_the_newest_user_turn(self):
        _, data, calls = self.run_chat(lambda messages, tools: ModelResponse("Hello!", [], usage={"input_tokens": 900, "cache_read_tokens": 800, "output_tokens": 5}))
        messages = calls[0].args[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertNotIn("Today is", messages[0]["content"])
        dated = [index for index, message in enumerate(messages) if "Today is " + time.strftime("%A %d %B %Y") in message["content"]]
        self.assertEqual(len(dated), 1)
        self.assertGreaterEqual(dated[0], len(messages) - 3)  # just before this turn's message
        self.assertEqual(messages[-1]["content"], "Summarise the report")
        end = next(e["data"] for e in data["events"] if e["kind"] == "agent_model" and e["data"].get("phase") == "end")
        self.assertEqual(end["usage"]["cache_read_tokens"], 800)

    def test_a_large_tool_result_is_saved_in_the_chat_folder_but_not_listed_as_a_deliverable(self):
        turns = iter([ModelResponse("", [ToolCall("1", "read_document", {"path": "attachments/report.txt"})]),
                      ModelResponse("Done.", [])])
        seen = []

        def model(messages, tools):
            seen.append(messages)
            return next(turns)
        huge = {"path": "attachments/report.txt", "text": "".join(f"row {n}\n" for n in range(20000))}
        with patch("dikdik.documents.read", return_value=huge):
            task, data, _ = self.run_chat(model)
        self.assertEqual(data["state"], "completed", data.get("result"))
        stub = next(m["content"] for m in seen[-1] if m.get("role") == "tool")
        self.assertLess(len(stub.encode()), tool_output.INLINE_LIMIT)
        path = stub.split("`")[1]
        self.assertIn("row 19999", (self.base / "data" / "chats" / task / path).read_text(encoding="utf-8"))
        self.assertEqual([f for f in self.engine.chat_files(task) if f["path"].startswith("tool-output/")], [])


if __name__ == "__main__":
    unittest.main()
