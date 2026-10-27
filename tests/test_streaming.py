import http.client
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine, TERMINAL
from dikdik.model_config import ModelConfig
from dikdik.providers import _assemble, chat_completion
from dikdik.server import make_server

CONFIG = ModelConfig("https://provider.example/v1/chat/completions", "m", "k", "bearer", 30, {})


def sse(*chunks):
    return [f"data: {json.dumps(c)}\n".encode() for c in chunks] + [b"\n", b"data: [DONE]\n"]


class Streamed:
    def __init__(self, lines, kind="text/event-stream"):
        self.lines, self.headers = lines, {"Content-Type": kind}

    def __iter__(self):
        return iter(self.lines)

    def read(self, size=-1):
        return b"".join(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class AssembleTests(unittest.TestCase):
    def test_text_tool_calls_and_usage(self):
        seen = []
        data = _assemble(sse(
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_", "arguments": "{\"pa"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": "th\": \"a.py\"}"}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"total_tokens": 42}},
        ), seen.append)
        message = data["choices"][0]["message"]
        self.assertEqual(seen, ["Hel", "Hello"])
        self.assertEqual(message["content"], "Hello")
        self.assertEqual(message["tool_calls"], [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}])
        self.assertEqual((data["choices"][0]["finish_reason"], data["usage"]), ("tool_calls", {"total_tokens": 42}))

    def test_limit_and_junk(self):
        with self.assertRaisesRegex(ValueError, "exceeds limit"):
            _assemble([b"data: " + b"x" * 300000 + b"\n"], None)
        self.assertEqual(_assemble([b": comment\n", b"data: not json\n", b"event: ping\n"], None)["choices"][0]["message"], {"content": None})


class TransportTests(unittest.TestCase):
    def test_streams_when_asked(self):
        seen = []
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = Streamed(sse({"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]}))
            data = chat_completion([{"role": "user", "content": "x"}], config=CONFIG, on_text=seen.append)
            sent = json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual((sent["stream"], data["choices"][0]["message"]["content"], seen), (True, "Hi", ["Hi"]))

    def test_rejected_streaming_falls_back_to_a_plain_request(self):
        plain = Streamed([json.dumps({"choices": [{"message": {"content": "plain"}}]}).encode()], "application/json")
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = [urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO()), plain]
            data = chat_completion([], config=CONFIG, on_text=lambda text: None)
            bodies = [json.loads(call.args[0].data) for call in opener.return_value.open.call_args_list]
        self.assertEqual(data["choices"][0]["message"]["content"], "plain")
        self.assertEqual(["stream" in body for body in bodies], [True, False])

    def test_server_errors_are_not_retried(self):
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError("u", 500, "down", {}, io.BytesIO())
            with self.assertRaisesRegex(ValueError, r"provider had an error \(HTTP 500\)"):
                chat_completion([], config=CONFIG, on_text=lambda text: None)
            self.assertEqual(opener.return_value.open.call_count, 1)


class ProviderErrorTests(unittest.TestCase):
    def test_failures_are_named_without_the_key_or_body(self):
        from dikdik.providers import provider_failure
        def http(code, body=b""):
            return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))
        cases = [(http(401), "authentication"), (http(403), "authentication"), (http(404), "not_found"),
                 (http(429, b'{"error": {"type": "insufficient_quota"}}'), "quota"), (http(429, b"not json"), "rate_limit"),
                 (http(422), "bad_request"), (http(503), "provider"), (TimeoutError(), "timeout"),
                 (urllib.error.URLError("refused"), "unreachable"), (ValueError("Provider redirects are disabled"), "redirect")]
        for error, kind in cases:
            with self.subTest(kind=kind):
                failure = provider_failure(error)
                self.assertEqual(failure.kind, kind)
                self.assertIsInstance(failure, ValueError)
        self.assertNotIn("insufficient_quota\"", str(provider_failure(http(429, b'{"error": {"type": "insufficient_quota", "message": "sk-secret"}}'))))
        self.assertNotIn("sk-secret", str(provider_failure(http(401, b"sk-secret"))))


class StreamEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        (base / "repo").mkdir()
        (base / "repo" / "app.py").write_text("x = 1\n")
        self.engine = Engine(base / "repo", base / "data")
        self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "t")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def open_stream(self, task, after=0):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        conn.request("GET", f"/api/tasks/{task}/stream?after={after}", headers={"Authorization": "Bearer t"})
        return conn, conn.getresponse()

    def read_events(self, response):
        events, kind, data = [], None, None
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and kind:
                events.append((kind, data))
                if kind == "end":
                    break
                kind = data = None
        return events

    def test_stream_replays_events_then_ends(self):
        task = self.engine.submit({"objective": "x", "mode": "offline", "verification": "syntax", "edits": [{"path": "app.py", "before": "x = 1", "after": "x = 2"}]})
        conn, response = self.open_stream(task)
        try:
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
            events = self.read_events(response)
        finally:
            conn.close()
        kinds = [data["kind"] for kind, data in events if kind == "task"]
        self.assertEqual(kinds[0], "queued")
        self.assertEqual(kinds[-1], "completed")
        self.assertEqual(events[-1], ("end", {"state": "completed"}))
        last = [data["seq"] for kind, data in events if kind == "task"][-1]
        conn, response = self.open_stream(task, after=last)  # resume after the last seen event
        try:
            self.assertEqual(self.read_events(response), [("end", {"state": "completed"})])
        finally:
            conn.close()

    def test_live_text_is_streamed_while_running(self):
        task = self.engine.submit({"objective": "x", "mode": "offline", "verification": "syntax", "edits": [{"path": "app.py", "before": "x = 1", "after": "x = 2"}]})
        for _ in range(300):
            if self.engine.store.get(task)["state"] in TERMINAL and not self.engine.active:
                break
            time.sleep(0.01)
        with self.engine.lock:
            self.engine.active[task] = threading.Event()  # pretend a turn is running
        self.engine.live[task] = "Thinking about"
        conn, response = self.open_stream(task, after=10_000)
        try:
            seen = []

            def reader():
                seen.extend(self.read_events(response))

            thread = threading.Thread(target=reader)
            thread.start()
            time.sleep(0.5)
            self.engine.live[task] = "Thinking about it"
            time.sleep(0.5)
            self.engine.live.pop(task)
            with self.engine.lock:
                self.engine.active.pop(task)
            thread.join(5)
        finally:
            conn.close()
        self.assertEqual([d["text"] for k, d in seen if k == "delta"], ["Thinking about", "Thinking about it", ""])
        self.assertEqual(seen[-1][0], "end")

    def test_missing_task_and_auth(self):
        conn, response = self.open_stream("missing")
        self.assertEqual(response.status, 404)
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request("GET", "/api/tasks/x/stream")
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()
