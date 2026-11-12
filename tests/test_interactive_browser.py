"""Real browser interaction fixture and public fetch policy regression checks."""
import base64
import json
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
        self.assertEqual({row["id"]: row.get("session") for row in self.engine.store.list()}[result["id"]], "browser")

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
            for _ in range(1200):
                if not self.engine.busy(task): break
                time.sleep(.01)
            self.assertFalse(self.engine.busy(task))
            self.assertTrue({call.name for call in calls}.issubset(model.offered))
            fetch.assert_called_once(); visit.assert_called_once()
            self.assertEqual([call.args[1] for call in action.call_args_list], ["fill", "click", "press", "screenshot"])

    def test_agent_tools_map_to_actions_that_return_the_snapshot(self):
        service = self.engine.browser
        cases = [("browser_snapshot", {}, "snapshot", {"observe": "snapshot"}),
                 ("browser_type", {"ref": "e3", "text": "555"}, "type", {"ref": "e3", "text": "555", "observe": "snapshot"}),
                 ("browser_check", {"label": "Terms"}, "check", {"label": "Terms", "observe": "snapshot"}),
                 ("browser_check", {"label": "Terms", "checked": False}, "uncheck", {"label": "Terms", "observe": "snapshot"}),
                 ("browser_navigate", {"direction": "back"}, "back", {"observe": "snapshot"}),
                 ("browser_resize", {"preset": "mobile", "color_scheme": "dark"}, "resize", {"width": 390, "height": 844, "color_scheme": "dark", "observe": "snapshot"}),
                 ("browser_inspect", {"targets": ["e3", "header"]}, "inspect", {"selectors": ["e3", "header"], "observe": "none"}),
                 ("browser_console", {"level": "error"}, "console", {"level": "error", "observe": "none"}),
                 ("browser_network", {"only_sent": True}, "network", {"only_sent": True, "observe": "none"}),
                 ("browser_evaluate", {"expression": "document.title"}, "evaluate", {"expression": "document.title", "observe": "none"})]
        for tool, arguments, action, sent in cases:
            with self.subTest(tool=tool), patch.object(service, "action", return_value={"status": "passed"}) as call:
                service.agent_action(self.task, tool, arguments)
                self.assertEqual(call.call_args.args[1:3], (action, sent))
        with patch.object(service, "action", return_value={"status": "passed"}) as call:
            service.agent_action(self.task, "browser_screenshot", {"region": [0, 0, 100, 50]})
            self.assertTrue(call.call_args.kwargs["attach"])
        for tool, arguments in [("browser_navigate", {"direction": "sideways"}), ("browser_resize", {"preset": "watch"}), ("browser_unknown", {})]:
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                service.agent_action(self.task, tool, arguments)

    def test_screenshots_reach_the_model_and_hover_leaves_no_history(self):
        service = self.engine.browser
        png = b"\x89PNG\r\n\x1a\nfixture"
        with patch.object(service.sessions, "execute", return_value={"status": "passed", "url": "https://example.com/", "title": "t", "text": "", "png": png}):
            shot = service.action(self.task, "screenshot", {}, attach=True)
            self.assertEqual(shot["attach_images"], ["data:image/png;base64," + base64.b64encode(png).decode()])
            before = len(self.engine.store.get(self.task)["events"])
            service.action(self.task, "hover", {"x": 1, "y": 2, "observe": "none"})
            self.assertEqual(len(self.engine.store.get(self.task)["events"]), before)
            recorded = [e["data"] for e in self.engine.store.get(self.task)["events"] if e["kind"] == "browser_result"]
            self.assertNotIn("attach_images", recorded[-1])

    def test_risk_levels_and_permission_groups_cover_every_browser_tool(self):
        from dikdik.agent import BROWSER_ACTION_TOOLS
        from dikdik.control import ACTION_TOOLS, RULE_GROUPS, assess
        names = {tool["function"]["name"] for tool in BROWSER_ACTION_TOOLS}
        writes = {"browser_click", "browser_fill", "browser_type", "browser_press", "browser_select", "browser_check", "browser_drag", "browser_evaluate"}
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(assess(ToolCall("c", name, {}))[0], "high" if name in writes else "low")
                self.assertIn(name, RULE_GROUPS["browse"])
                self.assertEqual(name in ACTION_TOOLS, name in writes)

    def test_the_app_opens_only_while_it_runs_and_the_proxy_follows_its_port(self):
        from dikdik.host_browser import PublicProxy
        service = self.engine.browser
        approval = {"approve_execution": True, "approve_browser": True}
        with self.assertRaisesRegex(ValueError, "Run preview_app first"):
            service.visit(self.task, "http://preview.dikdik.example/", approval)
        port = {"value": None}
        proxy = PublicProxy(preview_port=lambda: port["value"])
        try:
            self.assertIsNone(proxy.preview_port)
            port["value"] = 43123
            self.assertEqual(proxy.preview_port, 43123)
            port["value"] = 80  # never a privileged or invalid port
            self.assertIsNone(proxy.preview_port)
        finally:
            proxy.close()
        with patch.object(self.engine.live_preview, "status", return_value={"running": True, "port": 43123}):
            self.assertEqual(service.app_port(), 43123)
        with self.assertRaises(KeyError):
            service.live_frames(self.task)  # no page yet: the panel falls back to screenshots


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
                # The live panel's quick click on a link: the page is read after the navigation.
                run("back")
                for _ in range(3):
                    quick = sessions.execute("task", "click", {"selector": "a", "observe": "none"})
                    self.assertEqual((quick["status"], quick.get("png")), ("passed", None), quick)
                    run("back")
                self.assertEqual(sessions.execute("other", "fill", {"selector": "#name", "value": "no"})["status"], "failed")
        finally:
            sessions.close(); server.shutdown(); server.server_close()

    def test_agent_grade_actions_refs_real_keys_records_and_live_frames(self):
        """Everything an agent needs to check inputs and designs, against a real page."""
        html = b'''<!doctype html><html lang="en"><title>Agent fixture</title>
<style>body{background:#fff;color:#111} @media (prefers-color-scheme: dark){body{background:#000;color:#eee}}
#tip{display:none} #hover:hover + #tip{display:block} #drop{width:120px;height:60px;border:1px solid}</style>
<h1>Sign up</h1>
<form id="form"><label for="phone">Phone</label><input id="phone" required>
<label><input type="checkbox" id="terms"> I agree</label>
<select id="plan" aria-label="Plan"><option value="free">Free</option><option value="pro">Pro</option></select>
<button id="send" type="submit">Create account</button></form>
<p id="keys">0 keys</p><p id="result">Waiting</p>
<button id="hover">Help</button><span id="tip" role="tooltip">Shown on hover</span>
<button id="twice">Double me</button><p id="dbl">none</p>
<div id="drag" draggable="true">Card</div><div id="drop">Drop here</div><p id="dropped">empty</p>
<script>
let n = 0; const phone = document.querySelector('#phone');
phone.addEventListener('keydown', () => { n++; document.querySelector('#keys').textContent = n + ' keys'; });
phone.addEventListener('input', () => { phone.value = phone.value.replace(/\\D/g, '').replace(/(\\d{3})(?=\\d)/g, '$1-'); });
document.querySelector('#twice').ondblclick = () => document.querySelector('#dbl').textContent = 'double';
document.querySelector('#drag').ondragstart = e => e.dataTransfer.setData('text', 'card');
const drop = document.querySelector('#drop');
drop.ondragover = e => e.preventDefault();
drop.ondrop = e => { e.preventDefault(); document.querySelector('#dropped').textContent = 'dropped ' + e.dataTransfer.getData('text'); };
document.querySelector('#form').onsubmit = async e => {
  e.preventDefault(); console.error('submitting');
  const r = await fetch('/submit', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({phone: phone.value, terms: document.querySelector('#terms').checked, plan: document.querySelector('#plan').value})});
  document.querySelector('#result').textContent = 'Saved ' + (await r.text());
};
console.log('ready');
</script></html>'''
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(html))); self.end_headers(); self.wfile.write(html)
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(201); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        sessions = BrowserSessions()
        def fixture_open(proxy, url, host, port):
            if host != "dikdik.example": return None
            return socket.create_connection(server.server_address, timeout=5)
        try:
            with patch("dikdik.host_browser._ProxyHandler._open", fixture_open):
                def run(action, **args):
                    result = sessions.execute("task", action, args)
                    self.assertEqual(result["status"], "passed", result)
                    return result
                with sessions.watch("task"):
                    run("navigate", url="http://dikdik.example/", observe="snapshot")
                    frame = sessions.frame("task", 0, timeout=10)
                    self.assertIsNotNone(frame, "the live view streamed no frame")
                    self.assertTrue(frame[1].startswith(b"\xff\xd8"), "frames are JPEG")
                    tree = run("snapshot")["snapshot"]
                    self.assertIn('heading "Sign up" [level=1]', tree)
                    self.assertIn('textbox "Phone" [required]', tree)
                    import re as regex
                    ref = lambda pattern: regex.search(pattern + r'.*?\[ref=(e\d+)\]', tree).group(1)
                    phone, terms, plan, send = ref(r'textbox "Phone"'), ref(r'checkbox "I agree"'), ref(r'combobox "Plan"'), ref(r'button "Create account"')
                    # Real key events: the keydown counter and the input mask both run.
                    typed = run("type", ref=phone, text="5551234567", observe="snapshot")
                    self.assertIn('value="555-123-456-7"', typed["snapshot"])
                    self.assertIn("10 keys", typed["snapshot"])
                    self.assertIn("[checked]", regex.search(r'checkbox "I agree".*', run("check", ref=terms, observe="snapshot")["snapshot"]).group(0))
                    run("select", ref=plan, value="Pro")
                    # The refs stay on their elements across snapshots.
                    self.assertIn(f"[ref={phone}]", run("snapshot")["snapshot"])
                    run("click", ref=send)
                    run("wait", text="Saved ok")
                    self.assertEqual(json.loads(received[-1]), {"phone": "555-123-456-7", "terms": True, "plan": "pro"})
                    sent = run("network", only_sent=True)["network"]
                    self.assertEqual(sent[-1]["method"], "POST"); self.assertEqual(sent[-1]["status"], 201)
                    self.assertIn('"plan":"pro"', sent[-1]["body"])
                    console = run("console")["console"]
                    self.assertEqual([e["text"] for e in console if e["type"] in {"log", "error"}], ["ready", "submitting"])
                    self.assertEqual([e["text"] for e in run("console", level="error")["console"]], ["submitting"])
                    # Hover, double-click and drag, by role and name instead of selectors.
                    self.assertIn("Shown on hover", run("hover", role="button", name="Help")["text"])
                    self.assertIn("double", run("click", role="button", name="Double me", double=True)["text"])
                    self.assertIn("dropped card", run("drag", text="Card", exact=True, to={"text": "Drop here"})["text"])
                    # Design checks: dark mode, computed values and a zoomed region.
                    dark = run("resize", color_scheme="dark", observe="snapshot")
                    self.assertEqual(dark["layout"]["color_scheme"], "dark")
                    self.assertEqual(run("evaluate", expression="getComputedStyle(document.body).backgroundColor")["result"], "rgb(0, 0, 0)")
                    self.assertEqual(run("evaluate", ref=phone, expression="el => el.value")["result"], "555-123-456-7")
                    import struct
                    zoomed = run("screenshot", region=[0, 0, 200, 100])
                    self.assertEqual(struct.unpack(">II", zoomed["png"][16:24]), (200, 100))
                    inspected = run("inspect", selectors=[phone])["inspection"][0]
                    self.assertEqual((inspected["selector"], inspected["found"], inspected["tag"]), (phone, 1, "input"))
                    with self.assertRaises(AssertionError):
                        run("press", key="Control+Alt+Shift+Meta+x")
                    self.assertEqual(run("press", key="ControlOrMeta+a")["status"], "passed")
                    later = sessions.frame("task", frame[0], timeout=10)
                    self.assertIsNotNone(later, "the live view did not follow the page")
                self.assertFalse(sessions.has_page("missing"))
                self.assertTrue(sessions.has_page("task"))
        finally:
            sessions.close(); server.shutdown(); server.server_close()
