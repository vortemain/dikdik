"""Running Dikdik on a remote machine or cloud VM, for one owner.

Local use stays exactly as before: loopback only. Listening anywhere else is an
explicit choice (`--listen`), and then Dikdik insists on a public URL it answers to
(the Host and Origin checks accept only that address and 127.0.0.1), HTTPS (built in
with `--tls-cert/--tls-key`, or a reverse proxy that terminates TLS, declared with
`--behind-proxy`), Secure cookies, a cap on failed sign-ins, and a projects root that
Open folder, New project and Clone cannot leave. Features that only make sense on the
user's own desktop (the native folder picker, Open in VS Code) are switched off.
This is not a multi-user service: everyone with the sign-in link is the owner.
"""

from __future__ import annotations

import ipaddress
import ssl
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

SIGN_IN_FAILURES = 10          # per client address
SIGN_IN_WINDOW = 600           # seconds


@dataclass
class RemoteConfig:
    listen: str
    public_url: str
    projects_root: Path | None = None
    tls_cert: Path | None = None
    tls_key: Path | None = None
    behind_proxy: bool = False
    failures: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def public_host(self) -> str:
        return urlsplit(self.public_url).netloc.lower()

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}".lower()

    @property
    def secure(self) -> bool:
        return self.public_url.startswith("https://")

    def allow_sign_in_attempt(self, client: str) -> bool:
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.failures.get(client, []) if now - t < SIGN_IN_WINDOW]
            self.failures[client] = recent
            return len(recent) < SIGN_IN_FAILURES

    def record_failure(self, client: str) -> None:
        with self.lock:
            self.failures.setdefault(client, []).append(time.monotonic())

    def tls_context(self) -> ssl.SSLContext | None:
        if not self.tls_cert:
            return None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(self.tls_cert), str(self.tls_key))
        return context


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def check(listen: str, public_url: str | None, tls_cert: str | None, tls_key: str | None,
          behind_proxy: bool, projects_root: str | None) -> RemoteConfig | None:
    """Validate the remote flags; None means ordinary local use."""
    if is_loopback(listen) and not public_url:
        if projects_root:
            raise ValueError("--projects-root applies when Dikdik listens beyond this computer")
        return None
    if not public_url:
        raise ValueError("Listening beyond this computer needs --public-url, the address people will open (https://...)")
    parts = urlsplit(public_url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.path not in {"", "/"} or parts.query or parts.username:
        raise ValueError("--public-url must be a plain origin such as https://dikdik.example.com")
    if parts.scheme == "http" and not is_loopback(parts.hostname):
        raise ValueError("--public-url must use https:// unless it is this computer")
    if bool(tls_cert) != bool(tls_key):
        raise ValueError("Give both --tls-cert and --tls-key, or neither")
    if not is_loopback(listen) and not tls_cert and not behind_proxy:
        raise ValueError("Serve HTTPS: pass --tls-cert and --tls-key, or --behind-proxy when a reverse proxy terminates TLS")
    root = None
    if projects_root:
        root = Path(projects_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"--projects-root {root} is not a folder")
    return RemoteConfig(listen=listen, public_url=public_url.rstrip("/"), projects_root=root,
                        tls_cert=Path(tls_cert) if tls_cert else None, tls_key=Path(tls_key) if tls_key else None,
                        behind_proxy=behind_proxy)
