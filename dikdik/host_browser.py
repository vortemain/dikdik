"""Browse with the browser already on this computer (Edge or Chrome), headless.

Used when Docker is not running, in keeping with local mode. Two things keep it
safe for an agent to use:

- **A fresh profile every time.** The browser starts with an empty temporary
  profile: none of the user's cookies, logins, extensions or history.
- **Every request goes through a public-only proxy.** The browser is told to use a
  proxy inside Dikdik, which resolves each host itself and connects only to public
  Internet addresses on the standard ports. Redirects or page resources aimed at
  localhost, the local network or Dikdik itself are refused there, because the
  browser never resolves names on its own while a proxy is set.
"""

from __future__ import annotations

import html.parser
import os
import select
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .browser_network import public_url, resolve_public

PREVIEW_HOST = "preview.dikdik.example"

PAGE_TIMEOUT = 45
TEXT_LIMIT = 20_000
TUNNEL_LIMIT = 64 * 1024 * 1024   # bytes through one capture's proxy, both directions
CANDIDATES = {
    "win32": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%LocalAppData%\Google\Chrome\Application\chrome.exe"],
    "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
               "/Applications/Chromium.app/Contents/MacOS/Chromium"],
}


def find_browser() -> str | None:
    chosen = os.environ.get("DIKDIK_BROWSER")
    if chosen and Path(chosen).is_file():
        return chosen
    for raw in CANDIDATES.get(sys.platform, []):
        path = Path(os.path.expandvars(raw))
        if path.is_file():
            return str(path)
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(20)
        try:
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                head += chunk
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target, _ = (line.split(" ") + ["", "", ""])[:3]
            if method == "CONNECT":
                host, _, port = target.rpartition(":")
                if port != "443":
                    return self._refuse(b"403 Only HTTPS on port 443")
                upstream = self._open(f"https://{host.strip('[]')}/", host.strip("[]"), 443)
                if upstream is None:
                    return self._refuse(b"403 Not a public address")
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                rest = head.split(b"\r\n\r\n", 1)[1]
                if rest:
                    upstream.sendall(rest)
                self._pipe(upstream)
            elif (method in {"GET", "HEAD"} or self.server.allow_http_writes and method in {"POST", "PUT", "PATCH", "DELETE", "OPTIONS"}) and target.startswith("http://"):
                from urllib.parse import urlsplit
                parts = urlsplit(target)
                if parts.port not in (None, 80):
                    return self._refuse(b"403 Only HTTP on port 80")
                upstream = self._open(target, parts.hostname or "", 80)
                if upstream is None:
                    return self._refuse(b"403 Not a public address")
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                lines = head.split(b"\r\n\r\n", 1)[0].split(b"\r\n")[1:]
                lengths = [h.split(b":", 1)[1].strip() for h in lines if h.lower().startswith(b"content-length:")]
                if len(lengths) > 1 or any(h.lower().startswith(b"transfer-encoding:") for h in lines) or lengths and (not lengths[0].isdigit() or int(lengths[0]) > 1024 * 1024):
                    upstream.close()
                    return self._refuse(b"413 Request body too large or unsupported")
                kept = [h for h in lines if not h.lower().startswith((b"proxy-", b"connection:", b"keep-alive:"))]
                preview_port = getattr(self.server, "preview_port", None)
                if preview_port and parts.hostname == PREVIEW_HOST:
                    kept = [h for h in kept if not h.lower().startswith(b"host:")]
                    kept.append(f"Host: 127.0.0.1:{preview_port}".encode())
                request = f"{method} {path} HTTP/1.1\r\n".encode() + b"\r\n".join(kept) + b"\r\nConnection: close\r\n\r\n"
                upstream.sendall(request + head.split(b"\r\n\r\n", 1)[1])
                self._pipe(upstream)
            else:
                self._refuse(b"405 Only page loads are allowed")
        except (OSError, ValueError):
            return

    def _open(self, url: str, host: str, port: int):
        try:
            preview_port = getattr(self.server, "preview_port", None)
            if preview_port and host == PREVIEW_HOST and port == 80:
                return socket.create_connection(("127.0.0.1", preview_port), timeout=15)
            public_url(url)
            address = resolve_public(host, port)
            return socket.create_connection((address, port), timeout=15)
        except (OSError, ValueError):
            self.server.refused.append(host)
            return None

    def _refuse(self, status: bytes):
        self.request.sendall(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

    def _pipe(self, upstream):
        sockets = [self.request, upstream]
        transferred = 0
        try:
            while True:
                readable, _, _ = select.select(sockets, [], [], 20)
                if not readable:
                    return
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    self.server.bytes += len(data)
                    transferred += len(data)
                    if transferred > TUNNEL_LIMIT:
                        return
                    (upstream if sock is self.request else self.request).sendall(data)
        finally:
            upstream.close()


class PublicProxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, *, allow_http_writes=False, preview_port=None):
        if preview_port is not None and (type(preview_port) is not int or not 1024 <= preview_port <= 65535):
            raise ValueError("Preview port must be an assigned unprivileged port")
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.refused: list[str] = []
        self.bytes = 0
        self.allow_http_writes = allow_http_writes
        self.preview_port = preview_port
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def close(self):
        self.shutdown()
        self.server_close()


class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self._skip, self._in_title = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def page_text(markup: str) -> tuple[str, str]:
    parser = _Text()
    try:
        parser.feed(markup)
    except Exception:
        pass
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return " ".join(parser.title.split())[:300], "\n".join(line for line in lines if line)[:TEXT_LIMIT]


def _run(argv: list[str], cancel, timeout: float) -> subprocess.CompletedProcess | None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=flags)
    # Drain while the browser runs: --dump-dom can fill the pipe and prevent
    # the browser from ever exiting. Keep only a bounded UTF-8 DOM prefix.
    chunks, size = [], 0
    def read():
        nonlocal size
        for chunk in iter(lambda: process.stdout.read1(65536), b""):
            if size < 2 * 1024 * 1024:
                chunks.append(chunk[:2 * 1024 * 1024 - size])
            size += len(chunk)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if (cancel is not None and cancel.is_set()) or time.monotonic() > deadline:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)
            else:
                process.kill()
            process.wait(timeout=10)
            reader.join(timeout=5)
            process.stdout.close()
            return None
        time.sleep(0.1)
    reader.join(timeout=5)
    out = b"".join(chunks)
    process.stdout.close()
    return subprocess.CompletedProcess(argv, process.returncode, out, b"")


def capture(url: str, cancel=None, browser: str | None = None) -> dict:
    """Load one public page; return its title, text and a 1440x900 PNG screenshot."""
    url = public_url(url)
    browser = browser or find_browser()
    if browser is None:
        return {"status": "blocked", "error": "No Edge or Chrome was found on this computer"}
    proxy = PublicProxy()
    profile = Path(tempfile.mkdtemp(prefix="dikdik-browser-"))
    try:
        base = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--disable-sync", "--disable-background-networking", "--mute-audio",
                "--disable-crashpad", "--disable-hang-monitor", "--disable-client-side-phishing-detection",
                "--disable-component-update", "--disable-domain-reliability", "--disable-default-apps",
                "--disable-component-extensions-with-background-pages", "--metrics-recording-only",
                "--disable-features=OptimizationHintsFetching,OptimizationHints,Translate,msEdgeSidebarV2,msImplicitSignin",
                f"--user-data-dir={profile}", f"--proxy-server={proxy.address}", "--proxy-bypass-list=<-loopback>",
                "--window-size=1440,900", "--hide-scrollbars", "--timeout=20000"]
        shot = profile / "shot.png"
        # One launch yields both the screenshot and the DOM: a second launch doubled
        # the wait and made slow pages time out.
        done = _run([*base, f"--screenshot={shot}", "--dump-dom", url], cancel, PAGE_TIMEOUT)
        if done is None:
            return {"status": "cancelled" if cancel is not None and cancel.is_set() else "timed_out", "error": "The page did not finish loading"}
        png = shot.read_bytes() if shot.is_file() else b""
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            return {"status": "failed", "error": "The browser did not produce a screenshot", "refused": proxy.refused[:10]}
        title, text = page_text(done.stdout.decode("utf-8", "replace"))
        result = {"status": "passed", "url": url, "title": title or url, "text": text, "png": png, "runtime": "this computer"}
        if proxy.refused:
            result["refused"] = sorted(set(proxy.refused))[:10]  # private targets the page tried to reach
        return result
    finally:
        proxy.close()
        shutil.rmtree(profile, ignore_errors=True)
