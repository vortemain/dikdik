"""Work on another computer over SSH (ADR-009).

Dikdik runs on the host itself, bound to the host's 127.0.0.1, and this computer
reaches it through an `ssh -L` tunnel; every tool then runs there unchanged. Only the
system OpenSSH client is used, so keys, ssh-agent, ProxyJump and known_hosts behave as
in a terminal. Every call is non-interactive (`BatchMode=yes`) with strict host-key
checking; an unknown host key is shown to the user and saved only once they confirm
its fingerprint. Passwords are never asked for or stored, and the host's sign-in
token stays in memory here: never logged, printed or handed to an API key.
"""

from __future__ import annotations

import base64
import errno
import glob
import gzip
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, logs
from .projects import NAME, check_clone_url, repository_name

READY = "DIKDIK-SSH-READY"
MIN_PYTHON = (3, 13)
PYTHONS = ("python3.15", "python3.14", "python3.13", "python3", "python")
MAX_CONNECTIONS = 8
PROBE_TIMEOUT = 45
INSTALL_TIMEOUT = 300
CLONE_TIMEOUT = 900
DEFAULT_CLONE_PARENT = "~/projects"
READY_TIMEOUT = 90
TUNNEL_TIMEOUT = 30
DECISION_TIMEOUT = 600
HOST = re.compile(r"(?:ssh://)?(?:[A-Za-z0-9._%+-]+@)?(?:[A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?")
VERSION = re.sub(r"[^0-9A-Za-z._+-]", "_", __version__)
SKIPPED_SUFFIXES = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib"}
# Never offer anything to a host whose key is not verified yet: the key-fetching probe
# switches every authentication method off and records keys in a scratch file.
NO_AUTH = ["-o", "PubkeyAuthentication=no", "-o", "PasswordAuthentication=no",
           "-o", "KbdInteractiveAuthentication=no", "-o", "GSSAPIAuthentication=no",
           "-o", "HostbasedAuthentication=no", "-o", "IdentitiesOnly=yes"]
# OpenSSH keeps the first value given for an option, so StrictHostKeyChecking is set once per call.
BASE = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "ForwardAgent=no",
        "-o", "ForwardX11=no", "-o", "PermitLocalCommand=no"]
KEY_GUIDE = ("Dikdik signs in with SSH keys or ssh-agent only and never asks for passwords. "
             "Create a key with `ssh-keygen -t ed25519`, add it to the host with `ssh-copy-id {host}` "
             "(or append the .pub file to ~/.ssh/authorized_keys there), and if the key has a "
             "passphrase, load it with `ssh-add`. Then connect again.")

PROBE = """for p in {pythons}; do
  if command -v "$p" >/dev/null 2>&1; then
    "$p" -c 'import sys; print("DIKDIK-PY", "%d.%d.%d" % sys.version_info[:3], sys.executable)' 2>/dev/null
  fi
done
uname -s 2>/dev/null | sed 's/^/DIKDIK-OS /'
sed 's/^/DIKDIK-STAMP /' "$HOME/.dikdik-remote/{version}/stamp" 2>/dev/null
true"""

INSTALL = """import os, shutil, sys, tarfile
base = os.path.join(os.path.expanduser("~"), ".dikdik-remote")
os.makedirs(base, mode=0o700, exist_ok=True)
target = os.path.join(base, sys.argv[1])
part = target + ".part"
shutil.rmtree(part, ignore_errors=True)
os.makedirs(part, mode=0o700)
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
    archive.extractall(part, filter="data")
shutil.rmtree(target, ignore_errors=True)
os.replace(part, target)
with open(os.path.join(target, "stamp"), "w", encoding="utf-8") as handle:
    handle.write(sys.argv[2] + "\\n")
print("DIKDIK-INSTALLED", flush=True)
"""

# Runs on the host with its own Git and sign-in. Hooks and fsmonitor are off, as for local clones;
# a folder that already holds a clone of the same repository is reused.
CLONE = """import os, shutil, subprocess, sys
url, parent, name = sys.argv[1:4]
base = os.path.expanduser(parent)
os.makedirs(base, exist_ok=True)
target = os.path.join(base, name)
git = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
       "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always", "-c", "protocol.file.allow=always"]
env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
try:
    if os.path.exists(target):
        origin = subprocess.run(git + ["-C", target, "remote", "get-url", "origin"], capture_output=True, text=True,
                                env=env, stdin=subprocess.DEVNULL).stdout.strip()
        if origin != url:
            print("DIKDIK-CLONE-EXISTS", flush=True)
            raise SystemExit(3)
    else:
        done = subprocess.run(git + ["clone", "--quiet", "--no-recurse-submodules", "--", url, target],
                              capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        if done.returncode:
            shutil.rmtree(target, ignore_errors=True)
            sys.stderr.write(done.stderr)
            raise SystemExit(5)
except FileNotFoundError:
    print("DIKDIK-NO-GIT", flush=True)
    raise SystemExit(4)
print("DIKDIK-CLONED", flush=True)
"""

RUN = """import os, sys
base = os.path.join(os.path.expanduser("~"), ".dikdik-remote")
sys.path.insert(0, os.path.join(base, sys.argv[1]))
sys.argv = ["dikdik", os.path.expanduser(sys.argv[2]), *sys.argv[3:], "--data", os.path.join(base, "data")]
from dikdik.server import main
raise SystemExit(main())
"""


class SSHError(ValueError):
    """A connection problem worded for the user."""


@dataclass(frozen=True)
class SSHSession:
    """This Dikdik was started on a host by Connect to host (`--ssh-session`)."""
    label: str
    forwarded_port: int


# --- hosts -----------------------------------------------------------------------

def find_ssh() -> str | None:
    found = shutil.which("ssh")
    if found:
        return found
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
    return str(system) if os.name == "nt" and system.is_file() else None


def config_hosts(path: Path | None = None) -> list[dict]:
    """Named hosts from ~/.ssh/config and the files it includes; wildcard patterns are skipped."""
    path = path or Path.home() / ".ssh" / "config"
    hosts: dict[str, dict] = {}
    seen: set[Path] = set()

    def read(file: Path, depth: int) -> None:
        try:
            resolved = file.resolve()
            if resolved in seen or depth > 8 or not file.is_file():
                return
            seen.add(resolved)
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        current: list[dict] = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            pair = re.split(r"\s*=\s*|\s+", line, maxsplit=1)
            key, value = pair[0].lower(), (pair[1].strip() if len(pair) > 1 else "")
            if key == "include":
                for pattern in shlex.split(value):
                    pattern = os.path.expanduser(pattern)
                    if not os.path.isabs(pattern):
                        pattern = str(path.parent / pattern)
                    for match in sorted(glob.glob(pattern)):
                        read(Path(match), depth + 1)
                continue
            if key == "match":
                current = []
                continue
            if key == "host":
                current = []
                for name in value.split():
                    if any(ch in name for ch in "*?!") or not HOST.fullmatch(name):
                        continue
                    current.append(hosts.setdefault(name, {"name": name, "hostname": None, "user": None}))
                continue
            if key in {"hostname", "user"}:
                for entry in current:
                    if entry[key] is None:
                        entry[key] = value
    read(path, 0)
    return list(hosts.values())


def check_host(host) -> str:
    host = str(host or "").strip()
    if not host:
        raise SSHError("Choose a host from your SSH config or type user@host")
    if host.startswith("-") or len(host) > 255 or not HOST.fullmatch(host):
        raise SSHError("Type a host as alias, user@host or ssh://user@host:port")
    return host


def check_repository(url, parent) -> tuple[str, str, str]:
    """(url, parent folder on the host, clone folder name) for Open a repository on a host."""
    try:
        url = check_clone_url(url)
    except ValueError as exc:
        raise SSHError(str(exc).replace("this computer's own sign-in", "the host's own sign-in")) from None
    parent = check_folder(parent or DEFAULT_CLONE_PARENT)
    name = repository_name(url)
    if not NAME.fullmatch(name) or name in {".", ".."}:
        raise SSHError("Could not name a folder after that repository URL")
    return url, parent, name


def check_folder(folder) -> str:
    folder = str(folder or "").strip()
    if not folder:
        raise SSHError("Type the folder on the host to open, such as ~/projects/app")
    if len(folder) > 1000 or any(ord(ch) < 32 for ch in folder):
        raise SSHError("That folder path is not valid")
    if not (folder.startswith("/") or folder == "~" or folder.startswith("~/")):
        raise SSHError("Type a full path on the host, such as ~/projects/app or /srv/app")
    return folder


# --- what is sent to the host ----------------------------------------------------

_archive: tuple[bytes, str] | None = None
_archive_lock = threading.Lock()


def package_archive(root: Path | None = None) -> tuple[bytes, str]:
    """This Dikdik as a deterministic .tar.gz and its SHA-256, without caches or native code."""
    global _archive
    with _archive_lock:
        if root is None and _archive is not None:
            return _archive
        package = root or Path(__file__).parent
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as packed, tarfile.open(fileobj=packed, mode="w") as archive:
            for current, dirs, files in os.walk(package):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith(".")
                                 and not os.path.islink(os.path.join(current, d)))
                for name in sorted(files):
                    file = Path(current) / name
                    if file.is_symlink() or file.suffix.lower() in SKIPPED_SUFFIXES or name.startswith("dikdik_index"):
                        continue
                    data = file.read_bytes()
                    info = tarfile.TarInfo("dikdik/" + file.relative_to(package).as_posix())
                    info.size, info.mode, info.mtime = len(data), 0o644, 0
                    archive.addfile(info, io.BytesIO(data))
        result = buffer.getvalue(), hashlib.sha256(buffer.getvalue()).hexdigest()
        if root is None:
            _archive = result
        return result


def remote_command(python: str, code: str, *args: str) -> str:
    return " ".join(shlex.quote(part) for part in (python, "-I", "-c", code, *args))


def probe_command() -> str:
    return "sh -c " + shlex.quote(PROBE.format(pythons=" ".join(PYTHONS), version=VERSION))


def parse_probe(output: str) -> dict:
    pythons, os_name, stamp = [], None, None
    for line in output.splitlines():
        parts = line.strip().split(" ", 2)
        if parts[0] == "DIKDIK-PY" and len(parts) == 3:
            try:
                version = tuple(int(n) for n in parts[1].split("."))
            except ValueError:
                continue
            pythons.append({"version": version, "path": parts[2]})
        elif parts[0] == "DIKDIK-OS" and len(parts) > 1:
            os_name = " ".join(parts[1:])
        elif parts[0] == "DIKDIK-STAMP" and len(parts) > 1:
            stamp = parts[1]
    usable = sorted((p for p in pythons if p["version"][:2] >= MIN_PYTHON and p["path"].startswith("/")),
                    key=lambda p: p["version"], reverse=True)
    return {"pythons": pythons, "python": usable[0] if usable else None, "os": os_name, "stamp": stamp}


def fingerprints(known_hosts: str) -> list[dict]:
    keys = []
    for line in known_hosts.splitlines():
        fields = line.split()
        if fields and fields[0].startswith("@"):
            fields = fields[1:]
        if len(fields) < 3:
            continue
        try:
            blob = base64.b64decode(fields[2], validate=True)
        except ValueError:
            continue
        digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
        keys.append({"type": fields[1], "fingerprint": "SHA256:" + digest})
    return keys


def explain(stderr: str, host: str) -> tuple[str, str]:
    """(kind, message) for a failed ssh call; never echoes more than a short, redacted line."""
    text = stderr or ""
    if re.search(r"REMOTE HOST IDENTIFICATION HAS CHANGED|Host key for .+ has changed", text):
        return "host_key_changed", (f"The host key of {host} has changed since you last connected. This can mean the server "
                                    "was reinstalled, or that someone is intercepting the connection. Check with whoever runs it; "
                                    f"if the change is expected, remove the old key with `ssh-keygen -R <hostname>` and connect again.")
    if re.search(r"No \S+ host key is known for|Host key verification failed", text):
        return "host_key_unknown", f"{host} is not in your known_hosts yet."
    if re.search(r"Permission denied|Too many authentication failures|no authentication methods", text, re.I):
        return "auth", f"{host} refused the SSH key sign-in. " + KEY_GUIDE.format(host=host)
    if re.search(r"Could not resolve hostname|Name or service not known|nodename nor servname", text, re.I):
        return "unreachable", f"{host} could not be found. Check the name, or add it to ~/.ssh/config."
    if re.search(r"Connection refused", text, re.I):
        return "unreachable", f"{host} refused the connection. Is its SSH server running on that port?"
    if re.search(r"timed out|Network is unreachable|No route to host", text, re.I):
        return "unreachable", f"{host} did not answer. Check the network, VPN or ProxyJump settings."
    if re.search(r"'sh' is not recognized|sh: (command )?not found|not recognized as an internal", text):
        return "unsupported", f"{host} has no POSIX shell. Connect to host supports Linux and macOS hosts for now."
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    detail = logs.redact(lines[-1])[:300] if lines else "no details"
    return "failed", f"SSH to {host} failed: {detail}"


def free_port() -> tuple[int, bool]:
    """A local port free on 127.0.0.1 and, where this computer has it, on ::1 too.

    Browsers may try ::1 first for `localhost`, so the tunnel listens on both or the
    window could reach whatever else holds ::1 on that port."""
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as four:
            four.bind(("127.0.0.1", 0))
            port = four.getsockname()[1]
            try:
                with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as six:
                    six.bind(("::1", port))
                return port, True
            except OSError as exc:
                if getattr(exc, "errno", None) in {errno.EADDRINUSE, 10048}:  # in use on ::1: try another port
                    continue
                return port, False
    raise SSHError("No free local port for the tunnel")


# --- connections -----------------------------------------------------------------

@dataclass
class Connection:
    id: str
    host: str
    folder: str
    state: str = "connecting"
    step: str = "Connecting"
    error: str | None = None
    kind: str | None = None
    keys: list = field(default_factory=list)
    python: str | None = None
    port: int | None = None
    created: float = field(default_factory=time.time)
    repo: str | None = None
    parent: str | None = None
    token: str | None = field(default=None, repr=False)
    scratch: str = field(default="", repr=False)
    decision: threading.Event = field(default_factory=threading.Event, repr=False)
    trusted: bool = False
    closing: bool = False
    procs: dict = field(default_factory=dict, repr=False)

    def public(self) -> dict:
        return {"id": self.id, "host": self.host, "folder": self.folder, "state": self.state, "step": self.step,
                "error": self.error, "kind": self.kind, "host_keys": self.keys if self.state == "host_key" else [],
                "python": self.python, "port": self.port, "created": self.created, "repo": self.repo, "parent": self.parent}


class SSHConnections:
    """Connect to host: bootstrap Dikdik on another computer and tunnel to it."""

    def __init__(self, ssh: list[str] | None = None, unavailable: str | None = None, config: Path | None = None):
        self.ssh = ssh
        self.unavailable = unavailable
        self.config = config
        self.connections: dict[str, Connection] = {}
        self.lock = threading.Lock()

    def command(self) -> list[str]:
        if self.unavailable:
            raise SSHError(self.unavailable)
        if self.ssh is None:
            found = find_ssh()
            if not found:
                raise SSHError("No OpenSSH client found. Install OpenSSH (on Windows: Settings → Optional features → OpenSSH Client).")
            self.ssh = [found]
        return list(self.ssh)

    def hosts(self) -> dict:
        try:
            self.command()
        except SSHError as exc:
            return {"available": False, "reason": str(exc), "hosts": []}
        return {"available": True, "reason": None, "hosts": config_hosts(self.config)}

    def list(self) -> list[dict]:
        with self.lock:
            return [c.public() for c in sorted(self.connections.values(), key=lambda c: c.created)]

    def get(self, cid: str) -> Connection:
        with self.lock:
            return self.connections[cid]

    def connect(self, host, folder, repo=None) -> dict:
        self.command()
        host = check_host(host)
        parent = None
        if repo:
            repo, parent, name = check_repository(repo, folder)
            folder = parent.rstrip("/") + "/" + name
        else:
            folder = check_folder(folder)
        with self.lock:
            live = [c for c in self.connections.values() if c.state in {"connecting", "host_key", "connected"}]
            for existing in live:
                if existing.host == host and existing.folder == folder:
                    return existing.public()
            if len(live) >= MAX_CONNECTIONS:
                raise SSHError(f"At most {MAX_CONNECTIONS} hosts can be connected at once; disconnect one first")
            for stale in [c.id for c in self.connections.values() if c.state in {"failed", "closed"}]:
                del self.connections[stale]
            conn = Connection(secrets.token_hex(8), host, folder, repo=repo or None, parent=parent)
            self.connections[conn.id] = conn
        threading.Thread(target=self._run, args=(conn,), name=f"ssh-{conn.id}", daemon=True).start()
        return conn.public()

    def trust(self, cid: str, fingerprint) -> dict:
        conn = self.get(cid)
        if conn.state != "host_key":
            raise SSHError("This connection is not waiting for a host key decision")
        if fingerprint not in {key["fingerprint"] for key in conn.keys}:
            raise SSHError("That fingerprint is not the one the host presented")
        conn.trusted = True
        conn.decision.set()
        return conn.public()

    def link(self, cid: str) -> dict:
        conn = self.get(cid)
        if conn.state != "connected" or not conn.token:
            raise SSHError("This host is not connected")
        return {"url": f"http://localhost:{conn.port}/#token={conn.token}", "host": conn.host}

    def disconnect(self, cid: str) -> dict:
        conn = self.get(cid)
        conn.closing = True
        conn.decision.set()
        self._stop(conn)
        if conn.state != "failed":
            conn.state, conn.step = "closed", "Disconnected"
        return conn.public()

    def close_all(self) -> None:
        with self.lock:
            connections = list(self.connections.values())
        for conn in connections:
            conn.closing = True
            conn.decision.set()
            self._stop(conn)

    def route(self, method: str, path: str, body, owner: bool):
        """(status, data) for /api/ssh/..., or None for any other path."""
        if not path.startswith("/api/ssh/"):
            return None
        parts = path.strip("/").split("/")
        body = body if isinstance(body, dict) else {}
        try:
            if parts == ["api", "ssh", "hosts"] and method == "GET":
                return 200, self.hosts()
            if parts == ["api", "ssh", "connections"]:
                if method == "GET":
                    return 200, {"connections": self.list()}
                if method == "POST":
                    return 202, self.connect(body.get("host"), body.get("folder"), body.get("repo"))
            if len(parts) >= 4 and parts[2] == "connections" and re.fullmatch(r"[0-9a-f]{16}", parts[3]):
                if len(parts) == 4 and method == "DELETE":
                    return 200, self.disconnect(parts[3])
                if len(parts) == 5 and parts[4] == "trust" and method == "POST":
                    return 200, self.trust(parts[3], body.get("fingerprint"))
                if len(parts) == 5 and parts[4] == "link" and method == "POST":
                    if not owner:
                        return 403, {"error": "Open remote windows from the signed-in Dikdik window, not with an API key"}
                    return 200, self.link(parts[3])
        except KeyError:
            return 404, {"error": "No such connection"}
        except SSHError as exc:
            return 400, {"error": str(exc)}
        return 404, {"error": "Not found"}

    # --- the connection itself ---------------------------------------------------

    def _ssh(self, conn: Connection, *extra: str, command: str | None = None, strict: str = "yes") -> list[str]:
        args = [*self.command(), "-o", f"StrictHostKeyChecking={strict}", *BASE, *extra, "--", conn.host]
        return args + [command] if command is not None else args

    def _call(self, args: list[str], timeout: int, data: bytes | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(args, input=data, capture_output=True, timeout=timeout,
                                  stdin=None if data is not None else subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            raise SSHError("The host took too long to answer") from exc
        except OSError as exc:
            raise SSHError(f"Could not start ssh: {exc.strerror or exc}") from exc

    def _fail(self, conn: Connection, kind: str, text: str) -> None:
        conn.kind, conn.error, conn.state, conn.step = kind, text, "failed", "Failed"
        logs.log("ssh_connect_failed", kind=kind, host=conn.host)

    def _run(self, conn: Connection) -> None:
        try:
            self._bootstrap(conn)
        except SSHError as exc:
            self._stop(conn)
            if not conn.closing:
                self._fail(conn, conn.kind or "failed", str(exc))
        except Exception as exc:  # a bug must still end the connection visibly
            logs.log("ssh_connect_error", exc_info=True, host=conn.host)
            self._stop(conn)
            if not conn.closing:
                self._fail(conn, "failed", f"Connect to host stopped: {type(exc).__name__}")

    def _probe(self, conn: Connection) -> dict:
        while True:
            conn.step = "Checking the host"
            done = self._call(self._ssh(conn, "-T", "-o", "ClearAllForwardings=yes", command=probe_command()), PROBE_TIMEOUT)
            if done.returncode == 0:
                return parse_probe(done.stdout.decode("utf-8", "replace"))
            kind, text = explain(done.stderr.decode("utf-8", "replace"), conn.host)
            if kind != "host_key_unknown" or conn.trusted:
                conn.kind = kind
                raise SSHError(text)
            self._ask_host_key(conn)

    def _ask_host_key(self, conn: Connection) -> None:
        conn.step = "Reading the host key"
        with tempfile.TemporaryDirectory(prefix="dikdik-ssh-") as folder:
            scratch, empty = Path(folder) / "known_hosts", Path(folder) / "empty"
            empty.write_text("", encoding="utf-8")
            self._call(self._ssh(conn, "-T", *NO_AUTH, "-o", "ClearAllForwardings=yes",
                                 "-o", f"UserKnownHostsFile={ssh_path(scratch)}",
                                 "-o", f"GlobalKnownHostsFile={ssh_path(empty)}", command="true", strict="accept-new"),
                       PROBE_TIMEOUT)
            conn.scratch = scratch.read_text(encoding="utf-8") if scratch.is_file() else ""
        conn.keys = fingerprints(conn.scratch)
        if not conn.keys:
            conn.kind = "host_key_unknown"
            raise SSHError(f"Could not read the host key of {conn.host}. If it connects through a jump host, "
                           "connect to that host once in a terminal to verify its key, then try again.")
        conn.state, conn.step = "host_key", "Waiting for you to check the host key"
        conn.decision.clear()
        if not conn.decision.wait(DECISION_TIMEOUT) or conn.closing or not conn.trusted:
            conn.closing = True
            conn.state, conn.step = "closed", "Host key not accepted"
            raise SSHError("Host key not accepted")
        self._save_host_key(conn)
        conn.state, conn.step = "connecting", "Host key saved"

    def _save_host_key(self, conn: Connection) -> None:
        target = Path.home() / ".ssh" / "known_hosts"
        try:
            done = self._call([*self.command(), "-G", "--", conn.host], 15)
            for line in done.stdout.decode("utf-8", "replace").splitlines():
                key, _, value = line.partition(" ")
                if key.lower() == "userknownhostsfile" and value.split():
                    first = value.split()[0]
                    if "%" not in first:
                        target = Path(os.path.expanduser(first))
                    break
        except SSHError:
            pass
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        lines = [line for line in conn.scratch.splitlines() if line.strip()]
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write("\n".join(lines) + "\n")
        logs.log("ssh_host_key_saved", host=conn.host, keys=len(lines))

    def _bootstrap(self, conn: Connection) -> None:
        found = self._probe(conn)
        if found["os"] is None and not found["pythons"]:
            conn.kind = "unsupported"
            raise SSHError(f"{conn.host} did not answer like a Linux or macOS shell. Connect to host supports Linux and macOS hosts for now.")
        python = found["python"]
        if python is None:
            seen = ", ".join("{}.{}.{}".format(*p["version"]) for p in found["pythons"])
            conn.kind = "python"
            raise SSHError(f"{conn.host} has " + (f"Python {seen}" if seen else "no Python")
                           + ". Dikdik needs Python 3.13 or newer on the host: install it (for example with your package "
                             "manager, pyenv or uv) so that python3.13 or python3 is on the PATH, then connect again.")
        conn.python = "{}.{}.{}".format(*python["version"])
        archive, stamp = package_archive()
        if found["stamp"] != stamp:
            conn.step = f"Installing Dikdik {__version__} on the host"
            done = self._call(self._ssh(conn, "-T", "-o", "ClearAllForwardings=yes",
                                        command=remote_command(python["path"], INSTALL, VERSION, stamp)),
                              INSTALL_TIMEOUT, data=archive)
            if done.returncode != 0 or b"DIKDIK-INSTALLED" not in done.stdout:
                conn.kind = "install"
                raise SSHError("Installing Dikdik on the host failed: " + last_line(done.stderr))
        if conn.repo:
            self._clone(conn, python["path"])
        self._start(conn, python["path"])

    def _clone(self, conn: Connection, python: str) -> None:
        name = conn.folder.rsplit("/", 1)[-1]
        conn.step = f"Cloning {name} on the host"
        done = self._call(self._ssh(conn, "-T", "-o", "ClearAllForwardings=yes",
                                    command=remote_command(python, CLONE, conn.repo, conn.parent, name)), CLONE_TIMEOUT)
        if done.returncode == 0 and b"DIKDIK-CLONED" in done.stdout:
            return
        conn.kind = "clone"
        if b"DIKDIK-NO-GIT" in done.stdout:
            raise SSHError(f"Git is not installed on {conn.host}. Install it there, then connect again.")
        if b"DIKDIK-CLONE-EXISTS" in done.stdout:
            raise SSHError(f"{conn.folder} already exists on {conn.host} and is not a clone of that repository. "
                           "Choose another Clone into folder, or open that folder instead.")
        detail = done.stderr.decode("utf-8", "replace")
        if re.search(r"Host key verification failed|Permission denied \(publickey|could not read Username|Authentication failed|"
                     r"Repository not found|terminal prompts disabled", detail, re.I):
            raise SSHError(f"{conn.host} could not sign in to that repository. Git there uses the host's own sign-in: "
                           "run `gh auth login` on the host or add its SSH key to your Git provider (and connect to the Git "
                           "server once from the host to verify its key), or use the https URL for a public repository.")
        raise SSHError(f"Cloning on {conn.host} failed: " + last_line(done.stderr))

    def _start(self, conn: Connection, python: str) -> None:
        conn.step = "Starting Dikdik on the host"
        port, ipv6 = free_port()
        conn.port = port
        label = conn.host.removeprefix("ssh://")
        run = self._ssh(conn, "-T", "-o", "ClearAllForwardings=yes",
                        command=remote_command(python, RUN, VERSION, conn.folder, "--port", "0", "--no-browser",
                                               "--ssh-session", label, "--forwarded-port", str(port)))
        try:
            process = subprocess.Popen(run, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise SSHError(f"Could not start ssh: {exc.strerror or exc}") from exc
        conn.procs["run"] = process
        errors: list[str] = []
        threading.Thread(target=collect, args=(process.stderr, errors), daemon=True).start()
        ready: dict = {}
        arrived = threading.Event()

        def read_stdout() -> None:
            with process.stdout:
                for raw in process.stdout:
                    line = raw.decode("utf-8", "replace").strip()
                    if not ready and line.startswith(READY + " "):
                        try:
                            value = json.loads(line[len(READY) + 1:])
                            ready.update(port=int(value["port"]), token=str(value["token"]))
                            logs.register_secret(ready["token"])
                        except (ValueError, KeyError, TypeError):
                            pass
                        arrived.set()
            arrived.set()
        threading.Thread(target=read_stdout, daemon=True).start()
        if not arrived.wait(READY_TIMEOUT) or not ready:
            if conn.closing:
                raise SSHError("Disconnected")
            time.sleep(0.2)  # let stderr arrive after a quick exit
            detail = "\n".join(errors)
            kind, text = explain(detail, conn.host)
            if kind == "failed":
                said = re.findall(r"error: (.+)", detail)
                text = ("Dikdik could not start on the host: " + logs.redact(said[-1])[:300]) if said else \
                       ("Dikdik could not start on the host: " + last_line(detail.encode()))
                kind = "start"
            conn.kind = kind
            raise SSHError(text)
        conn.token = ready["token"]
        self._tunnel(conn, ready["port"], ipv6)

    def _tunnel(self, conn: Connection, remote_port: int, ipv6: bool) -> None:
        conn.step = "Opening the tunnel"
        forwards = ["-L", f"127.0.0.1:{conn.port}:127.0.0.1:{remote_port}"]
        if ipv6:
            forwards += ["-L", f"[::1]:{conn.port}:127.0.0.1:{remote_port}"]
        try:
            tunnel = subprocess.Popen(self._ssh(conn, "-N", "-o", "ExitOnForwardFailure=yes", *forwards),
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as exc:
            raise SSHError(f"Could not start ssh: {exc.strerror or exc}") from exc
        conn.procs["tunnel"] = tunnel
        errors: list[str] = []
        threading.Thread(target=collect, args=(tunnel.stderr, errors), daemon=True).start()
        deadline = time.monotonic() + TUNNEL_TIMEOUT
        while not healthy(conn.port):
            if conn.closing:
                raise SSHError("Disconnected")
            if tunnel.poll() is not None or time.monotonic() > deadline:
                time.sleep(0.2)
                conn.kind = "tunnel"
                raise SSHError("The SSH tunnel did not open: " + last_line("\n".join(errors).encode()))
            time.sleep(0.25)
        conn.state, conn.step = "connected", "Connected"
        logs.log("ssh_connected", host=conn.host)
        threading.Thread(target=self._watch, args=(conn,), daemon=True).start()

    def _watch(self, conn: Connection) -> None:
        run, tunnel = conn.procs["run"], conn.procs["tunnel"]
        while run.poll() is None and tunnel.poll() is None:
            time.sleep(0.5)
        if conn.closing:
            return
        conn.closing = True
        ended_on_host = run.poll() == 0  # Disconnect in the remote window stops the host process cleanly
        self._stop(conn)
        if ended_on_host:
            conn.state, conn.step = "closed", "Disconnected"
        else:
            self._fail(conn, "lost", f"The connection to {conn.host} was lost.")
        logs.log("ssh_disconnected", host=conn.host, lost=not ended_on_host)

    def _stop(self, conn: Connection) -> None:
        conn.token = None
        for process in list(conn.procs.values()):
            try:
                if process.stdin:
                    process.stdin.close()  # the host process ends when its stdin closes
                    process.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()


def ssh_path(path: Path) -> str:
    """A path for an ssh -o value: forward slashes, quoted, and % kept literal."""
    return '"' + path.as_posix().replace("%", "%%") + '"'


def collect(stream, sink: list[str]) -> None:
    with stream:
        for raw in stream:
            sink.append(raw.decode("utf-8", "replace").rstrip())
            del sink[:-40]


def last_line(raw: bytes) -> str:
    lines = [line.strip() for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
    return logs.redact(lines[-1])[:300] if lines else "no details"


def healthy(port: int) -> bool:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/health", headers={"Host": f"localhost:{port}"})
        ok = conn.getresponse().status == 200
        conn.close()
        return ok
    except (OSError, http.client.HTTPException):
        return False
