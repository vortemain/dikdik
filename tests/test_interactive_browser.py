"""Real browser interaction fixture and public fetch policy regression checks."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import socket
import threading
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from dikdik.interactive_browser import BrowserSessions, web_fetch
from dikdik.engine import Engine
from dikdik.agent import ModelResponse, ToolCall


class FetchTests(unittest.TestCase):
    def test_https_close_uses_tls_transport_after_connection_detaches(self):
        from dikdik.browser_network import fetch_public
        transport, raw = Mock(), Mock()
        raw.settimeout.side_effect = OSError("raw socket transferred to TLS")
        response = Mock(status=200)
        response.isclosed.side_effect = [False, True]
        response.read1.return_value = b"actual HTTPS data"
        response.getheader.side_effect = lambda name, default=None: "text/plain" if name == "Content-Type" else default
        connection = Mock()
        def getresponse():
            connection.sock = None  # HTTPConnection detaches Connection: close responses.
            return response
        connection.getresponse.side_effect = getresponse
        with (patch("dikdik.browser_network.resolve_public", return_value="93.184.216.34"),
              patch("dikdik.browser_network.socket.create_connection", return_value=raw),
              patch("dikdik.browser_network.http.client.HTTPConnection", return_value=connection),
              patch("dikdik.browser_network.ssl.create_default_context") as tls):
            tls.return_value.wrap_socket.return_value = transport
            result = fetch_public("https://example.com/")
        self.assertEqual(base64.b64decode(result["body"]), b"actual HTTPS data")
        transport.settimeout.assert_called_once()
        raw.settimeout.assert_not_called()

    def test_follows_validated_redirects_and_returns_actual_text(self):
        replies = [dict(status=302, headers={"location": "https://example.com/page"}, body=""),
                   dict(status=200, headers={"content-type": "text/html"}, body=base64.b64encode(b"<title>Facts</title><p>Real page data</p>").decode())]
        with patch("dikdik.interactive_browser.fetch_public", side_effect=replies) as fetch:
            result = web_fetch("https://example.com/")
        self.assertEqual(result["text"], "Real page data")
        self.assertTrue(result["untrusted"])
        self.assertEqual(fetch.call_count, 2)

    def test_redirects_cannot_reach_private_hosts(self):
        with patch("dikdik.interactive_browser.fetch_public", return_value=dict(status=302, headers={"location": "http://127.0.0.1/"}, body="")) as fetch:
            with self.assertRaises(ValueError):
                web_fetch("https://example.com/")
        self.assertEqual(fetch.call_count, 1)

    def test_non_text_and_excess_redirects_are_rejected(self):
        with patch("dikdik.interactive_browser.fetch_public", return_value=dict(status=200, headers={"content-type": "application/octet-stream"}, body="")):
            with self.assertRaises(ValueError): web_fetch("https://example.com/")
        with patch("dikdik.interactive_browser.fetch_public", return_value=dict(status=302, headers={"location": "https://example.com/"}, body="")):
            with self.assertRaisesRegex(ValueError, "redirects"): web_fetch("https://example.com/")


class ServiceInteractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        project = base / "project"; project.mkdir()
        (project / "app.py").write_text("value = 1\n")
        self.engine = Engine(project, base / "data")
        self.addCleanup(self.engine.close)
        self.task = self.engine.store.create({"objective": "Test browser tools"})

    def test_action_routes_require_consent_and_keep_task_isolation(self):
        service = self.engine.browser
        with patch.object(service.sessions, "execute", return_value={"status": "passed"}) as action:
            self.assertEqual(service.route("POST", f"/api/tasks/{self.task}/browser/actions", {"action": "click"})[0], 403)
            self.assertEqual(service.route("POST", "/api/tasks/missing/browser/actions", {"action": "click", "approve_execution": True, "approve_browser": True})[0], 404)
            self.assertEqual(service.route("POST", f"/api/tasks/{self.task}/browser/actions", {"action": "click", "arguments": {"selector": "#button"}, "approve_execution": True, "approve_browser": True})[0], 200)
            self.assertEqual(action.call_count, 1)
        service.configure({"enabled": False})
        with self.assertRaises(PermissionError): service.action(self.task, "screenshot", {})
        with self.assertRaises(PermissionError): service.fetch(self.task, "https://example.com/")

    def test_manual_browser_session_does_not_grant_model_disclosure(self):
        service = self.engine.browser
        self.assertEqual(service.route("POST", "/api/browser/sessions", {})[0], 403)
        status, result = service.route("POST", "/api/browser/sessions", {"approve_browser": True, "approve_execution": True})
        self.assertEqual(status, 201)
        task = self.engine.store.get(result["id"])
        self.assertTrue(task["result"]["browser_session"])
        self.assertFalse(task["request"]["approve_external"])

    def test_agent_calls_fetch_and_persistent_actions_through_real_dispatch(self):
        calls = [ToolCall("f", "web_fetch", {"url": "https://example.com/"}),
                 ToolCall("n", "browse_url", {"url": "https://example.com/"}),
                 ToolCall("t", "browser_fill", {"selector": "#name", "value": "Dikdik"}),
                 ToolCall("c", "browser_click", {"selector": "#button"}),
                 ToolCall("k", "browser_press", {"key": "Enter"}),
                 ToolCall("s", "browser_screenshot", {})]
        class Model:
            def __init__(self): self.offered = set(); self.steps = iter(calls)
            def respond(self, messages, tools):
                self.offered.update(t["function"]["name"] for t in tools)
                call = next(self.steps, None)
                return ModelResponse("", [call]) if call else ModelResponse("Done", [])
        model = Model()
        with (patch("dikdik.engine.CompatibleAgentModel", return_value=model),
              patch.object(self.engine.browser, "info", return_value={"interactive": True}),
              patch.object(self.engine.browser, "visit", return_value={"status": "passed", "text": "Actual page"}) as visit,
              patch.object(self.engine.browser, "fetch", return_value={"status": 200, "text": "Actual data", "untrusted": True}) as fetch,
              patch.object(self.engine.browser, "action", return_value={"status": "passed", "text": "Updated page"}) as action):
            task = self.engine.submit({"objective": "Read and interact with the page", "mode": "agent", "verification": "syntax", "approve_external": True, "approve_execution": True, "approve_browser": True, "confirmation": "never"})
            for _ in range(500):
                if not self.engine.busy(task): break
                time.sleep(.01)
            self.assertFalse(self.engine.busy(task))
            self.assertTrue({call.name for call in calls}.issubset(model.offered))
            fetch.assert_called_once(); visit.assert_called_once()
            self.assertEqual([call.args[1] for call in action.call_args_list], ["fill", "click", "press", "screenshot"])


@unittest.skipUnless(os.environ.get("DIKDIK_RUN_INTERACTIVE") == "1", "Opt-in real Chromium test")
class LiveInteractionTests(unittest.TestCase):
    def test_real_dom_fill_click_keyboard_navigation_and_screenshot(self):
        html = b'''<!doctype html><title>Dikdik interactions</title><form id="form"><label>Name <input id="name"></label><select id="choice"><option value="one">One</option><option value="two">Two</option></select><button id="send">Apply locally</button></form><p id="result">Waiting</p><a href="/next">Next page</a><script>document.querySelector('form').onsubmit=e=>{e.preventDefault();document.querySelector('#result').textContent='Hello '+document.querySelector('#name').value+' '+document.querySelector('#choice').value}</script>'''
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<title>Next page</title><p>Navigation worked</p>" if self.path == "/next" else html
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        sessions = BrowserSessions()
        # Only this test fixture host is routed to the local fixture. Production
        # DNS/URL policy remains intact and has separate private-network tests.
        def fixture_open(proxy, url, host, port):
            if host != "dikdik.example": return None
            return socket.create_connection(server.server_address, timeout=5)
        try:
            with patch("dikdik.host_browser._ProxyHandler._open", fixture_open):
                def run(action, **args):
                    result = sessions.execute("task", action, args)
                    self.assertEqual(result["status"], "passed", result)
                    self.assertTrue(result["png"].startswith(b"\x89PNG"))
                    return result
                initial = run("navigate", url="http://dikdik.example/")
                resized = run("resize", width=640, height=720)
                import struct
                self.assertEqual(struct.unpack(">II", resized["png"][16:24]), (640, 720))
                self.assertIn("#name", [element["selector"] for element in initial["elements"]])
                run("fill", selector="#name", value="Dikdik")
                run("select", selector="#choice", value="two")
                run("click", selector="#send")
                self.assertIn("Hello Dikdik two", run("screenshot")["text"])
                run("click", selector="#name")
                run("press", key="ControlOrMeta+A")
                run("type", text="Native")
                self.assertIn("Hello Native two", run("press", key="Enter")["text"])
                self.assertIn("Navigation worked", run("click", selector="a")["text"])
                self.assertIn("Next page", run("back")["text"])
                self.assertIn("Navigation worked", run("forward")["text"])
                self.assertIn("Navigation worked", run("reload")["text"])
                self.assertEqual(sessions.execute("other", "fill", {"selector": "#name", "value": "no"})["status"], "failed")
        finally:
            sessions.close(); server.shutdown(); server.server_close()
