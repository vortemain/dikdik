"""Multiple API keys with scopes for programmatic access."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server import make_server


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "test-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, token="test-token"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def test_create_list_delete_key(self):
        status, body = self.call("POST", "/api/keys", {"name": "CI", "scopes": ["read"]})
        data = json.loads(body)
        self.assertEqual(status, 201)
        self.assertTrue(data["token"].startswith("dikdik-"))
        self.assertEqual(data["scopes"], ["read"])

        # List
        status, body = self.call("GET", "/api/keys")
        keys = json.loads(body)["keys"]
        self.assertEqual(status, 200)
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["name"], "CI")
        self.assertNotIn("token", keys[0])  # never return the token
        self.assertNotIn("hash", keys[0])

        # Delete
        key_id = keys[0]["id"]
        status, body = self.call("DELETE", f"/api/keys/{key_id}")
        self.assertEqual(status, 200)
        status, body = self.call("GET", "/api/keys")
        self.assertEqual(len(json.loads(body)["keys"]), 0)

    def test_api_key_authenticates(self):
        status, body = self.call("POST", "/api/keys", {"name": "CI", "scopes": ["read"]})
        token = json.loads(body)["token"]

        # Use the API key to access a protected route
        status, body = self.call("GET", "/api/tasks", token=token)
        self.assertEqual(status, 200)

    def test_wrong_api_key_refused(self):
        status, body = self.call("GET", "/api/tasks", token="dikdik-wrong")
        self.assertEqual(status, 401)

    def test_invalid_scope_refused(self):
        status, body = self.call("POST", "/api/keys", {"name": "bad", "scopes": ["superadmin"]})
        self.assertEqual(status, 400)

    def test_key_name_required(self):
        status, body = self.call("POST", "/api/keys", {"scopes": ["read"]})
        self.assertEqual(status, 400)

    def test_delete_unknown_key_404(self):
        status, body = self.call("DELETE", "/api/keys/nonexistent")
        self.assertEqual(status, 404)

    def test_rotate_key(self):
        status, body = self.call("POST", "/api/keys", {"name": "CI", "scopes": ["read"]})
        old_token = json.loads(body)["token"]
        key_id = json.loads(self.call("GET", "/api/keys")[1])["keys"][0]["id"]

        # Rotate
        status, body = self.call("POST", f"/api/keys/{key_id}/rotate")
        data = json.loads(body)
        self.assertEqual(status, 200)
        new_token = data["token"]
        self.assertNotEqual(new_token, old_token)

        # Old token no longer works
        status, body = self.call("GET", "/api/tasks", token=old_token)
        self.assertEqual(status, 401)

        # New token works
        status, body = self.call("GET", "/api/tasks", token=new_token)
        self.assertEqual(status, 200)

    def key(self, *scopes):
        return json.loads(self.call("POST", "/api/keys", {"name": "k", "scopes": list(scopes)})[1])["token"]

    def test_read_key_may_only_read(self):
        token = self.key("read")
        self.assertEqual(self.call("GET", "/api/tasks", token=token)[0], 200)
        self.assertEqual(self.call("PATCH", "/api/settings/app", {"sounds": True}, token=token)[0], 403)
        self.assertEqual(self.call("POST", "/api/files/upload", {"path": "new.txt", "content": "x"}, token=token)[0], 403)
        self.assertFalse((self.root / "new.txt").exists())

    def test_only_admin_keys_manage_keys(self):
        for scopes in (["read"], ["write"], ["read", "write"]):
            token = self.key(*scopes)
            self.assertEqual(self.call("GET", "/api/keys", token=token)[0], 403, scopes)
            self.assertEqual(self.call("POST", "/api/keys", {"name": "x", "scopes": ["admin"]}, token=token)[0], 403, scopes)
        admin = self.key("admin")
        self.assertEqual(self.call("POST", "/api/keys", {"name": "x", "scopes": ["read"]}, token=admin)[0], 201)

    def test_write_key_may_change_things(self):
        token = self.key("write")
        self.assertEqual(self.call("PATCH", "/api/settings/app", {"sounds": True}, token=token)[0], 200)

    def test_rotate_unknown_key_404(self):
        status, body = self.call("POST", "/api/keys/nonexistent/rotate")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()