"""Dikdik on a server: explicit flags, exact public address, Secure cookies, limits."""

import http.client
import json
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.remote import RemoteConfig, check
from dikdik.server import make_server


class FlagTests(unittest.TestCase):
    def test_local_use_needs_no_flags(self):
        self.assertIsNone(check("127.0.0.1", None, None, None, False, None))

    def test_listening_beyond_this_computer_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            cases = [
                (("0.0.0.0", None, None, None, False, None), "--public-url"),
                (("0.0.0.0", "http://dikdik.example.com", None, None, True, None), "https://"),
                (("0.0.0.0", "https://dikdik.example.com/path", None, None, True, None), "plain origin"),
                (("0.0.0.0", "https://dikdik.example.com", None, None, False, None), "Serve HTTPS"),
                (("0.0.0.0", "https://dikdik.example.com", "cert.pem", None, False, None), "both"),
                (("0.0.0.0", "https://dikdik.example.com", None, None, True, str(Path(folder) / "missing")), "not a folder"),
                (("127.0.0.1", None, None, None, False, folder), "--projects-root"),
            ]
            for args, words in cases:
                with self.subTest(args=args), self.assertRaisesRegex(ValueError, words):
                    check(*args)
            config = check("0.0.0.0", "https://dikdik.example.com/", None, None, True, folder)
            self.assertEqual((config.public_host, config.public_origin, config.secure), ("dikdik.example.com", "https://dikdik.example.com", True))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.projects = base / "projects"
        (self.projects / "one").mkdir(parents=True)
        (self.projects / "one" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (base / "outside").mkdir()
        self.outside = base / "outside"
        self.remote = RemoteConfig(listen="127.0.0.1", public_url="https://dikdik.example.com", projects_root=self.projects, behind_proxy=True)
        self.server = make_server(Engine(self.projects / "one", base / "data"), 0, "tok", self.remote)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: self.server.projects.engine.close())
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, host="dikdik.example.com", origin=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=20)
        headers = {"Host": host, "Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer tok"
        if origin:
            headers["Origin"] = origin
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, response.getheader("Set-Cookie"), raw

    def test_only_the_public_address_and_loopback_are_served(self):
        self.assertEqual(self.call("GET", "/api/tasks")[0], 200)
        self.assertEqual(self.call("GET", "/api/tasks", origin="https://dikdik.example.com")[0], 200)
        self.assertEqual(self.call("GET", "/api/tasks", host=f"127.0.0.1:{self.server.server_port}")[0], 200)
        self.assertEqual(self.call("GET", "/api/tasks", host="evil.example.com")[0], 403)
        self.assertEqual(self.call("GET", "/api/tasks", origin="https://evil.example.com")[0], 403)
        self.assertEqual(self.call("GET", "/api/tasks", origin="http://dikdik.example.com")[0], 403)

    def test_sign_in_cookie_is_secure_and_failures_are_capped(self):
        status, cookie, _ = self.call("POST", "/api/session", {"token": "tok"}, auth=False)
        self.assertEqual(status, 200)
        self.assertIn("; Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        statuses = [self.call("POST", "/api/session", {"token": "wrong"}, auth=False)[0] for _ in range(11)]
        self.assertEqual(statuses[:10], [401] * 10)
        self.assertEqual(statuses[10], 429)
        self.assertEqual(self.call("POST", "/api/session", {"token": "tok"}, auth=False)[0], 429)  # even the right one, for a while

    def test_desktop_only_features_are_off_and_projects_stay_inside_the_root(self):
        self.assertEqual(self.call("GET", "/api/projects/folders")[0], 400)
        self.assertEqual(self.call("POST", "/api/projects/pick", {})[0], 400)
        self.assertEqual(self.call("POST", "/api/files/open-in-editor", {"path": "a.py"})[0], 400)
        self.assertEqual(self.call("POST", "/api/projects/open", {"path": str(self.outside)})[0], 400)
        self.assertEqual(self.call("POST", "/api/projects/new", {"name": "two", "parent": str(self.outside)})[0], 400)
        status, _, body = self.call("POST", "/api/projects/new", {"name": "two"})
        self.assertEqual(status, 201, body)
        self.assertTrue((self.projects / "two").is_dir())


@unittest.skipUnless(shutil.which("openssl"), "openssl is needed to make a test certificate")
class TlsTests(unittest.TestCase):
    def test_built_in_https(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            cert, key = base / "cert.pem", base / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                            "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
            (base / "repo").mkdir()
            (base / "repo" / "a.py").write_text("x = 1\n", encoding="utf-8")
            remote = RemoteConfig(listen="127.0.0.1", public_url="https://localhost", tls_cert=cert, tls_key=key)
            server = make_server(Engine(base / "repo", base / "data"), 0, "tok", remote)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                context = ssl._create_unverified_context()
                conn = http.client.HTTPSConnection("127.0.0.1", server.server_port, timeout=20, context=context)
                conn.request("GET", "/api/tasks", headers={"Host": "localhost", "Authorization": "Bearer tok"})
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
                plain = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                with self.assertRaises((OSError, http.client.HTTPException)):
                    plain.request("GET", "/health")
                    plain.getresponse()
            finally:
                server.shutdown()
                server.server_close()
                server.projects.engine.close()


if __name__ == "__main__":
    unittest.main()
