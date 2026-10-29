"""Credential-free public GET relay for the network-disabled browser container."""

import base64
import http.client
import ipaddress
import queue
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit


RESOURCE_LIMIT = 2 * 1024 * 1024


def public_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("Enter a public HTTP or HTTPS URL")
    try:
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise ValueError("Enter a valid public URL") from None
    if parts.scheme not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        raise ValueError("Browser URLs must use HTTP or HTTPS without credentials")
    if port not in {None, 80 if parts.scheme == "http" else 443}:
        raise ValueError("Browser URLs must use the standard HTTP or HTTPS port")
    host = host.rstrip(".").encode("idna").decode("ascii").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
        raise ValueError("Private and local browser targets are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("Browser targets must be public Internet hosts") from None
    else:
        if not address.is_global or address.is_multicast or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None):
            raise ValueError("Private and special-use browser addresses are not allowed")
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def resolve_public(host: str, port: int, timeout: float = 3) -> str:
    """Bound DNS and validate all answers, then connect to one pinned IP."""
    replies = queue.Queue(maxsize=1)
    def resolve():
        try:
            replies.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            replies.put(None)
    threading.Thread(target=resolve, daemon=True).start()
    try:
        rows = replies.get(timeout=timeout)
    except queue.Empty:
        raise ValueError("Browser DNS lookup timed out") from None
    if not rows:
        raise ValueError("Browser host could not be resolved")
    addresses = [ipaddress.ip_address(row[4][0]) for row in rows]
    if any(not addr.is_global or addr.is_multicast or
           (isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None) for addr in addresses):
        raise ValueError("Browser DNS resolved to a private or special-use address")
    return str(addresses[0])


def fetch_public(url: str, timeout: float = 5) -> dict:
    """No proxies, cookies, authorization, ambient headers, or automatic redirects."""
    url = public_url(url)
    parts = urlsplit(url)
    host, port = parts.hostname, 443 if parts.scheme == "https" else 80
    address = resolve_public(host, port, min(3, timeout))
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    raw = socket.create_connection((address, port), timeout=timeout)
    transport = raw
    def abort():
        try:
            (connection.sock or transport).shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    watchdog = threading.Timer(timeout, abort)
    watchdog.daemon = True
    watchdog.start()
    try:
        transport = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if parts.scheme == "https" else raw
        connection.sock = transport
        target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        connection.request("GET", target, headers={"User-Agent": "Dikdik-Browser/1", "Accept": "*/*", "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        body, deadline = bytearray(), time.monotonic() + timeout
        while True:
            if response.isclosed() is True:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Browser resource timed out")
            (connection.sock or transport).settimeout(remaining)
            block = response.read1(min(65536, RESOURCE_LIMIT + 1 - len(body)))
            if not block:
                break
            body.extend(block)
            if len(body) > RESOURCE_LIMIT:
                raise ValueError("Browser resource exceeds 2 MiB")
        headers = {"content-type": response.getheader("Content-Type", "application/octet-stream")}
        location = response.getheader("Location")
        if location and 300 <= response.status < 400:
            headers["location"] = public_url(urljoin(url, location))
        return {"status": response.status, "headers": headers, "body": base64.b64encode(body).decode("ascii")}
    finally:
        watchdog.cancel()
        connection.close()
        transport.close()
        raw.close()
