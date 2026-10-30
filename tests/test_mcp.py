import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.engine import Engine, TERMINAL
from dikdik.mcp import McpError, McpSession, McpStore, StdioClient, HttpClient, tool_name
from dikdik import mcp

SERVER = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
COMMAND = f'"{sys.executable}" "{SERVER}"'


class HttpMcp(BaseHTTPRequestHandler):
    """Streamable HTTP MCP server: JSON for initialize, an event stream for everything else."""
    sessions = []

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        HttpMcp.sessions.append((self.headers.get("Mcp-Session-Id"), self.headers.get("Authorization"), self.headers.get("MCP-Protocol-Version")))
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        if message["method"] == "initialize":
            body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "http-echo"}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "session-123")
        else:
            result = {"tools": [{"name": "ping", "inputSchema": {"type": "object", "properties": {}}}]} if message["method"] == "tools/list" \
                else {"content": [{"type": "text", "text": "pong"}]}
            events = [{"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}, {"jsonrpc": "2.0", "id": message["id"], "result": result}]
            body = "".join(f"event: message\ndata: {json.dumps(e)}\n\n" for e in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        (base / "repo").mkdir()
        self.engine = Engine(base / "repo", base / "data")
        self.addCleanup(self.engine.close)
        self.store = self.engine.mcp

    def test_create_validates_and_starts_disabled(self):
        made = self.store.create({"name": "echo", "transport": "stdio", "command": COMMAND})
        self.assertFalse(self.store.get(made["id"])["enabled"])
        for bad in ({"name": "bad name", "transport": "stdio", "command": "x"},
                    {"name": "a", "transport": "ftp"},
                    {"name": "b", "transport": "http", "url": "http://example.com/mcp"},  # plain HTTP off this machine
                    {"name": "c", "transport": "http", "url": "https://user:pw@example.com/mcp"},
                    {"name": "d", "transport": "stdio", "command": "  "},
                    {"name": "e", "transport": "stdio", "command": "x", "secret_ref": "key"},  # key needs an env var name
                    {"name": "echo", "transport": "stdio", "command": "x"}):
            with self.subTest(bad=bad), self.assertRaises(McpError):
                self.store.create(bad)
        self.store.create({"name": "remote", "transport": "http", "url": "https://mcp.example.com/mcp"})
        self.assertEqual(self.store.set_enabled(made["id"], True), {"id": made["id"], "enabled": True})
        with self.assertRaises(KeyError):
            self.store.delete("missing")
        self.assertEqual([s["name"] for s in self.store.list()], ["echo", "remote"])

    def test_tool_names_are_safe_for_providers(self):
        self.assertEqual(tool_name("git hub", "list.issues"), "mcp__git_hub__list_issues")
        self.assertLessEqual(len(tool_name("x" * 40, "y" * 40)), 64)


class ClientTests(unittest.TestCase):
    def test_stdio_server_lists_and_calls_tools_with_a_minimal_environment(self):
        server = {"name": "echo", "transport": "stdio", "command": COMMAND, "secret_ref": "echo-key", "env_var": "ECHO_KEY"}
        with patch.dict(os.environ, {"DIKDIK_API_KEY": "must-not-leak"}):
            session = McpSession([server], lambda ref: b"sk-echo")
        try:
            self.assertEqual(session.errors, [])
            names = [t["function"]["name"] for t in session.tools]
            self.assertEqual(names, ["mcp__echo__echo", "mcp__echo__add", "mcp__echo__whoami"])
            self.assertEqual(session.tools[0]["function"]["parameters"]["required"], ["text"])
            self.assertEqual(session.call("mcp__echo__echo", {"text": "hi"}), {"is_error": False, "content": "hi"})
            self.assertEqual(session.call("mcp__echo__add", {"a": 2, "b": 3})["content"], "5")
            env = json.loads(session.call("mcp__echo__whoami", {})["content"])
            self.assertEqual(env, {"key": "sk-echo", "dikdik_vars": []})
        finally:
            session.close()

    def test_test_button_reports_server_and_tools(self):
        info = mcp.test_server({"name": "echo", "transport": "stdio", "command": COMMAND}, lambda ref: b"")
        self.assertEqual((info["server"], [t["name"] for t in info["tools"]]), ("echo", ["echo", "add", "whoami"]))

    def test_unreachable_servers_are_reported_not_fatal(self):
        session = McpSession([{"name": "gone", "transport": "stdio", "command": "definitely-not-a-real-command-xyz"},
                              {"name": "dead", "transport": "http", "url": "http://127.0.0.1:9/mcp"}], lambda ref: b"")
        self.assertEqual([e["server"] for e in session.errors], ["gone", "dead"])
        self.assertEqual(session.tools, [])
        session.close()

    def test_streamable_http_with_sessions_and_event_streams(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), HttpMcp)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        HttpMcp.sessions = []
        client = HttpClient(f"http://127.0.0.1:{server.server_port}/mcp", "token-1")
        client.initialize()
        self.assertEqual([t["name"] for t in client.tools()], ["ping"])
        self.assertEqual(client.call("ping", {})["content"][0]["text"], "pong")
        first, *later = HttpMcp.sessions
        self.assertEqual(first, (None, "Bearer token-1", None))
        self.assertTrue(all(s == ("session-123", "Bearer token-1", "2025-06-18") for s in later))


class AgentMcpTests(unittest.TestCase):
    def test_agent_calls_mcp_tools_and_sees_their_output(self):
        session = McpSession([{"name": "echo", "transport": "stdio", "command": COMMAND}], lambda ref: b"")
        self.addCleanup(session.close)

        class Model:
            calls = [ModelResponse("", [ToolCall("1", "mcp__echo__add", {"a": 20, "b": 22})]), ModelResponse("The answer is 42.", [])]

            def respond(self, messages, tools):
                self.tools = [t["function"]["name"] for t in tools]
                self.last = messages
                return self.calls.pop(0)

        model = Model()
        result = run_agent({"a.py": "x = 1\n"}, "Add", model, None, lambda kind, data: None, threading.Event(),
                           extra_tools=session.tools, extra_call=session.call)
        self.assertEqual(result.state, "completed")
        self.assertIn("mcp__echo__add", model.tools)
        self.assertIn('"content":"42"', model.last[-1]["content"])

    def test_engine_connects_enabled_servers_for_agent_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "repo").mkdir()
            (base / "repo" / "a.py").write_text("x = 1\n")
            engine = Engine(base / "repo", base / "data")
            try:
                server = engine.mcp.create({"name": "echo", "transport": "stdio", "command": COMMAND})
                engine.mcp.set_enabled(server["id"], True)
                seen = {}

                class Model:
                    def respond(self, messages, tools):
                        seen["tools"] = [t["function"]["name"] for t in tools]
                        return ModelResponse("Done.", [])

                with patch("dikdik.engine.CompatibleAgentModel", return_value=Model()):
                    task = engine.submit({"objective": "Use tools", "mode": "agent", "verification": "syntax", "approve_external": True})
                    for _ in range(500):
                        data = engine.store.get(task)
                        if data["state"] in TERMINAL and not engine.active:
                            break
                        time.sleep(0.01)
                self.assertEqual(data["state"], "completed", data["result"])
                self.assertIn("mcp__echo__echo", seen["tools"])
                used = next(e for e in data["events"] if e["kind"] == "mcp")
                self.assertEqual((used["data"]["servers"], used["data"]["tools"], used["data"]["errors"]), (["echo"], 3, []))
            finally:
                engine.close()
