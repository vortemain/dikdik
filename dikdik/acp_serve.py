"""ACP (Agent Client Protocol) server mode: expose Dikdik's agent over stdio JSON-RPC.

Other ACP clients (Claude Code, OpenCode, etc.) can launch ``dikdik --acp`` and
talk to the agent through the standard ACP protocol. This is a minimal adapter:
it translates ACP task lifecycle calls to the engine's submit/send/get API.

The protocol is JSON-RPC 2.0 over newline-delimited stdio. Each request is one
JSON object per line; each response is one JSON object per line.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from .engine import Engine, TERMINAL

PROTOCOL_VERSION = "0.3"


def serve(root: Path, data: Path):
    """Run an ACP server on stdin/stdout, routing to a Dikdik engine."""
    engine = Engine(root, data)
    try:
        _loop(engine, sys.stdin, sys.stdout)
    finally:
        engine.close()


def _loop(engine, reader, writer):
    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _reply(writer, {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}, None)
            continue
        method = request.get("method")
        params = request.get("params") or {}
        req_id = request.get("id")
        try:
            result = _dispatch(engine, method, params)
            _reply(writer, {"jsonrpc": "2.0", "result": result}, req_id)
        except Exception as exc:
            _reply(writer, {"jsonrpc": "2.0", "error": {"code": -32000, "message": str(exc)}}, req_id)


def _reply(writer, payload, req_id):
    if req_id is not None:
        payload["id"] = req_id
    writer.write(json.dumps(payload, ensure_ascii=False) + "\n")
    writer.flush()


def _dispatch(engine: Engine, method: str, params: dict) -> dict:
    if method == "initialize":
        return {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tasks": True, "streaming": False},
                "serverInfo": {"name": "dikdik", "version": "2.0"}}
    if method == "task/new":
        request = {"objective": params.get("prompt", ""), "mode": "agent", "verification": "syntax",
                   "approve_external": True}
        task = engine.submit(request)
        return {"taskId": task, "status": _wait(engine, task)}
    if method == "task/send":
        task = params.get("taskId")
        if not task:
            raise ValueError("taskId is required")
        message = params.get("message", {})
        engine.send(task, {"objective": message.get("content", ""), "mode": "agent", "verification": "syntax",
                           "approve_external": True})
        return {"taskId": task, "status": _wait(engine, task)}
    if method == "task/get":
        task = params.get("taskId")
        if not task:
            raise ValueError("taskId is required")
        data = engine.store.get(task)
        return {"taskId": task, "state": data["state"], "result": data.get("result")}
    if method == "shutdown":
        return {"ok": True}
    raise ValueError(f"Unknown method: {method}")


def _wait(engine: Engine, task: str, timeout: float = 30) -> str:
    """Return the task's state, waiting briefly for completion."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = engine.store.get(task)
        state = data["state"]
        with engine.lock:
            busy = bool(engine.active) or bool(engine.pending)
        if not busy and state in TERMINAL:
            return state
        if state in TERMINAL:
            return state
        time.sleep(0.05)
    return engine.store.get(task)["state"]