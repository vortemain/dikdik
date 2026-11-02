"""DAST runs against real servers: a deliberately vulnerable one and a hardened twin."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik import dast

APP = r'''
import html, sqlite3, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
SAFE = sys.argv[2] == "safe"
db = sqlite3.connect(":memory:", check_same_thread=False)
db.execute("CREATE TABLE items(id INTEGER, name TEXT)"); db.execute("INSERT INTO items VALUES(1, 'pen')")
class Handler(BaseHTTPRequestHandler):
    if SAFE:
        server_version, sys_version = "app", ""
    def send(self, status, body, kind="text/html; charset=utf-8", extra=()):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind); self.send_header("Content-Length", str(len(data)))
        if SAFE:
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        parts = urlsplit(self.path); query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        origin = self.headers.get("Origin")
        cors = () if SAFE or not origin else (("Access-Control-Allow-Origin", origin), ("Access-Control-Allow-Credentials", "true"))
        if parts.path == "/":
            cookie = "sid=abc; HttpOnly; SameSite=Lax; Path=/" if SAFE else "sid=abc; Path=/"
            self.send(200, '<a href="/search?q=hi">s</a> <a href="/item?id=1">i</a> <a href="/go?next=/">g</a>'
                           '<a href="https://elsewhere.example/">x</a><form action="/search"><input name="q"></form>'
                           '<form method="post" action="/buy"><input name="qty"></form>', extra=(("Set-Cookie", cookie),) + cors)
        elif parts.path == "/search":
            q = query.get("q", "")
            self.send(200, "<p>Results for " + (html.escape(q) if SAFE else q) + "</p>")
        elif parts.path == "/item":
            try:
                if SAFE:
                    row = db.execute("SELECT name FROM items WHERE id=?", (query.get("id", ""),)).fetchone()
                else:
                    row = db.execute("SELECT name FROM items WHERE id='" + query.get("id", "") + "'").fetchone()
                self.send(200, "<p>" + html.escape(str(row)) + "</p>")
            except Exception as exc:
                self.send(200 if SAFE else 500, "<p>Not found</p>" if SAFE else "<pre>sqlite3.OperationalError: " + html.escape(str(exc)) + "</pre>")
        elif parts.path == "/go":
            target = query.get("next", "/")
            if SAFE and not (target.startswith("/") and not target.startswith("//")):
                target = "/"
            self.send(302, "", extra=(("Location", target),))
        elif parts.path == "/.env" and not SAFE:
            self.send(200, "DATABASE_URL=postgres://u:p@db/x\n", kind="text/plain")
        else:
            self.send(404, "<p>Not found</p>")
    def log_message(self, *args):
        pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''


class ValidationTests(unittest.TestCase):
    def test_arguments_stay_on_the_project_server(self):
        for bad in ({"command": "serve"}, {"command": "s {port}", "paths": ["http://evil.example/"]},
                    {"command": "s {port}", "paths": ["//evil.example/"]}, {"command": "s {port}", "paths": []},
                    {"command": "s {port}", "max_pages": 0}, {"command": "s {port}", "max_pages": 61}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dast.validate(bad)

    def test_links_to_other_origins_are_not_followed(self):
        self.assertEqual(dast._target("/a", "b?x=1", 8000), "/b?x=1")
        self.assertEqual(dast._target("/", "http://127.0.0.1:8000/c", 8000), "/c")
        for link in ("https://elsewhere.example/", "http://127.0.0.1:9000/", "javascript:alert(1)", "mailto:a@b.c", "//evil.example/"):
            self.assertIsNone(dast._target("/", link, 8000), link)


class RealServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "app.py").write_text(APP, encoding="utf-8")
        python = Path(sys.executable).as_posix()
        cls.vulnerable = dast.run(cls.root, {"command": f'"{python}" app.py {{port}} vulnerable'}, threading.Event())
        cls.hardened = dast.run(cls.root, {"command": f'"{python}" app.py {{port}} safe'}, threading.Event())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def rules(self, result):
        return {f["rule"] for f in result["findings"]}

    def test_the_vulnerable_server_is_caught(self):
        result = self.vulnerable
        self.assertEqual(result["status"], "failed", result)
        found = self.rules(result)
        for rule in ("DAST001", "DAST002", "DAST003", "DAST004", "DAST005", "DAST006", "DAST007", "DAST008", "DAST010", "DAST012", "DAST013", "DAST014"):
            self.assertIn(rule, found, rule)
        xss = next(f for f in result["findings"] if f["rule"] == "DAST007")
        self.assertEqual((xss["severity"], xss["path"]), ("high", "/search?q="))
        self.assertEqual(next(f for f in result["findings"] if f["rule"] == "DAST013")["path"], "/.env")
        self.assertIn("/item?id=1", result["pages"])
        self.assertFalse(any("elsewhere" in page for page in result["pages"]))

    def test_the_hardened_twin_passes(self):
        result = self.hardened
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(self.rules(result), {"DAST012"})  # the POST form is reported, never submitted

    def test_a_server_that_does_not_start_is_a_failure_not_a_pass(self):
        result = dast.run(self.root, {"command": f'"{Path(sys.executable).as_posix()}" -c "raise SystemExit(3)" {{port}}'}, threading.Event())
        self.assertEqual(result["status"], "failed")
        self.assertIn("exited", result["error"])


if __name__ == "__main__":
    unittest.main()
