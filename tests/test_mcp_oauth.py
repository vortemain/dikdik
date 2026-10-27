"""MCP completeness: editing, environment keys, the server library, and OAuth sign-in
against a local OAuth-protected MCP server (no internet)."""
import base64
import hashlib
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from dikdik.engine import Engine
from dikdik.mcp import McpError, oauth_ref
from dikdik.mcp_catalog import CATALOG
from dikdik.server import make_server

SERVER = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
COMMAND = f'"{sys.executable}" "{SERVER}"'


class FakeAuth(BaseHTTPRequestHandler):
    """An MCP server behind OAuth plus its authorization server, all on one port."""
    codes: dict = {}
    tokens: dict = {}
    registered: list = []
    token_requests: list = []
    lifetime = 3600
    register_ok = True

    def log_message(self, *args):
        pass

    def send(self, status, body=b"", kind="application/json", headers=()):
        raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        self.send_response(status)
        self.send_header("Content-Type", kind)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/.well-known/oauth-protected-resource/mcp":
            return self.send(200, {"resource": self.base + "/mcp", "authorization_servers": [self.base], "scopes_supported": ["read"]})
        if path == "/.well-known/oauth-authorization-server":
            return self.send(200, {"issuer": self.base, "authorization_endpoint": self.base + "/authorize", "token_endpoint": self.base + "/token",
                                   "registration_endpoint": self.base + "/register", "code_challenge_methods_supported": ["S256"]})
        if path == "/authorize":
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            code = f"code-{len(FakeAuth.codes)}"
            FakeAuth.codes[code] = q
            return self.send(302, b"", headers=[("Location", q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]}))])
        self.send(404, {"error": "nope"})

    def do_POST(self):
        path = urlsplit(self.path).path
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if path == "/register":
            if not FakeAuth.register_ok:
                return self.send(400, {"error": "invalid_client_metadata"})
            FakeAuth.registered.append(json.loads(raw))
            return self.send(201, {"client_id": "client-1"})
        if path == "/token":
            form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            FakeAuth.token_requests.append(form)
            if form["grant_type"] == "authorization_code":
                asked = FakeAuth.codes.pop(form.get("code"), None)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if not asked or asked["code_challenge"] != challenge or form["redirect_uri"] != asked["redirect_uri"]:
                    return self.send(400, {"error": "invalid_grant"})
            elif form["grant_type"] != "refresh_token" or form.get("refresh_token") != "refresh-1":
                return self.send(400, {"error": "invalid_grant"})
            token = f"access-{len(FakeAuth.tokens)}"
            FakeAuth.tokens[token] = True
            return self.send(200, {"access_token": token, "token_type": "Bearer", "expires_in": FakeAuth.lifetime, "refresh_token": "refresh-1"})
        if path == "/mcp":
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] not in FakeAuth.tokens:
                return self.send(401, {"error": "unauthorized"}, headers=[("WWW-Authenticate", f'Bearer resource_metadata="{self.base}/.well-known/oauth-protected-resource/mcp"')])
            message = json.loads(raw)
            if "id" not in message:
                return self.send(202)
            result = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "secured"}} if message["method"] == "initialize" \
                else {"tools": [{"name": "whoami", "inputSchema": {"type": "object"}}]} if message["method"] == "tools/list" \
                else {"content": [{"type": "text", "text": auth[7:]}]}
            return self.send(200, {"jsonrpc": "2.0", "id": message["id"], "result": result})
        self.send(404, {"error": "nope"})


class McpCompletenessTests(unittest.TestCase):
    def setUp(self):
        FakeAuth.codes, FakeAuth.tokens, FakeAuth.registered, FakeAuth.token_requests = {}, {}, [], []
        FakeAuth.lifetime, FakeAuth.register_ok = 3600, True
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        (base / "repo").mkdir()
        self.engine = Engine(base / "repo", base / "data")
        self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "test-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.auth = ThreadingHTTPServer(("127.0.0.1", 0), FakeAuth)
        threading.Thread(target=self.auth.serve_forever, daemon=True).start()
        self.addCleanup(self.auth.server_close)
        self.addCleanup(self.auth.shutdown)
        self.mcp_url = f"http://127.0.0.1:{self.auth.server_port}/mcp"

    def call(self, method, path, body=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=20)
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None,
                         headers={"Authorization": "Bearer test-token"} if auth else {})
            response = conn.getresponse()
            raw = response.read()
            kind = response.getheader("Content-Type", "")
            return response.status, (json.loads(raw) if "json" in kind else raw.decode())
        finally:
            conn.close()

    def follow_authorize(self, url):
        """Play the browser: open the authorization page and return where it redirects."""
        parts = urlsplit(url)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
        conn.request("GET", parts.path + "?" + parts.query)
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 302)
        return response.getheader("Location")

    def add_oauth_server(self):
        status, made = self.call("POST", "/api/mcp", {"name": "secured", "transport": "http", "url": self.mcp_url, "auth": "oauth"})
        self.assertEqual(status, 201)
        return made["id"]

    # --- editing and env keys ---------------------------------------------------------
    def test_edit_is_sparse_and_validated(self):
        status, made = self.call("POST", "/api/mcp", {"name": "echo", "transport": "stdio", "command": COMMAND, "enabled": True})
        server_id = made["id"]
        self.assertTrue(self.call("GET", "/api/mcp")[1][0]["enabled"])
        status, edited = self.call("PATCH", f"/api/mcp/{server_id}", {"name": "echo2", "env": {"ECHO_KEY": "k1"}})
        self.assertEqual((status, edited["name"], edited["command"], edited["env"], edited["enabled"]), (200, "echo2", COMMAND, {"ECHO_KEY": "k1"}, True))
        for bad in ({"colour": "red"}, {"env": {"lower": "k1"}}, {"env": {"ECHO_KEY": "conn.x"}}, {"auth": "oauth"}, {"command": ""}, {}):
            self.assertEqual(self.call("PATCH", f"/api/mcp/{server_id}", bad)[0], 400, bad)
        self.call("POST", "/api/mcp", {"name": "other", "transport": "http", "url": "https://mcp.example.com/mcp"})
        self.assertEqual(self.call("PATCH", f"/api/mcp/{server_id}", {"name": "other"})[0], 400)
        # Switching type drops the command and its env unless sent again.
        status, switched = self.call("PATCH", f"/api/mcp/{server_id}", {"transport": "http", "url": "https://mcp.example.com/x"})
        self.assertEqual((status, switched["command"], switched["env"]), (200, "", {}))
        self.assertEqual(self.call("PATCH", "/api/mcp/" + "0" * 32, {"name": "x"})[0], 404)

    def test_env_keys_reach_the_command_server_only(self):
        self.engine.models.secrets.set("echo-key", b"sealed-value")
        status, made = self.call("POST", "/api/mcp", {"name": "echo", "transport": "stdio", "command": COMMAND, "env": {"ECHO_KEY": "echo-key"}})
        status, result = self.call("POST", f"/api/mcp/{made['id']}/test", {"tool_call": {"name": "whoami", "arguments": {}}})
        self.assertEqual(status, 200)
        seen = json.loads(result["tool_result"]["content"])
        self.assertEqual((seen["key"], seen["dikdik_vars"]), ("sealed-value", []))
        status, result = self.call("POST", f"/api/mcp/{made['id']}/test", {"tool_call": {"name": "nope", "arguments": {}}})
        self.assertTrue(result["tool_result"]["is_error"])
        # A missing key is a plain message, not a 404.
        self.call("PATCH", f"/api/mcp/{made['id']}", {"env": {"ECHO_KEY": "gone"}})
        status, result = self.call("POST", f"/api/mcp/{made['id']}/test", {})
        self.assertEqual(status, 400)
        self.assertIn("“gone” is not stored", result["error"])

    def test_renaming_a_key_keeps_env_references(self):
        self.engine.models.secrets.set("old-key", b"v")
        status, made = self.call("POST", "/api/mcp", {"name": "echo", "transport": "stdio", "command": COMMAND, "env": {"ECHO_KEY": "old-key"}})
        self.assertEqual(self.call("POST", "/api/secrets/old-key/rename", {"to": "new-key"})[0], 200)
        self.assertEqual(self.engine.mcp.get(made["id"])["env"], {"ECHO_KEY": "new-key"})

    # --- the library ---------------------------------------------------------------------
    def test_library_lists_entries_and_marks_installed(self):
        status, entries = self.call("GET", "/api/mcp/catalog")
        self.assertEqual(status, 200)
        ids = [e["id"] for e in entries]
        self.assertIn("filesystem", ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(o["transport"] in {"http", "stdio"} for e in entries for o in e["options"]))
        self.assertEqual(len(next(e for e in entries if e["id"] == "sentry")["options"]), 2)
        self.assertFalse(any(e["installed"] for e in entries))

    def test_library_install_checks_first_and_seals_keys_after(self):
        entry = {"id": "echo-test", "name": "Echo", "description": "", "options": [
            {"id": "local", "label": "Local", "transport": "stdio", "command": COMMAND,
             "args": [{"name": "paths", "label": "Paths (space separated)", "required": True}],
             "credentials": [{"name": "token", "label": "Echo key", "env_var": "ECHO_KEY"}]}]}
        with patch("dikdik.mcp_catalog.CATALOG", CATALOG + [entry]):
            base = {"entry": "echo-test", "args": {"paths": "a b"}, "credentials": {"token": {"value": "s3cret", "save_as": "echo-key"}}}
            self.assertIn("Approve running", self.call("POST", "/api/mcp/catalog/install", base)[1]["error"])
            self.assertIn("required", self.call("POST", "/api/mcp/catalog/install", {**base, "approve_command": True, "args": {}})[1]["error"])
            self.assertIn("quotes", self.call("POST", "/api/mcp/catalog/install", {**base, "approve_command": True, "args": {"paths": 'a"b'}})[1]["error"])
            status, done = self.call("POST", "/api/mcp/catalog/install", {**base, "approve_command": True, "enable": True})
            self.assertEqual((status, done["tools"]), (201, 3))
            server = self.engine.mcp.get(done["id"])
            self.assertEqual((server["command"], server["env"], server["catalog_id"], server["enabled"]), (COMMAND + " a b", {"ECHO_KEY": "echo-key"}, "echo-test", True))
            self.assertEqual(self.engine.models.secrets.reveal("echo-key"), b"s3cret")
            self.assertTrue(next(e for e in self.call("GET", "/api/mcp/catalog")[1] if e["id"] == "echo-test")["installed"])
            # Same name again, or a key name that exists, is refused.
            self.assertIn("already exists", self.call("POST", "/api/mcp/catalog/install", {**base, "approve_command": True})[1]["error"])
            self.assertIn("already exists", self.call("POST", "/api/mcp/catalog/install", {**base, "approve_command": True, "name": "echo2"})[1]["error"])
            # Reusing a stored key works.
            status, again = self.call("POST", "/api/mcp/catalog/install", {**base, "approve_command": True, "name": "echo2", "credentials": {"token": {"ref": "echo-key"}}})
            self.assertEqual(status, 201)

    def test_failed_library_install_saves_nothing(self):
        entry = {"id": "broken", "name": "Broken", "description": "", "options": [
            {"id": "local", "label": "Local", "transport": "stdio", "command": "definitely-not-a-real-command-xyz",
             "credentials": [{"name": "token", "label": "Key", "env_var": "KEY"}]}]}
        with patch("dikdik.mcp_catalog.CATALOG", CATALOG + [entry]):
            status, reply = self.call("POST", "/api/mcp/catalog/install", {"entry": "broken", "approve_command": True,
                                                                           "credentials": {"token": {"value": "x", "save_as": "broken-key"}}})
        self.assertEqual(status, 400)
        self.assertEqual(self.engine.mcp.list(), [])
        self.assertNotIn("broken-key", [s["ref"] for s in self.engine.models.secrets.list()])
        self.assertEqual(self.call("POST", "/api/mcp/catalog/install", {"entry": "no-such"})[0], 404)
        self.assertEqual(self.call("POST", "/api/mcp/catalog/install", {"entry": "sentry", "option": "nope"})[0], 400)

    def test_oauth_library_entry_installs_without_a_check(self):
        status, done = self.call("POST", "/api/mcp/catalog/install", {"entry": "linear"})
        self.assertEqual((status, done["oauth"]), (201, True))
        server = self.engine.mcp.get(done["id"])
        self.assertEqual((server["auth"], server["url"], server["enabled"]), ("oauth", "https://mcp.linear.app/mcp", False))

    # --- OAuth ---------------------------------------------------------------------------
    def test_oauth_sign_in_end_to_end(self):
        server_id = self.add_oauth_server()
        self.assertFalse(self.call("GET", "/api/mcp")[1][0]["signed_in"])
        status, result = self.call("POST", f"/api/mcp/{server_id}/test", {})
        self.assertEqual((status, result["error"]), (400, "Sign in to secured first"))
        status, job = self.call("POST", f"/api/mcp/{server_id}/oauth/start", {})
        self.assertEqual((status, job["status"]), (200, "authorizing"))
        query = {k: v[0] for k, v in parse_qs(urlsplit(job["authorization_url"]).query).items()}
        redirect = f"http://127.0.0.1:{self.server.server_port}/api/mcp/oauth/callback"
        self.assertEqual((query["code_challenge_method"], query["redirect_uri"], query["resource"], query["scope"], query["client_id"]),
                         ("S256", redirect, self.mcp_url, "read", "client-1"))
        self.assertEqual(FakeAuth.registered[0]["redirect_uris"], [redirect])
        self.assertEqual(self.call("GET", f"/api/mcp/oauth/{job['id']}")[1]["status"], "authorizing")
        # The browser lands on the callback without the session (SameSite=Strict).
        location = self.follow_authorize(job["authorization_url"])
        status, page = self.call("GET", urlsplit(location).path + "?" + urlsplit(location).query, auth=False)
        self.assertEqual(status, 200)
        self.assertIn("Signed in.", page)
        self.assertEqual(self.call("GET", f"/api/mcp/oauth/{job['id']}")[1], {"id": job["id"], "server_id": server_id, "status": "completed", "ok": True, "error": "", "error_kind": ""})
        listed = self.call("GET", "/api/mcp")[1][0]
        self.assertTrue(listed["signed_in"])
        self.assertNotIn(oauth_ref(server_id), [s["ref"] for s in self.call("GET", "/api/secrets")[1]])  # hidden from API keys
        self.assertNotIn("access-0", json.dumps(listed))
        status, tested = self.call("POST", f"/api/mcp/{server_id}/test", {"tool_call": {"name": "whoami", "arguments": {}}})
        self.assertEqual((status, tested["server"], tested["tool_result"]["content"]), (200, "secured", "access-0"))
        # Replaying the callback is a client error; the job stays completed.
        status, page = self.call("GET", urlsplit(location).path + "?" + urlsplit(location).query, auth=False)
        self.assertEqual(status, 400)
        status, replay = self.call("POST", f"/api/mcp/oauth/{job['id']}/callback", {"callback_url": location})
        self.assertEqual((status, replay["status"]), (409, "completed"))
        # Signing out forgets the token.
        self.assertEqual(self.call("DELETE", f"/api/mcp/{server_id}/oauth")[0], 200)
        self.assertFalse(self.call("GET", "/api/mcp")[1][0]["signed_in"])

    def test_expired_tokens_are_refreshed_and_resealed(self):
        server_id = self.add_oauth_server()
        FakeAuth.lifetime = 30  # inside the refresh margin, so the next use refreshes
        job = self.call("POST", f"/api/mcp/{server_id}/oauth/start", {})[1]
        location = self.follow_authorize(job["authorization_url"])
        self.assertEqual(self.call("POST", f"/api/mcp/oauth/{job['id']}/callback", {"callback_url": location})[1]["status"], "completed")
        FakeAuth.lifetime = 3600
        status, tested = self.call("POST", f"/api/mcp/{server_id}/test", {"tool_call": {"name": "whoami", "arguments": {}}})
        self.assertEqual((status, tested["tool_result"]["content"]), (200, "access-1"))
        self.assertEqual(FakeAuth.token_requests[-1]["grant_type"], "refresh_token")
        stored = json.loads(self.engine.models.secrets.reveal(oauth_ref(server_id)))
        self.assertEqual(stored["access_token"], "access-1")
        # The agent's session uses the same token.
        from dikdik.mcp import McpSession
        session = McpSession([self.engine.mcp.get(server_id)], self.engine.models.secrets.reveal, self.engine.models.secrets.set)
        self.addCleanup(session.close)
        self.assertEqual((session.errors, session.call("mcp__secured__whoami", {})["content"]), ([], "access-1"))

    def test_callback_validation_and_denial(self):
        server_id = self.add_oauth_server()
        job = self.call("POST", f"/api/mcp/{server_id}/oauth/start", {})[1]
        state = parse_qs(urlsplit(job["authorization_url"]).query)["state"][0]
        port = self.server.server_port
        good = f"http://127.0.0.1:{port}/api/mcp/oauth/callback"
        for bad in [f"https://127.0.0.1:{port}/api/mcp/oauth/callback?state={state}&code=x",
                    f"http://evil.example:{port}/api/mcp/oauth/callback?state={state}&code=x",
                    f"http://127.0.0.1:{port + 1}/api/mcp/oauth/callback?state={state}&code=x",
                    f"http://127.0.0.1:{port}/elsewhere?state={state}&code=x",
                    f"http://127.0.0.1:99999/api/mcp/oauth/callback?state={state}&code=x",
                    f"{good}?state=wrong&code=x"]:
            self.assertEqual(self.call("POST", f"/api/mcp/oauth/{job['id']}/callback", {"callback_url": bad})[0], 400, bad)
        self.assertEqual(self.call("GET", f"/api/mcp/oauth/{job['id']}")[1]["status"], "authorizing")
        self.assertEqual(self.call("POST", f"/api/mcp/oauth/{job['id']}/callback", {})[0], 422)
        self.assertEqual(self.call("POST", "/api/mcp/oauth/" + "0" * 32 + "/callback", {"callback_url": good})[0], 404)
        self.assertEqual(self.call("GET", "/api/mcp/oauth/" + "0" * 32)[0], 404)
        status, denied = self.call("POST", f"/api/mcp/oauth/{job['id']}/callback", {"callback_url": f"{good}?error=access_denied&state={state}"})
        self.assertEqual((status, denied["ok"], denied["error_kind"]), (200, False, "denied"))
        self.assertFalse(self.call("GET", "/api/mcp")[1][0]["signed_in"])

    def test_oauth_start_errors_and_timeout(self):
        status, stdio = self.call("POST", "/api/mcp", {"name": "echo", "transport": "stdio", "command": COMMAND})
        self.assertEqual(self.call("POST", f"/api/mcp/{stdio['id']}/oauth/start", {})[0], 400)
        status, plain = self.call("POST", "/api/mcp", {"name": "plain", "transport": "http", "url": self.mcp_url})
        self.assertEqual(self.call("POST", f"/api/mcp/{plain['id']}/oauth/start", {})[0], 400)
        status, dead = self.call("POST", "/api/mcp", {"name": "dead", "transport": "http", "url": "http://127.0.0.1:9/mcp", "auth": "oauth"})
        status, job = self.call("POST", f"/api/mcp/{dead['id']}/oauth/start", {})
        self.assertEqual((status, job["ok"], job["status"], job["error_kind"]), (200, False, "failed", "unreachable"))
        FakeAuth.register_ok = False
        server_id = self.add_oauth_server()
        job = self.call("POST", f"/api/mcp/{server_id}/oauth/start", {})[1]
        self.assertIn("refused to register", job["error"])
        FakeAuth.register_ok = True
        self.assertEqual(self.call("POST", f"/api/mcp/{server_id}/oauth/start", {"timeout": 0})[0], 400)
        job = self.call("POST", f"/api/mcp/{server_id}/oauth/start", {"timeout": 0.2})[1]
        time.sleep(0.4)
        self.assertEqual(self.call("GET", f"/api/mcp/oauth/{job['id']}")[1]["error_kind"], "timeout")

    def test_changing_the_url_or_sign_in_forgets_the_token(self):
        server_id = self.add_oauth_server()
        self.engine.models.secrets.set(oauth_ref(server_id), json.dumps({"access_token": "a"}).encode())
        self.call("PATCH", f"/api/mcp/{server_id}", {"url": self.mcp_url + "2"})
        self.assertFalse(self.call("GET", "/api/mcp")[1][0]["signed_in"])
        self.engine.models.secrets.set(oauth_ref(server_id), json.dumps({"access_token": "a"}).encode())
        self.call("DELETE", f"/api/mcp/{server_id}")
        self.assertNotIn(oauth_ref(server_id), [s["ref"] for s in self.engine.models.secrets.list()])

    def test_routes_need_the_session(self):
        for method, path in [("GET", "/api/mcp/catalog"), ("POST", "/api/mcp/catalog/install"), ("GET", "/api/mcp/oauth/x")]:
            self.assertEqual(self.call(method, path, {} if method == "POST" else None, auth=False)[0], 401)
        status, page = self.call("GET", "/api/mcp/oauth/callback?state=forged&code=x", auth=False)
        self.assertEqual(status, 400)
        self.assertIn("not valid", page)

    def test_discovery_refuses_servers_that_do_not_ask(self):
        from dikdik.mcp_oauth import discover
        with self.assertRaises(McpError):
            discover(f"http://127.0.0.1:{self.auth.server_port}/nothing")  # 404, not 401


if __name__ == "__main__":
    unittest.main()
