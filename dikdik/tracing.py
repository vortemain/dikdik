"""OpenTelemetry-compatible traces of agent work, exported over OTLP/HTTP JSON (standard library).

Each agent turn is a trace: a root span for the task, with child spans for every model call and
tool call, built from the agent's own event stream. Spans carry names, durations, counts and
outcomes, never tool arguments, file contents or model text, and every attribute passes through
the log redactor.

Export is off unless `DIKDIK_OTEL_ENDPOINT` names an OTLP/HTTP collector (for example
`http://127.0.0.1:4318` for a local OpenTelemetry Collector, Jaeger or Grafana Alloy). Plain HTTP
is accepted only for loopback; anything else must be HTTPS. Spans are batched on a background
thread with a bounded queue; a slow or absent collector drops spans (counted), never blocks or
fails a task. The task span is also written to the diagnostics log.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from . import __version__, logs

SERVER, INTERNAL, CLIENT = 2, 1, 3  # OTLP span kinds
OK, ERROR = 1, 2


def _attributes(values: dict) -> list[dict]:
    out = []
    for key, value in logs.redact(values).items():
        if isinstance(value, bool):
            out.append({"key": key, "value": {"boolValue": value}})
        elif isinstance(value, int):
            out.append({"key": key, "value": {"intValue": str(value)}})
        elif isinstance(value, float):
            out.append({"key": key, "value": {"doubleValue": value}})
        elif value is not None:
            out.append({"key": key, "value": {"stringValue": str(value)[:1000]}})
    return out


class Span:
    def __init__(self, trace_id: str, name: str, kind: int = INTERNAL, parent: str | None = None, attributes: dict | None = None):
        self.trace_id, self.span_id, self.parent, self.name, self.kind = trace_id, secrets.token_hex(8), parent, name, kind
        self.start, self.end = time.time_ns(), None
        self.attributes, self.status, self.message = dict(attributes or {}), OK, ""

    def finish(self, error: str | None = None, **attributes) -> "Span":
        self.attributes.update(attributes)
        if error:
            self.status, self.message = ERROR, str(error)[:300]
        self.end = time.time_ns()
        return self

    def otlp(self) -> dict:
        return {"traceId": self.trace_id, "spanId": self.span_id, **({"parentSpanId": self.parent} if self.parent else {}),
                "name": self.name, "kind": self.kind, "startTimeUnixNano": str(self.start), "endTimeUnixNano": str(self.end or time.time_ns()),
                "attributes": _attributes(self.attributes),
                "status": {"code": self.status, **({"message": logs.redact(self.message)} if self.message else {})}}


def endpoint_allowed(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}


class Exporter:
    """Batches finished spans and POSTs them to `<endpoint>/v1/traces` as OTLP JSON."""

    def __init__(self, endpoint: str, batch: int = 64, interval: float = 2.0, capacity: int = 2048, timeout: float = 5.0):
        if not endpoint_allowed(endpoint):
            raise ValueError("OTLP endpoint must be https, or http on loopback")
        self.url = endpoint.rstrip("/") + "/v1/traces"
        self.batch, self.interval, self.timeout = batch, interval, timeout
        self.queue: queue.Queue = queue.Queue(maxsize=capacity)
        self.dropped = self.sent = self.failed = 0
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._thread = threading.Thread(target=self._run, name="dikdik-otlp", daemon=True)
        self._thread.start()

    def export(self, span: Span) -> None:
        try:
            self.queue.put_nowait(span)
            self._idle.clear()
        except queue.Full:
            self.dropped += 1

    def _post(self, spans: list[Span]) -> None:
        body = {"resourceSpans": [{"resource": {"attributes": _attributes({"service.name": "dikdik", "service.version": __version__})},
                                   "scopeSpans": [{"scope": {"name": "dikdik.agent", "version": __version__},
                                                   "spans": [span.otlp() for span in spans]}]}]}
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read()
            self.sent += len(spans)
        except OSError:
            self.failed += len(spans)

    def _run(self) -> None:
        while not self._stop.is_set():
            spans = []
            try:
                spans.append(self.queue.get(timeout=self.interval))
                while len(spans) < self.batch:
                    spans.append(self.queue.get_nowait())
            except queue.Empty:
                pass
            if spans:
                self._post(spans)
            if self.queue.empty():
                self._idle.set()

    def flush(self, timeout: float = 10.0) -> bool:
        return self._idle.wait(timeout)

    def close(self) -> None:
        self.flush()
        self._stop.set()
        self._thread.join(timeout=self.interval + 1)


_exporter: Exporter | None = None
_exporter_lock = threading.Lock()


def exporter() -> Exporter | None:
    """The process-wide exporter configured by DIKDIK_OTEL_ENDPOINT, or None."""
    global _exporter
    endpoint = os.environ.get("DIKDIK_OTEL_ENDPOINT", "").strip()
    if not endpoint:
        return None
    with _exporter_lock:
        if _exporter is None or not _exporter.url.startswith(endpoint.rstrip("/")):
            try:
                _exporter = Exporter(endpoint)
            except ValueError:
                logs.log("otlp_endpoint_refused", logging.WARNING, endpoint=endpoint)
                return None
        return _exporter


class TaskTracer:
    """Turns one agent turn's event stream into a trace: task -> model calls and tool calls."""

    def __init__(self, task: str, attributes: dict | None = None, sink: Exporter | None = None):
        self.sink = sink if sink is not None else exporter()
        self.trace_id = secrets.token_hex(16)
        self.root = Span(self.trace_id, "dikdik.task", SERVER, attributes={"dikdik.task_id": task, **(attributes or {})})
        self.model: Span | None = None
        self.tools: dict[str, Span] = {}
        self.counts = {"model_calls": 0, "tool_calls": 0, "tool_failures": 0}

    def _emit(self, span: Span) -> None:
        if self.sink is not None:
            self.sink.export(span)

    def observe(self, kind: str, data: dict) -> None:
        if not isinstance(data, dict):
            return
        phase = data.get("phase")
        if kind == "model":
            if phase == "start":
                self.model = Span(self.trace_id, "dikdik.model_call", CLIENT, self.root.span_id,
                                  {"dikdik.model_call": data.get("model_calls"), "dikdik.context_bytes": data.get("context_bytes")})
                self.counts["model_calls"] += 1
            elif phase in ("end", "error") and self.model is not None:
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                self._emit(self.model.finish(data.get("error") if phase == "error" else None,
                                             **{f"gen_ai.usage.{k}": v for k, v in usage.items() if isinstance(v, (int, float))}))
                self.model = None
        elif kind == "tool" and isinstance(data.get("id"), str):
            if phase == "start":
                self.tools[data["id"]] = Span(self.trace_id, f"dikdik.tool {data.get('name', '')}", INTERNAL, self.root.span_id,
                                              {"dikdik.tool": data.get("name"), "dikdik.tool_call": data.get("tool_calls")})
                self.counts["tool_calls"] += 1
            elif phase == "end" and data["id"] in self.tools:
                failed = data.get("failed") is True
                self.counts["tool_failures"] += failed
                self._emit(self.tools.pop(data["id"]).finish("tool reported a failure" if failed else None, **{"dikdik.failed": failed}))

    def finish(self, state: str, **attributes) -> Span:
        for span in [*self.tools.values(), *([self.model] if self.model else [])]:
            self._emit(span.finish("unfinished when the turn ended"))
        self.tools, self.model = {}, None
        failed = state in {"failed", "blocked", "cancelled", "interrupted"}
        self.root.finish(f"task ended {state}" if failed else None, **{"dikdik.state": state, **self.counts, **attributes})
        self._emit(self.root)
        logs.log("task_trace", trace_id=self.trace_id, state=state,
                 duration_ms=round((self.root.end - self.root.start) / 1e6, 1), **self.counts)
        return self.root
