"""Agent turns become OTLP traces that a real HTTP collector receives, without sensitive data."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from dikdik import tracing
from dikdik.agent import ModelResponse, ToolCall


class Collector:
    """A minimal OTLP/HTTP receiver: records every POST /v1/traces body."""

    def __init__(self):
        self.bodies = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.bodies.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def spans(self):
        return [span for _, body in self.bodies for rs in body["resourceSpans"] for ss in rs["scopeSpans"] for span in ss["spans"]]

    def close(self):
        self.server.shutdown(); self.server.server_close()


def attrs(span):
    return {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}


class TracingTests(unittest.TestCase):
    def setUp(self):
        self.collector = Collector()
        self.addCleanup(self.collector.close)
        self.exporter = tracing.Exporter(self.collector.url, interval=0.05)
        self.addCleanup(self.exporter.close)

    def test_events_become_a_parented_trace_on_a_real_collector(self):
        tracer = tracing.TaskTracer("task-1", {"dikdik.mode": "local"}, sink=self.exporter)
        tracer.observe("model", {"phase": "start", "model_calls": 1, "context_bytes": 900})
        tracer.observe("model", {"phase": "end", "usage": {"input_tokens": 120, "output_tokens": 30}})
        tracer.observe("tool", {"phase": "start", "id": "c1", "name": "read_file", "tool_calls": 1,
                                "arguments": {"path": "secret-plan.txt"}})
        tracer.observe("tool", {"phase": "end", "id": "c1", "name": "read_file", "failed": True, "observation": {"text": "file body"}})
        root = tracer.finish("completed", **{"dikdik.tests_verified": True})
        self.assertTrue(self.exporter.flush(5))
        path = self.collector.bodies[0][0]
        self.assertEqual(path, "/v1/traces")
        spans = {s["name"]: s for s in self.collector.spans()}
        self.assertEqual(set(spans), {"dikdik.task", "dikdik.model_call", "dikdik.tool read_file"})
        self.assertEqual({s["traceId"] for s in spans.values()}, {root.trace_id})
        for child in ("dikdik.model_call", "dikdik.tool read_file"):
            self.assertEqual(spans[child]["parentSpanId"], root.span_id)
        self.assertEqual(attrs(spans["dikdik.model_call"])["gen_ai.usage.input_tokens"], "120")
        self.assertEqual(spans["dikdik.tool read_file"]["status"]["code"], tracing.ERROR)
        task = attrs(spans["dikdik.task"])
        self.assertEqual((task["dikdik.state"], task["model_calls"], task["tool_failures"], task["dikdik.tests_verified"]),
                         ("completed", "1", "1", True))
        sent = json.dumps(self.collector.bodies)
        self.assertNotIn("secret-plan.txt", sent)  # arguments and observations never leave the process
        self.assertNotIn("file body", sent)
        resource = attrs({"attributes": self.collector.bodies[0][1]["resourceSpans"][0]["resource"]["attributes"]})
        self.assertEqual(resource["service.name"], "dikdik")

    def test_unfinished_spans_close_with_the_task_and_failures_mark_the_root(self):
        tracer = tracing.TaskTracer("t", sink=self.exporter)
        tracer.observe("tool", {"phase": "start", "id": "c9", "name": "run_command"})
        tracer.finish("failed")
        self.exporter.flush(5)
        spans = {s["name"]: s for s in self.collector.spans()}
        self.assertEqual(spans["dikdik.tool run_command"]["status"]["message"], "unfinished when the turn ended")
        self.assertEqual(spans["dikdik.task"]["status"]["code"], tracing.ERROR)

    def test_endpoint_policy_and_an_absent_collector(self):
        self.assertTrue(tracing.endpoint_allowed("http://127.0.0.1:4318"))
        self.assertTrue(tracing.endpoint_allowed("https://otel.example.com"))
        self.assertFalse(tracing.endpoint_allowed("http://otel.example.com"))
        with self.assertRaises(ValueError):
            tracing.Exporter("http://collector.internal:4318")
        dead = tracing.Exporter("http://127.0.0.1:9", interval=0.05, timeout=0.5)  # nothing listens on the discard port
        tracer = tracing.TaskTracer("t", sink=dead)
        tracer.finish("completed")
        self.assertTrue(dead.flush(10))
        self.assertEqual((dead.sent, dead.failed), (0, 1))  # counted, never raised
        dead.close()

    def test_without_an_endpoint_tracing_is_off(self):
        with patch.dict("os.environ", {"DIKDIK_OTEL_ENDPOINT": ""}):
            self.assertIsNone(tracing.exporter())
            self.assertIsNone(tracing.TaskTracer("t").sink)

    def test_the_engine_traces_a_real_agent_turn(self):
        from dikdik.engine import Engine
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            engine = Engine(root, Path(temp) / "data")
            try:
                class Script:
                    def __init__(self):
                        self.replies = iter([ModelResponse("", [ToolCall("e", "edit_file", {"path": "calc.py", "before": "a - b", "after": "a + b"})]),
                                             ModelResponse("Done.", [])])
                    def respond(self, messages, tools):
                        return next(self.replies)
                with patch.dict("os.environ", {"DIKDIK_OTEL_ENDPOINT": self.collector.url}), \
                        patch("dikdik.tracing._exporter", None), patch("dikdik.engine.CompatibleAgentModel", return_value=Script()):
                    task = engine.submit({"objective": "Fix add", "mode": "agent", "verification": "syntax",
                                          "approve_external": True, "approve_execution": False})
                    for _ in range(200):
                        if engine.store.status(task) not in {"queued", "running"}:
                            break
                        threading.Event().wait(0.05)
                    tracing._exporter.flush(5) if tracing._exporter else None
                    exported = tracing._exporter
                spans = {s["name"] for s in self.collector.spans()}
                self.assertTrue({"dikdik.task", "dikdik.tool edit_file"} <= spans, spans)
                if exported:
                    exported.close()
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
