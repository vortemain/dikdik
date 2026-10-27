"""WebSocket session socket for bidirectional task event streaming."""

import base64
import hashlib
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server import make_server

MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_connect(host, port, path, token):
    """Open a WebSocket connection and return the raw socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Authorization: Bearer {token}\r\n"
        f"Origin: http://{host}:{port}\r\n"
        f"\r\n"
    )
    sock.sendall(request.encode())
    # Read the HTTP response headers
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    status_line = response.split(b"\r\n")[0].decode()
    sock.settimeout(5)
    return sock, status_line


def ws_recv(sock):
    """Read one WebSocket text frame."""
    header = sock.recv(2)
    if len(header) < 2:
        return None
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        ext = sock.recv(2)
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = sock.recv(8)
        length = struct.unpack("!Q", ext)[0]
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(min(length - len(payload), 65536))
        if not chunk:
            break
        payload += chunk
    if opcode == 0x8:  # close
        return None
    return payload.decode("utf-8")


def ws_send(sock, text):
    """Send a masked text frame (client-to-server)."""
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    header = bytearray([0x80 | 0x1, 0x80 | len(payload)])
    if len(payload) < 126:
        header[1] = 0x80 | len(payload)
    elif len(payload) < 65536:
        header[1] = 0x80 | 126
        header += struct.pack("!H", len(payload))
    else:
        header[1] = 0x80 | 127
        header += struct.pack("!Q", len(payload))
    sock.sendall(bytes(header) + mask + masked)


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / "repo"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.engine = Engine(root, base / "data")
        self.addCleanup(self.engine.close)
        self.server = make_server(self.engine, 0, "test-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def make_task(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        conn.request("POST", "/api/tasks", body=json.dumps({
            "objective": "Change x", "mode": "offline", "verification": "syntax",
            "approve_external": False, "approve_execution": False,
            "edits": [{"path": "app.py", "before": "1", "after": "2"}]}),
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"})
        r = conn.getresponse()
        task = json.loads(r.read())["id"]
        conn.close()
        return task

    def test_websocket_handshake_succeeds(self):
        task = self.make_task()
        sock, status = ws_connect("127.0.0.1", self.server.server_port, f"/api/tasks/{task}/ws", "test-token")
        self.assertIn("101", status)
        sock.close()

    def test_websocket_wrong_token_refused(self):
        task = self.make_task()
        sock, status = ws_connect("127.0.0.1", self.server.server_port, f"/api/tasks/{task}/ws", "wrong-token")
        self.assertIn("401", status)
        sock.close()

    def test_websocket_unknown_task_404(self):
        sock, status = ws_connect("127.0.0.1", self.server.server_port, "/api/tasks/nonexistent/ws", "test-token")
        self.assertIn("404", status)
        sock.close()

    def test_websocket_receives_events(self):
        task = self.make_task()
        sock, status = ws_connect("127.0.0.1", self.server.server_port, f"/api/tasks/{task}/ws", "test-token")
        self.assertIn("101", status)
        # Wait for events (the task is already completed)
        messages = []
        for _ in range(20):
            try:
                msg = ws_recv(sock)
                if msg:
                    messages.append(json.loads(msg))
                    if any(m.get("type") == "end" for m in messages):
                        break
            except (socket.timeout, OSError):
                break
        sock.close()
        self.assertTrue(len(messages) > 0)
        self.assertTrue(any(m.get("type") in ("event", "end") for m in messages))

    def test_websocket_sends_message(self):
        task = self.make_task()
        sock, status = ws_connect("127.0.0.1", self.server.server_port, f"/api/tasks/{task}/ws", "test-token")
        self.assertIn("101", status)
        # Send a follow-up message
        ws_send(sock, json.dumps({"type": "message", "objective": "Again", "mode": "offline",
                                   "verification": "syntax",
                                   "edits": [{"path": "app.py", "before": "2", "after": "3"}]}))
        # Should get a queued response
        messages = []
        for _ in range(30):
            try:
                msg = ws_recv(sock)
                if msg:
                    messages.append(json.loads(msg))
                    if any(m.get("type") == "queued" for m in messages):
                        break
            except (socket.timeout, OSError):
                break
        sock.close()
        self.assertTrue(any(m.get("type") in ("queued", "error") for m in messages),
                        f"messages: {messages}")


if __name__ == "__main__":
    unittest.main()