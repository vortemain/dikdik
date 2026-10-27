"""Minimal WebSocket protocol handler using only the Python standard library.

Implements just enough of RFC 6455 for the session events socket:
- The HTTP upgrade handshake (Sec-WebSocket-Key/Accept)
- Text frames (opcode 0x1) with client-side masking
- Close frames (opcode 0x8)
- Ping/pong (opcode 0x9/0xA)

No pip dependencies. The handler runs in a ThreadingHTTPServer thread and
manages one connection at a time per task.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import threading
import time

MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + MAGIC).encode()).digest()).decode()


def read_frame(sock) -> tuple[int, bytes] | None:
    """Read one WebSocket frame. Returns (opcode, payload) or None on close."""
    header = sock.recv(2)
    if len(header) < 2:
        return None
    fin = header[0] & 0x80
    opcode = header[0] & 0x0F
    masked = header[1] & 0x80
    length = header[1] & 0x7F
    if length == 126:
        ext = sock.recv(2)
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = sock.recv(8)
        length = struct.unpack("!Q", ext)[0]
    if masked:
        mask = sock.recv(4)
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(min(length - len(payload), 65536))
        if not chunk:
            return None
        payload += chunk
    if masked:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    if opcode == OP_CLOSE:
        return None
    if opcode == OP_PING:
        send_frame(sock, OP_PONG, payload)
        return read_frame(sock)
    return opcode, payload


def send_frame(sock, opcode: int, payload: bytes) -> None:
    """Send a WebSocket frame (server-to-client, no mask)."""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)
    try:
        sock.sendall(bytes(header) + payload)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass


def send_text(sock, text: str) -> None:
    send_frame(sock, OP_TEXT, text.encode("utf-8"))


def send_close(sock) -> None:
    send_frame(sock, OP_CLOSE, b"")