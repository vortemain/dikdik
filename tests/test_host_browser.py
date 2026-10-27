"""The computer's own browser: public-only proxy, page text, and when it is used."""

import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik import host_browser
from dikdik.engine import Engine
from dikdik.host_browser import PublicProxy, page_text


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.proxy = PublicProxy()
        self.addCleanup(self.proxy.close)

    def ask(self, raw: bytes) -> str:
        with socket.create_connection(("127.0.0.1", self.proxy.server_address[1]), timeout=10) as sock:
            sock.sendall(raw)
            return sock.recv(200).split(b"\r\n")[0].decode()

    def test_private_and_unusual_targets_are_refused(self):
        for raw in (b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n", b"CONNECT localhost:443 HTTP/1.1\r\n\r\n",
                    b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n", b"CONNECT 10.0.0.5:443 HTTP/1.1\r\n\r\n",
                    b"GET http://192.168.1.1/ HTTP/1.1\r\nHost: 192.168.1.1\r\n\r\n", b"GET http://169.254.169.254/latest HTTP/1.1\r\n\r\n"):
            with self.subTest(raw=raw):
                self.assertIn("403", self.ask(raw))
        self.assertIn("403 Only HTTPS", self.ask(b"CONNECT example.com:22 HTTP/1.1\r\n\r\n"))
        self.assertIn("403 Only HTTP on port 80", self.ask(b"GET http://example.com:8080/ HTTP/1.1\r\n\r\n"))
        self.assertIn("405", self.ask(b"POST http://example.com/ HTTP/1.1\r\n\r\n"))

    def test_a_name_that_resolves_to_a_private_address_is_refused(self):
        with patch("dikdik.host_browser.resolve_public", side_effect=ValueError("private")):
            self.assertIn("403", self.ask(b"CONNECT sneaky.example:443 HTTP/1.1\r\n\r\n"))
        self.assertIn("sneaky.example", self.proxy.refused)


class PageTextTests(unittest.TestCase):
    def test_text_without_scripts_or_styles(self):
        title, text = page_text("<html><head><title> Hello  page </title><style>p{}</style></head><body>"
                                "<script>alert(1)</script><h1>Big</h1><p>One &amp; two</p><noscript>x</noscript></body></html>")
        self.assertEqual(title, "Hello page")
        self.assertEqual(text, "Big\nOne & two")


class RuntimeChoiceTests(unittest.TestCase):
    def test_the_computer_browser_is_used_when_docker_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            engine = Engine(root, Path(temp) / "data")
            try:
                with patch("dikdik.browser_service.subprocess.run", side_effect=OSError), \
                        patch("dikdik.host_browser.find_browser", return_value="C:/Edge/msedge.exe"):
                    info = engine.browser.info()
                    self.assertEqual((info["availability"], info["runtime"], info["network"]), ("host_ready", "this computer", "public_only_proxy"))
                    task = engine.store.create({"objective": "x", "mode": "agent", "verification": "local"})
                    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
                    with patch("dikdik.host_browser.capture", return_value={"status": "passed", "url": "https://example.com/", "title": "Example",
                                                                             "text": "Example text", "png": png}) as capture:
                        result = engine.browser.visit(task, "https://example.com/", {"approve_execution": True, "approve_browser": True})
                    capture.assert_called_once()
                    self.assertEqual(result["status"], "passed")
                    self.assertEqual(engine.browser.screenshot(task, result["screenshot"].rsplit("/", 1)[-1]), png)
                    with self.assertRaises(PermissionError):
                        engine.browser.visit(task, "https://example.com/", {"approve_execution": True})
            finally:
                engine.close()


@unittest.skipUnless(os.environ.get("DIKDIK_RUN_HOST_BROWSER") == "1", "set DIKDIK_RUN_HOST_BROWSER=1 to load a real public page")
class LiveCaptureTests(unittest.TestCase):
    def test_real_capture(self):
        result = host_browser.capture("https://example.com/")
        self.assertEqual(result["status"], "passed", result.get("error"))
        self.assertIn("Example", result["title"])
        self.assertTrue(result["png"].startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
