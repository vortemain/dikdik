"""Fast, working previews of the project's own app: the proxy keeps slow compiles, live-reload
sockets and form posts working for the preview host only, and the dev server stays running
for the Browser panel and the next preview."""
import socket
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik import host_browser, local_preview
from dikdik.host_browser import PREVIEW_HOST, PublicProxy


def serve_once(handler):
    """A loopback server that hands each connection to `handler(conn)`; returns its port."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def loop():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return listener


def read_request(conn):
    data = b""
    while b"\r\n\r\n" not in data:
        data += conn.recv(65536)
    head, _, body = data.partition(b"\r\n\r\n")
    if b"transfer-encoding: chunked" in head.lower():
        while not body.endswith(b"0\r\n\r\n"):
            body += conn.recv(65536)
    return head, body


def through_proxy(proxy, raw: bytes, timeout=10) -> bytes:
    with socket.create_connection(proxy.server_address, timeout=timeout) as client:
        client.sendall(raw)
        reply = b""
        while True:
            try:
                chunk = client.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            reply += chunk
        return reply


class PreviewProxyTests(unittest.TestCase):
    def setUp(self):
        self.seen = []

        def app(conn):
            with conn:
                head, body = read_request(conn)
                self.seen.append((head, body))
                if getattr(self, "slow", 0):
                    time.sleep(self.slow)
                reply = b"ok:" + head.split(b" ", 1)[0] + b":" + body
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(reply), reply))

        self.listener = serve_once(app)
        self.addCleanup(self.listener.close)
        self.port = self.listener.getsockname()[1]
        self.proxy = PublicProxy(allow_http_writes=False, preview_port=self.port)  # serves on its own thread
        self.addCleanup(self.proxy.close)

    def test_the_app_may_post_any_body_and_gets_its_own_host(self):
        raw = (f"POST http://{PREVIEW_HOST}/actions HTTP/1.1\r\nHost: {PREVIEW_HOST}\r\nTransfer-Encoding: chunked\r\n\r\n"
               "5\r\nhello\r\n0\r\n\r\n").encode()
        reply = through_proxy(self.proxy, raw)
        self.assertIn(b"200 OK", reply)
        self.assertIn(b"ok:POST:5\r\nhello", reply)
        self.assertIn(f"Host: 127.0.0.1:{self.port}".encode(), self.seen[0][0])

    def test_a_slow_first_compile_is_waited_for(self):
        # The app answers after longer than a public connection may stay silent.
        self.slow = 1.5
        with patch.object(host_browser, "IDLE_SECONDS", 0.5):
            reply = through_proxy(self.proxy, f"GET http://{PREVIEW_HOST}/ HTTP/1.1\r\nHost: {PREVIEW_HOST}\r\n\r\n".encode())
        self.assertIn(b"ok:GET:", reply)

    def test_live_reload_sockets_reach_only_the_app(self):
        reply = through_proxy(self.proxy, f"CONNECT {PREVIEW_HOST}:80 HTTP/1.1\r\nHost: {PREVIEW_HOST}:80\r\n\r\n".encode(), timeout=2)
        self.assertTrue(reply.startswith(b"HTTP/1.1 200 Connection established"), reply)
        refused = through_proxy(self.proxy, b"CONNECT example.com:80 HTTP/1.1\r\nHost: example.com:80\r\n\r\n", timeout=2)
        self.assertIn(b"403", refused)

    def test_public_sites_keep_their_limits(self):
        # Refused before any connection: a public page only loads, unless writes were allowed.
        post = through_proxy(self.proxy, b"POST http://example.com/ HTTP/1.1\r\nHost: example.com\r\nContent-Length: 1\r\n\r\nx", timeout=2)
        self.assertIn(b"405", post)


class LoopbackTests(unittest.TestCase):
    def test_the_address_that_answered_is_tried_first(self):
        host_browser._LOOPBACK_FAMILY.pop(40111, None)
        calls = []

        def connect(address, timeout):
            calls.append(address[0])
            if address[0] == "127.0.0.1":
                raise ConnectionRefusedError()
            return socket.socket()

        with patch("dikdik.host_browser.socket.create_connection", side_effect=connect):
            host_browser.connect_loopback(40111, 1).close()
            host_browser.connect_loopback(40111, 1).close()
        self.assertEqual(calls, ["127.0.0.1", "::1", "::1"])


class FakeShells:
    def __init__(self):
        self.started, self.closed, self.running = [], False, True

    def start(self, root, command, env=None):
        self.started.append(command)
        return {"id": "s1"}

    def poll(self, shell_id):
        return {"status": "running" if self.running and not self.closed else "exited", "output": "ready"}

    def close(self):
        self.closed = True


class FakeSessions:
    def __init__(self, preview_port=None):
        self.port = preview_port

    def execute(self, task, action, arguments, cancel=None):
        return {"status": "passed", "text": "Shop", "elements": [{"tag": "a"}], "layout": {"width": 1440, "scroll_width": 1440},
                "url": f"http://{PREVIEW_HOST}/"}

    def close(self):
        pass


class LiveServerTests(unittest.TestCase):
    def run_preview(self, root, command, live, shells):
        with patch.object(local_preview, "available", return_value=True), \
                patch.object(local_preview, "BrowserSessions", FakeSessions), \
                patch.object(local_preview, "BackgroundShells", return_value=shells), \
                patch.object(local_preview, "connect_loopback", side_effect=lambda port, timeout: socket.socket()), \
                patch.object(local_preview, "warm_up"):
            return local_preview.preview(root, command, threading.Event(), live=live)

    def test_a_dev_server_is_kept_and_reused_by_the_next_preview(self):
        live, root = local_preview.LiveServer(), Path(".")
        first = FakeShells()
        result = self.run_preview(root, "npm run dev -- --port {port}", live, first)
        self.assertEqual(result["status"], "passed", result)
        self.assertFalse(first.closed)  # still running for the Browser panel
        status = live.status()
        self.assertTrue(status["running"])
        self.assertEqual(status["port"], result["server_port"])
        second = FakeShells()
        again = self.run_preview(root, "npm run dev -- --port {port}", live, second)
        self.assertTrue(again["server_reused"])
        self.assertEqual(again["server_port"], result["server_port"])
        self.assertEqual(second.started, [])  # nothing started: the page was ready at once

    def test_a_production_server_or_another_command_starts_fresh(self):
        live, root = local_preview.LiveServer(), Path(".")
        first = FakeShells()
        self.run_preview(root, "npx next start --port {port}", live, first)
        second = FakeShells()
        result = self.run_preview(root, "npx next start --port {port}", live, second)
        self.assertNotIn("server_reused", result)  # next start serves its last build: never reused
        self.assertEqual(len(second.started), 1)
        self.assertTrue(first.closed)  # replaced, not left running beside the new one
        live.stop()
        self.assertTrue(second.closed)
        self.assertFalse(live.status()["running"])

    def test_without_live_the_server_is_stopped_as_before(self):
        shells = FakeShells()
        self.run_preview(Path("."), "npm run dev -- --port {port}", None, shells)
        self.assertTrue(shells.closed)

    def test_a_server_that_died_is_not_reused(self):
        live, root = local_preview.LiveServer(), Path(".")
        first = FakeShells()
        self.run_preview(root, "npm run dev -- --port {port}", live, first)
        first.running = False
        second = FakeShells()
        result = self.run_preview(root, "npm run dev -- --port {port}", live, second)
        self.assertNotIn("server_reused", result)
        self.assertEqual(len(second.started), 1)


if __name__ == "__main__":
    unittest.main()
