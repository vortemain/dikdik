import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import Engine, TERMINAL
from dikdik.server import make_server, session_token


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "repo"
        root.mkdir()
        (root / "app.py").write_text("x = 1\n")
        self.engine = Engine(root, base / "data")
        self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def call(self, method, path, body=None, headers=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            h = {"Authorization": "Bearer test-token"} if auth else {}
            h.update(headers or {})
            conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_static_app_and_security_headers(self):
        for path in ["/", "/app.js", "/style.css", "/icon.svg"]:
            with self.subTest(path=path):
                status, headers, content = self.call("GET", path, auth=False)
                self.assertEqual(status, 200)
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertTrue(content)

    def test_api_needs_token(self):
        self.assertEqual(self.call("GET", "/api/project", auth=False)[0], 401)

    def test_session_secret_persists_until_rotated(self):
        path = Path(self.temp.name) / "web.token"
        first = session_token(path)
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(session_token(path), first)
        self.assertNotEqual(session_token(path, rotate=True), first)

    def test_active_model_is_described_without_secrets(self):
        self.assertEqual(self.call("GET", "/api/model", auth=False)[0], 401)
        with patch.dict("os.environ", {}, clear=True):
            status, _, body = self.call("GET", "/api/model")
        self.assertEqual(status, 200)
        info = json.loads(body)
        self.assertFalse(info["configured"])
        self.assertIn("Settings", info["problem"])

    def wait_terminal(self, task):
        for _ in range(300):
            data = json.loads(self.call("GET", "/api/tasks/" + task)[2])
            if data["state"] in TERMINAL and not self.engine.active:
                return data
            time.sleep(.01)
        self.fail("task did not finish")

    def test_conversation_routes(self):
        edit = lambda before, after: {"mode": "offline", "verification": "syntax", "edits": [{"path": "app.py", "before": before, "after": after}]}
        status, _, body = self.call("POST", "/api/tasks", {"objective": "Change x", **edit("x = 1", "x = 2")})
        task = json.loads(body)["id"]
        self.wait_terminal(task)
        status, _, body = self.call("POST", f"/api/tasks/{task}/messages", {"objective": "Again", **edit("x = 2", "x = 3")})
        self.assertEqual((status, json.loads(body)), (202, {"id": task, "queued": False}))
        data = self.wait_terminal(task)
        self.assertIn("+x = 3", data["result"]["diff"])
        self.assertEqual(self.call("POST", "/api/tasks/missing/messages", {"objective": "x", **edit("a", "b")})[0], 404)
        status, _, body = self.call("PATCH", "/api/tasks/" + task, {"title": "Renamed", "pinned": True})
        self.assertEqual(status, 200)
        listed = json.loads(self.call("GET", "/api/tasks")[2])[0]
        self.assertEqual((listed["title"], listed["pinned"]), ("Renamed", True))
        self.assertEqual(self.call("PATCH", "/api/tasks/" + task, {"state": "failed"})[0], 400)
        self.assertEqual(self.call("PATCH", "/api/tasks/missing", {"title": "x"})[0], 404)
        self.assertEqual(self.call("DELETE", "/api/tasks/" + task)[0], 200)
        self.assertEqual(self.call("GET", "/api/tasks/" + task)[0], 404)
        self.assertEqual(self.call("DELETE", "/api/tasks/" + task)[0], 404)

    def test_apply_files_and_git_routes(self):
        status, _, body = self.call("POST", "/api/tasks", {"objective": "Change x", "mode": "offline", "verification": "syntax", "edits": [{"path": "app.py", "before": "x = 1", "after": "x = 2"}]})
        task = json.loads(body)["id"]
        self.wait_terminal(task)
        content = json.loads(self.call("GET", f"/api/files/content?path=app.py&task={task}")[2])
        original = (self.engine.root / "app.py").read_bytes()  # CRLF on Windows: compare real bytes
        self.assertEqual((content["version"], content["text"].strip()), ("candidate", "x = 2"))
        self.assertEqual(json.loads(self.call("GET", "/api/files/content?path=app.py")[2])["text"].strip(), "x = 1")
        self.assertEqual(self.call("GET", "/api/files/content?path=nope.py")[0], 404)
        self.assertEqual(self.call("POST", f"/api/tasks/{task}/apply", {})[0], 400)  # needs explicit confirmation
        (self.engine.root / "app.py").write_text("x = 9\n")
        status, _, body = self.call("POST", f"/api/tasks/{task}/apply", {"confirm": True})
        self.assertEqual((status, json.loads(body)["conflicts"]), (409, ["app.py"]))
        (self.engine.root / "app.py").write_bytes(original)
        status, _, body = self.call("POST", f"/api/tasks/{task}/apply", {"confirm": True})
        self.assertEqual((status, json.loads(body)["applied"]), (200, ["app.py"]))
        self.assertEqual((self.engine.root / "app.py").read_bytes(), original.replace(b"1", b"2"))
        self.assertEqual(self.call("POST", f"/api/tasks/{task}/apply", ["not", "an", "object"])[0], 400)
        git = json.loads(self.call("GET", "/api/git")[2])
        self.assertIn("repo", git)
        self.assertEqual(self.call("POST", "/api/git/commit", {"message": "", "files": ["app.py"]})[0], 400)

    def test_run_control_routes(self):
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        status, _, body = self.call("POST", "/api/attachments", {"name": "dot.png", "type": "image/png", "data": base64.b64encode(png).decode()})
        self.assertEqual(status, 201)
        attachment = json.loads(body)["id"]
        status, headers, content = self.call("GET", f"/api/attachments/{attachment}")
        self.assertEqual((status, headers["Content-Type"], content), (200, "image/png", png))
        self.assertEqual(self.call("GET", "/api/attachments/" + "0" * 32)[0], 404)
        self.assertEqual(json.loads(self.call("GET", "/api/settings/app")[2])["confirmation"], "never")
        self.assertEqual(json.loads(self.call("PATCH", "/api/settings/app", {"condenser": False})[2])["condenser"], False)
        self.assertEqual(self.call("PATCH", "/api/settings/app", {"condenser": "no"})[0], 400)
        status, _, body = self.call("POST", "/api/tasks", {"objective": "Change x", "mode": "offline", "verification": "syntax", "edits": [{"path": "app.py", "before": "x = 1", "after": "x = 2"}]})
        task = json.loads(body)["id"]
        self.wait_terminal(task)
        self.assertEqual(json.loads(self.call("POST", f"/api/tasks/{task}/pause", {})[2]), {"requested": False})
        status, _, body = self.call("POST", f"/api/tasks/{task}/fork", {})
        self.assertEqual(status, 201)
        fork = json.loads(body)["id"]
        self.assertIn("+x = 2", json.loads(self.call("GET", f"/api/tasks/{fork}")[2])["result"]["diff"])
        self.assertEqual(self.call("POST", f"/api/tasks/{task}/ask", {"question": "Why?"})[0], 400)  # needs approval
        self.assertEqual(self.call("POST", f"/api/tasks/{task}/resume", {"approve_external": True, "verification": "syntax"})[0], 400)
        self.assertEqual(self.call("POST", f"/api/tasks/{task}/goal/stop", {})[0], 400)
        self.assertEqual(self.call("POST", f"/api/tasks/{task}/unknown", {})[0], 404)

    def test_bundled_fonts_are_served(self):
        status, headers, content = self.call("GET", "/fonts/inter-latin-wght-normal.woff2", auth=False)
        self.assertEqual((status, headers["Content-Type"]), (200, "font/woff2"))
        self.assertTrue(content.startswith(b"wOF2"))
        self.assertEqual(self.call("GET", "/fonts/LICENSE-Inter.txt", auth=False)[0], 401)

    def test_sign_in_link_sets_a_strict_http_only_session_cookie(self):
        status, _, _ = self.call("POST", "/api/session", {"token": "wrong"}, auth=False)
        self.assertEqual(status, 401)
        status, headers, _ = self.call("POST", "/api/session", {"token": "test-token"}, auth=False)
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        pair = cookie.split(";")[0]
        self.assertEqual(self.call("GET", "/api/project", headers={"Cookie": pair}, auth=False)[0], 200)
        self.assertEqual(self.call("GET", "/api/project", headers={"Cookie": pair.split("=")[0] + "=nope"}, auth=False)[0], 401)
        # The cookie never overrides the cross-origin and rebinding checks.
        self.assertEqual(self.call("GET", "/api/project", headers={"Cookie": pair, "Origin": "https://evil.example"}, auth=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/session", {"token": "test-token"}, headers={"Origin": "https://evil.example"}, auth=False)[0], 403)

    def test_cross_origin_and_rebinding_host_blocked(self):
        self.assertEqual(self.call("GET", "/api/project", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/project", headers={"Host": "evil.example"})[0], 403)

    def test_path_traversal_is_not_a_static_file_route(self):
        self.assertEqual(self.call("GET", "/../../pyproject.toml")[0], 404)

    def test_real_http_journey(self):
        project = json.loads(self.call("GET", "/api/project")[2])
        self.assertEqual(project["files"][0]["path"], "app.py")
        status, _, body = self.call("POST", "/api/tasks", {"objective": "Change x", "mode": "offline", "verification": "syntax", "edits": [{"path": "app.py", "before": "x = 1", "after": "x = 2"}]})
        self.assertEqual(status, 202)
        task = json.loads(body)["id"]
        for _ in range(100):
            data = json.loads(self.call("GET", "/api/tasks/" + task)[2])
            if data["state"] in TERMINAL:
                break
            time.sleep(.01)
        self.assertEqual(data["state"], "completed")
        self.assertIn("+x = 2", data["result"]["diff"])
        self.assertFalse(data["result"]["tests_verified"])
        listing = json.loads(self.call("GET", "/api/tasks")[2])
        self.assertEqual(len(listing), 1)
        self.assertEqual((listing[0]["objective"], listing[0]["mode"]), ("Change x", "offline"))
        cancelled = json.loads(self.call("POST", f"/api/tasks/{task}/cancel", {})[2])
        self.assertFalse(cancelled["requested"])

    def test_missing_task_and_execution_approval_errors(self):
        self.assertEqual(self.call("GET", "/api/tasks/missing")[0], 404)
        status, _, body = self.call("POST", "/api/tasks", {"objective": "x", "mode": "offline", "verification": "docker"})
        self.assertEqual(status, 400)
        self.assertIn("approval", json.loads(body)["error"])

    def test_agent_http_journey_uses_objective_without_user_edits(self):
        model = Mock()
        model.respond.side_effect = [
            ModelResponse("Investigate", [ToolCall("read", "read_file", {"path": "app.py"})]),
            ModelResponse("Edit", [ToolCall("edit", "edit_file", {"path": "app.py", "before": "x = 1", "after": "x = 2"})]),
            ModelResponse("Verify", [ToolCall("test", "run_tests", {})]),
            ModelResponse("Finish", [ToolCall("finish", "finish", {})]),
        ]

        def instrumented_verifier(candidate, *args, **kwargs):
            self.assertEqual((candidate / "app.py").read_text(), "x = 2\n")
            return {"kind": "unittest", "status": "passed", "note": "Instrumented contract verifier; not a real test execution"}

        with patch("dikdik.engine.CompatibleAgentModel", return_value=model), patch("dikdik.engine.run_tests", side_effect=instrumented_verifier):
            status, _, body = self.call("POST", "/api/tasks", {"objective": "Change x", "mode": "agent", "verification": "docker", "approve_execution": True, "approve_external": True})
            self.assertEqual(status, 202)
            task = json.loads(body)["id"]
            for _ in range(200):
                data = json.loads(self.call("GET", "/api/tasks/" + task)[2])
                if data["state"] in TERMINAL:
                    break
                time.sleep(.01)
        self.assertEqual(data["state"], "completed")
        self.assertEqual(data["result"]["agent"]["tool_calls"], 4)
        self.assertEqual((self.engine.root / "app.py").read_text(), "x = 1\n")
        self.assertNotIn("edits", data["request"])
        self.assertIn("+x = 2", data["result"]["diff"])

    def test_agent_api_denies_missing_disclosure_or_non_docker_verification(self):
        for request in [
            {"objective": "x", "mode": "agent", "verification": "docker", "approve_execution": True},
            {"objective": "x", "mode": "agent", "verification": "docker", "approve_external": True},
            {"objective": "x", "mode": "agent", "verification": "syntax"},
        ]:
            with self.subTest(request=request):
                self.assertEqual(self.call("POST", "/api/tasks", request)[0], 400)
