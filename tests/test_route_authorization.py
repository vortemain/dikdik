"""Every API route refuses anonymous and foreign-Host requests, except an explicit public list.

The paths are harvested from the server source, so a route added later is covered without
editing this file. This is the test that would have caught the pre-auth pairing route.
"""

import http.client
import re
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server import make_server

SOURCE = Path(__file__).resolve().parent.parent / "dikdik"
# Reachable without the session secret. Each still enforces its own checks.
PUBLIC = {
    ("POST", "/api/session"),          # exchanges the sign-in secret for a cookie
    ("POST", "/api/pair/verify"),      # redeems an owner-minted pairing code
    ("GET", "/api/mcp/oauth/callback"),  # provider redirect; state-checked
    ("GET", "/api/server-info"), ("GET", "/api/openapi"),  # documented public metadata
}
METHODS = ("GET", "POST", "PATCH", "DELETE")


def api_paths() -> list[str]:
    found = set()
    for module in SOURCE.glob("*.py"):
        for literal in re.findall(r"""["'](/api/[A-Za-z0-9_./-]*)["']""", module.read_text(encoding="utf-8")):
            literal = literal.rstrip("/")
            if literal:
                found.add(literal)
                found.add(literal + "/x")  # prefix routes: /api/tasks/<id>
    return sorted(found)


class RouteAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name)
        (base / "repo").mkdir()
        cls.engine = Engine(base / "repo", base / "data")
        cls.server = make_server(cls.engine, 0, "route-token")
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.paths = api_paths()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.engine.close()
        cls.temp.cleanup()

    def request(self, method, path, headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            body = b"{}" if method in {"POST", "PATCH"} else None
            conn.request(method, path, body=body, headers={"Content-Type": "application/json", **headers})
            response = conn.getresponse()
            response.read()
            return response.status
        finally:
            conn.close()

    def test_harvest_finds_the_api(self):
        self.assertGreater(len(self.paths), 100)
        self.assertIn("/api/pair", self.paths)
        self.assertIn("/api/tasks", self.paths)

    def test_anonymous_requests_are_refused(self):
        leaks = []
        for path in self.paths:
            for method in METHODS:
                if (method, path) in PUBLIC:
                    continue
                status = self.request(method, path, {})
                if status not in (401, 403):
                    leaks.append(f"{method} {path} -> {status}")
        self.assertEqual(leaks, [], "Routes answer without the session secret")

    def test_foreign_host_is_refused_even_with_the_secret(self):
        # DNS rebinding: a page on another name reaches 127.0.0.1 with its own Host header.
        port = self.server.server_port
        rebinding = {"Host": f"attacker.example:{port}", "Origin": f"http://attacker.example:{port}",
                     "Authorization": "Bearer route-token"}
        leaks = []
        for path in self.paths:
            for method in METHODS:
                status = self.request(method, path, rebinding)
                if (method, path) in {("GET", "/api/server-info"), ("GET", "/api/openapi")}:
                    continue
                if status != 403:
                    leaks.append(f"{method} {path} -> {status}")
        self.assertEqual(leaks, [], "Routes answer a foreign Host")


if __name__ == "__main__":
    unittest.main()
