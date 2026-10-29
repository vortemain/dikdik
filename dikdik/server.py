"""Loopback-only authenticated HTTP interface. No repository code runs on host."""

import argparse
import errno as errno_module
import hmac
import html
import importlib.util
import json
import os
import platform
import secrets
import select
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .engine import ApplyConflict, Engine, TERMINAL
from . import __version__ as DIKDIK_VERSION
from .control import IMAGE_TYPES, workspace_image, workspace_images
from . import gitops
from .repository import INDEXER, approved_root, inventory, metadata, workspace_metadata
from .server_routes import ROUTE_PREFIXES, build_routes
from .gateway import Gateway
from .openapi import document as openapi_document
from .server_state import ServerState
from .api_keys import ApiKeyStore
from .html_preview import HTMLPreviews, PREVIEW_CSP
from .config import check_config_or_exit
from .projects import Projects, check_location, pick_folder, list_folders
from . import remote as remote_mode
from . import git_accounts


WEB = Path(__file__).parent / "web"
M4_PREFIXES = ROUTE_PREFIXES
FONTS = ("inter-latin-wght-normal", "inter-latin-ext-wght-normal", "jetbrains-mono-latin-wght-normal", "jetbrains-mono-latin-ext-wght-normal")
# An explicit allowlist: nothing else under web/ (or outside it) is ever served.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/workbench.css": ("workbench.css", "text/css; charset=utf-8"),
    "/model-settings.css": ("model-settings.css", "text/css; charset=utf-8"),
    "/dialogs.css": ("dialogs.css", "text/css; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    **{f"/fonts/{name}.woff2": (f"fonts/{name}.woff2", "font/woff2") for name in FONTS},
    **{f"/assets/{path.relative_to(WEB / 'assets').as_posix()}":
       (path.relative_to(WEB).as_posix(), "image/svg+xml")
       for path in (WEB / "assets").glob("*/*.svg") if path.is_file() and not path.is_symlink()},
}
SESSION_AGE = 30 * 24 * 3600
PAGE_CSP = ("default-src 'self'; style-src 'self'; script-src 'self'; font-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
# An extension app is untrusted page content. `sandbox` gives it an opaque origin even
# when opened directly, so it never shares Dikdik's origin, cookie or API; it may run its
# own inline script but cannot load or send anything, and only Dikdik may frame it.
APP_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
           "img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'")
CORS_ORIGINS = [o.strip() for o in os.environ.get("DIKDIK_CORS_ORIGINS", "").split(",") if o.strip()]


def session_token(path: Path, rotate: bool = False) -> str:
    """Load the browser session secret from the private data directory, creating it once.

    Persisting it means an open tab survives a server restart, so the browser never
    has to be handed a token again. `--rotate-token` invalidates every session.
    """
    if not rotate:
        try:
            value = path.read_text(encoding="utf-8").strip()
            if len(value) >= 32:
                return value
        except FileNotFoundError:
            pass
    value = secrets.token_urlsafe(32)
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
    return value


def workspace_path(root: Path, rel, write: bool = False) -> Path:
    """Resolve a user-supplied relative path inside the workspace. Writes may not
    target the workspace root itself or anything under .git (hooks, config, objects)."""
    if not isinstance(rel, str) or not rel.strip() or len(rel) > 500 or rel.startswith("-"):
        raise ValueError("path must be a safe relative path")
    base = root.resolve()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise ValueError("path must stay inside the workspace")
    if write and (target == base or target.relative_to(base).parts[0].lower() == ".git"):
        raise ValueError("path must name a file or folder inside the workspace, outside .git")
    return target


class _Live:
    """Stands in for whichever object `holder.<name>` currently is. The handler below
    says `engine`, `routes` and `gateway` throughout; when the user switches project,
    those names follow the new project's objects without any route changing."""

    def __init__(self, holder, name):
        object.__setattr__(self, "_holder", holder)
        object.__setattr__(self, "_name", name)

    def _target(self):
        return getattr(self._holder, self._name)

    def __getattr__(self, attr):
        return getattr(self._target(), attr)

    def __setattr__(self, attr, value):
        setattr(self._target(), attr, value)

    def __call__(self, *args, **kwargs):
        return self._target()(*args, **kwargs)


class _Wiring:
    """The per-project objects the HTTP handler serves: rebuilt on every project switch."""

    def __init__(self, engine):
        self.port = None
        self.wire(engine)

    def wire(self, engine):
        self.engine = engine
        self.routes = build_routes(engine)  # M4: shared (method, path) routing table
        self.gateway = Gateway(engine)
        engine.gateway = self.gateway  # the gateway routes after authentication in this handler
        if self.port is not None:
            self.routes.oauth.port = self.port


def open_in_editor(root: Path, rel: str, line=None) -> dict:
    """Open a project file (or the project, for ".") in the VS Code on this computer.
    Only paths inside the project; the editor is the user's own `code` command."""
    target = root.resolve() if rel in (".", "") else workspace_path(root, rel)
    if not target.exists():
        raise KeyError(rel)
    editor = shutil.which("code")
    if editor is None:
        raise ValueError("VS Code's `code` command is not on PATH. In VS Code, run “Shell Command: Install 'code' command in PATH”.")
    where = str(target) + (f":{int(line)}" if isinstance(line, int) and not isinstance(line, bool) and line > 0 and target.is_file() else "")
    args = [editor, "--reuse-window", "--goto", where] if target.is_file() else [editor, str(target)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    return {"opened": rel or ".", "editor": "VS Code"}


def make_server(engine: Engine, port: int, token: str, remote: "remote_mode.RemoteConfig | None" = None):
    wiring = _Wiring(engine)
    sign_ins = git_accounts.SignIns()
    projects = Projects(engine, on_switch=wiring.wire, limit=remote.projects_root if remote else None)
    engine, routes, gateway = _Live(wiring, "engine"), _Live(wiring, "routes"), _Live(wiring, "gateway")
    expected = ("Bearer " + token).encode("utf-8")
    started_at = time.monotonic()
    activity = {"last": time.monotonic()}
    server_state = ServerState(ready=not os.environ.get("DIKDIK_DORMANT", ""))
    api_keys = ApiKeyStore(Path(engine.data) if hasattr(engine, "data") else Path("."))
    previews = HTMLPreviews()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never log authorization, objectives or repository contents.

        def reply(self, status, data, mime="application/json", cookie=None, csp=PAGE_CSP):
            body = json.dumps(data).encode() if mime == "application/json" else data
            self.send_response(status)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(body)

        @property
        def cookie_name(self):
            # Cookies are not port-scoped, so each local instance gets its own name.
            return f"dikdik_session_{self.server.server_port}"

        def refuse(self, status, data):
            # Read a small unread body first: on Windows, closing a socket with unread
            # bytes resets it, and the client can lose the refusal before reading it.
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if 0 < length <= 65536:
                self.rfile.read(length)
            else:
                self.close_connection = True
            self.reply(status, data)

        def cors_origin(self):
            """Return the configured CORS origin if the request matches one."""
            if not CORS_ORIGINS:
                return None
            origin = self.headers.get("Origin", "")
            return origin if origin in CORS_ORIGINS else None

        def cors_headers(self):
            origin = self.cors_origin()
            return {"Access-Control-Allow-Origin": origin} if origin else {}

        def same_origin(self):
            host = f"127.0.0.1:{self.server.server_port}"
            hosts, origins = {host}, {"http://" + host}
            if remote:  # on a server: also exactly the public address it was started with
                hosts.add(remote.public_host)
                origins.add(remote.public_origin)
            requested = (self.headers.get("Host") or "").lower()
            origin = (self.headers.get("Origin") or ("https://" if remote and requested == remote.public_host and remote.secure else "http://") + requested).lower()
            if requested not in hosts:
                self.refuse(403, {"error": "Invalid host or origin"})
                return False
            if origin in origins:
                return True
            if CORS_ORIGINS and origin in CORS_ORIGINS:
                return True
            self.refuse(403, {"error": "Invalid host or origin"})
            return False

        def session_cookie(self):
            try:
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get(self.cookie_name)
            except CookieError:
                return ""
            return morsel.value if morsel else ""

        def authorized(self):
            if not self.same_origin():
                return False
            # Scripts and tests send the bearer header; the browser holds an HttpOnly,
            # SameSite=Strict session cookie, so nobody has to paste a token.
            supplied = self.headers.get("Authorization", "") or ("Bearer " + self.session_cookie())
            self.api_key = None
            if not hmac.compare_digest(supplied.encode("utf-8"), expected):
                key = api_keys.verify(supplied.removeprefix("Bearer ")) if supplied.startswith("Bearer dikdik-") else None
                if key is None:
                    self.refuse(401, {"error": "Not signed in. Open the link printed in the Dikdik terminal."})
                    return False
                self.api_key = key
                if not self.key_allows(key, self.command, urlsplit(self.path).path):
                    self.refuse(403, {"error": f"This API key's scopes ({', '.join(key['scopes'])}) do not allow this request"})
                    return False
            if server_state.dormant and urlsplit(self.path).path != "/api/init":
                self.refuse(503, {"error": "Server is dormant; POST /api/init to initialize"})
                return False
            return True

        @staticmethod
        def key_allows(key, method, path):
            """read: GET only; write: anything but key management and init; admin: everything.
            The session token is the owner and is never scope-limited."""
            scopes = set(key["scopes"])
            if "admin" in scopes:
                return True
            if path == "/api/init" or path == "/api/keys" or path.startswith("/api/keys/"):
                return False
            return "write" in scopes or method == "GET"

        def may_write(self):
            key = getattr(self, "api_key", None)
            return key is None or bool({"write", "admin"} & set(key["scopes"]))

        def start_session(self, body):
            supplied = body.get("token") if isinstance(body, dict) else None
            client = self.client_address[0] if self.client_address else "?"
            if remote and not remote.allow_sign_in_attempt(client):
                self.reply(429, {"error": "Too many failed sign-ins; wait ten minutes"})
                return
            if not isinstance(supplied, str) or not hmac.compare_digest(("Bearer " + supplied).encode("utf-8"), expected):
                if remote:
                    remote.record_failure(client)
                self.reply(401, {"error": "That sign-in link is not valid for this server. Use the one printed in the Dikdik terminal."})
                return
            cookie = f"{self.cookie_name}={token}; Path=/; Max-Age={SESSION_AGE}; HttpOnly; SameSite=Strict" + ("; Secure" if remote and remote.secure else "")
            self.reply(200, {"signed_in": True}, cookie=cookie)

        def stream(self, task, after):
            """Server-sent events: each new task event, the reply as it streams, then `end`."""
            state, _ = engine.store.events_after(task, after, 0)  # 404s before any header is sent
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.close_connection = True
            last, live, quiet, deadline = after, None, time.monotonic(), time.monotonic() + 900
            try:
                while time.monotonic() < deadline:
                    state, events = engine.store.events_after(task, last)
                    out = []
                    for event in events:
                        last = event["seq"]
                        out.append(f"id: {last}\nevent: task\ndata: {json.dumps(event)}\n\n")
                    text = engine.live.get(task)
                    if text != live:
                        live = text
                        out.append(f"event: delta\ndata: {json.dumps({'text': text or ''})}\n\n")
                    if not events and state in TERMINAL and not engine.busy(task) and not engine.bash.busy(task) and not engine.browser.busy(task):
                        out.append(f"event: end\ndata: {json.dumps({'state': state})}\n\n")
                    if not out and time.monotonic() - quiet > 15:
                        out.append(": keep-alive\n\n")
                    if out:
                        self.wfile.write("".join(out).encode())
                        self.wfile.flush()
                        quiet = time.monotonic()
                        if out[-1].startswith("event: end"):
                            return
                    time.sleep(0.2)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

        def websocket(self, task, after):
            """Upgrade to a WebSocket that streams task events and accepts follow-ups.
            authorized() has already checked origin, credentials and dormancy."""
            ws_key = self.headers.get("Sec-WebSocket-Key", "")
            if len(ws_key) < 16 or "websocket" not in self.headers.get("Upgrade", "").lower():
                self.reply(400, {"error": "Missing WebSocket upgrade headers"})
                return
            try:
                engine.store.get(task)
            except KeyError:
                self.reply(404, {"error": "Task not found"})
                return
            from .websocket import accept_key, read_frame, send_text, send_close, send_frame, OP_TEXT
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept_key(ws_key))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            sock = self.connection
            sock.settimeout(10)  # bounds a frame the client started but has not finished
            last, live, ended = after, None, False
            quiet, deadline = time.monotonic(), time.monotonic() + 900
            try:
                while time.monotonic() < deadline:
                    state, events = engine.store.events_after(task, last)
                    for event in events:
                        last = event["seq"]
                        send_text(sock, json.dumps({"type": "event", "seq": last, "data": event}))
                    text = engine.live.get(task)
                    if text != live:
                        live = text
                        send_text(sock, json.dumps({"type": "delta", "text": (text or "")[:16384]}))
                    idle = (not events and state in TERMINAL and not engine.busy(task)
                            and not engine.bash.busy(task) and not engine.browser.busy(task))
                    if idle and not ended:
                        send_text(sock, json.dumps({"type": "end", "state": state}))
                    ended = idle
                    if select.select([sock], [], [], 0.5)[0]:
                        frame = read_frame(sock)
                        if frame is None:
                            break  # the client closed the socket
                        if frame[0] == OP_TEXT:
                            self.websocket_message(task, frame[1], sock, send_text)
                            ended = False
                    if time.monotonic() - quiet > 15:
                        send_frame(sock, 0x9, b"")  # ping keeps proxies from closing an idle socket
                        quiet = time.monotonic()
            except (OSError, ValueError):
                pass
            finally:
                send_close(sock)

        def websocket_message(self, task, raw, sock, send_text):
            """A follow-up sent over the socket goes through the same validation as
            POST /api/tasks/<id>/messages; its approvals are whatever the client sent."""
            try:
                message = json.loads(raw.decode("utf-8"))
                if not isinstance(message, dict) or message.get("type") != "message":
                    raise ValueError("Send {\"type\": \"message\", \"objective\": ...}")
                if not self.may_write():
                    raise ValueError("This API key is read-only")
                engine.send(task, {key: value for key, value in message.items() if key != "type"})
                send_text(sock, json.dumps({"type": "queued"}))
            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
                send_text(sock, json.dumps({"type": "error", "error": str(exc)}))

        def oauth_callback(self):
            """The sign-in provider redirects the browser here. The session cookie is
            SameSite=Strict and not sent on that cross-site redirect, so the one-time
            state in the URL is what ties the reply to a sign-in the user started."""
            state = parse_qs(urlsplit(self.path).query).get("state", [""])[0]
            job = routes.oauth.job_for_state(state) if state else None
            if job is None:
                status, text = 400, "This sign-in link is not valid or has already been used."
            else:
                try:
                    status, result = routes.oauth.callback(job, f"http://127.0.0.1:{self.server.server_port}{self.path}")
                    text = "Signed in. You can close this window and return to Dikdik." if result["ok"] else \
                        f"Sign-in was not completed: {result['error'] or result['status']}. You can close this window."
                except ValueError as exc:
                    status, text = 400, str(exc)
            page = ('<!doctype html><meta charset="utf-8"><title>Dikdik sign-in</title>'
                    f'<p style="font:16px system-ui;margin:3em auto;max-width:32em">{html.escape(text)}</p>').encode()
            self.reply(200 if status in (200, 409) else status, page, "text/html; charset=utf-8")

        def do_OPTIONS(self):
            origin = self.cors_origin()
            if not origin:
                self.send_response(403)
                self.end_headers()
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.end_headers()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in STATIC:
                filename, mime = STATIC[path]
                raw = (WEB / filename).read_bytes()
                range_header = self.headers.get("Range")
                if range_header and range_header.startswith("bytes="):
                    try:
                        spec = range_header[6:].split("-")
                        start = int(spec[0]) if spec[0] else 0
                        end = int(spec[1]) if len(spec) > 1 and spec[1] else len(raw) - 1
                        end = min(end, len(raw) - 1)
                        if start > end or start >= len(raw):
                            raise ValueError
                        self.send_response(206)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(end - start + 1))
                        self.send_header("Content-Range", f"bytes {start}-{end}/{len(raw)}")
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(raw[start:end + 1])
                    except (ValueError, IndexError):
                        self.send_response(416)
                        self.end_headers()
                else:
                    self.reply(200, raw, mime)
                return
            if path == "/api/mcp/oauth/callback":
                if self.same_origin():
                    self.oauth_callback()
                return
            if path == "/alive" or path == "/health":
                self.reply(200, {"status": "ok"})
                return
            if path == "/ready":
                if server_state.dormant:
                    self.reply(503, {"status": "dormant", "error": "Server is not initialized; POST /api/init first"})
                else:
                    self.reply(200, {"status": "ready"})
                return
            if path == "/server_info" or path == "/api/server-info":
                now = time.monotonic()
                self.reply(200, {"version": DIKDIK_VERSION, "python": platform.python_version(),
                                 "platform": platform.platform(), "workspace": engine.root.name,
                                 "rust_indexer": INDEXER, "port": self.server.server_port,
                                 "uptime": round(now - started_at, 3),
                                 "idle_time": round(now - activity["last"], 3),
                                 **server_state.info()})
                return
            if path == "/openapi.json" or path == "/api/openapi":
                self.reply(200, openapi_document())
                return
            if not self.authorized():
                return
            answer = gateway.route("GET", path, {}, dict(self.headers))
            if answer is not None:
                self.reply(*answer)
                return
            if path.startswith(M4_PREFIXES):
                self.reply(*routes("GET", self.path, {}))
                return
            try:
                planning = engine.planning.route('GET', self.path, {})
                if planning is not None:
                    self.reply(*planning)
                    return
                parts = path.split("/")
                if len(parts) == 4 and parts[:3] == ["", "api", "previews"]:
                    try:
                        self.reply(200, previews.get(parts[3], str(engine.root)), "text/html; charset=utf-8", csp=PREVIEW_CSP)
                    except KeyError:
                        self.reply(404, {"error": "Preview expired or belongs to another project; open it again from the response"})
                    return
                if len(parts) == 4 and parts[:3] == ["", "api", "attachments"]:
                    meta, raw = engine.attachments.get(parts[3])
                    self.reply(200, raw, meta["type"] if meta["type"] in IMAGE_TYPES else "application/octet-stream")
                    return
                if path == "/api/settings/app":
                    self.reply(200, engine.app_settings())
                    return
                if path == "/api/settings/schema":
                    self.reply(200, {
                        "properties": {
                            "confirmation": {"type": "string", "enum": ["never", "risky", "always"]},
                            "condenser": {"type": "boolean"},
                            "git_name": {"type": "string", "maxLength": 100},
                            "git_email": {"type": "string", "maxLength": 200},
                            "sounds": {"type": "boolean"},
                            "router": {"type": "string"},
                            "title_model": {"type": "string"},
                            "event_retention_seconds": {"type": "integer", "minimum": 0, "maximum": 86400 * 30},
                        },
                        "defaults": {k: v for k, v in engine.app_settings().items()},
                    })
                    return
                if path == "/api/workspaces":
                    self.reply(200, {"workspaces": engine.workspaces.list()})
                    return
                if path == "/api/chatgpt/status":
                    self.reply(200, engine.chatgpt.status())
                    return
                if path == "/api/apps":
                    self.reply(200, {"apps": engine.apps.list()})
                    return
                if path == "/api/worktrees":
                    self.reply(200, {"worktrees": engine.worktrees.list()})
                    return
                if path == "/api/vscode":
                    import shutil as _sh
                    available = _sh.which("code-server") is not None
                    self.reply(200, {"available": available, "command": "code-server",
                                     "workspace": str(engine.root)})
                    return
                if path.startswith("/api/apps/") and path.endswith("/bundle") and path.count("/") == 4:
                    app = engine.apps.get(path.split("/")[3])
                    if not app["enabled"]:
                        self.reply(403, {"error": "Enable this app before opening it"})
                        return
                    raw, mime = engine.apps.bundle(app["name"], app["entrypoint"])
                    self.reply(200, raw, mime, csp=APP_CSP)
                    return
                if path == "/api/keys":
                    self.reply(200, {"keys": api_keys.list()})
                    return
                if path == "/api/projects/folders":
                    if remote:
                        raise ValueError("Folder browsing is available only when Dikdik runs on this computer; type the server folder path instead")
                    query = parse_qs(urlsplit(self.path).query)
                    self.reply(200, list_folders(query.get("path", [str(engine.root.parent)])[0]))
                    return
                if path == "/api/projects":
                    self.reply(200, projects.describe())
                    return
                if path == "/api/commands/allowed":
                    self.reply(200, {"commands": engine.allowed_commands()})
                    return
                if path == "/api/tasks/count":
                    mine, mine_params = engine.store._mine()
                    with engine.store.connect() as db:
                        total = db.execute(f"SELECT COUNT(*) FROM tasks WHERE {mine}", mine_params).fetchone()[0]
                        by_state = {row["state"]: row["count"] for row in
                                    db.execute(f"SELECT state, COUNT(*) AS count FROM tasks WHERE {mine} GROUP BY state", mine_params)}
                    self.reply(200, {"total": total, "by_state": by_state})
                    return
                if path == "/api/tasks/search":
                    query = parse_qs(urlsplit(self.path).query)
                    q = query.get("q", [""])[0]
                    state_filter = query.get("state", [""])[0]
                    with engine.store.connect() as db:
                        sql = "SELECT id,created,state,request,title,pinned,archived,folder,tags FROM tasks"
                        mine, mine_params = engine.store._mine()
                        params: list = [*mine_params]
                        conditions = [mine]
                        if q:
                            conditions.append("title LIKE ?")
                            params.append(f"%{q}%")
                        if state_filter:
                            conditions.append("state=?")
                            params.append(state_filter)
                        sql += " WHERE " + " AND ".join(conditions)
                        sql += " ORDER BY pinned DESC, created DESC LIMIT 100"
                        rows = db.execute(sql, params).fetchall()
                    tasks = [{"id": r["id"], "created": r["created"], "state": r["state"],
                              "title": r["title"] or "", "pinned": bool(r["pinned"]),
                              "archived": bool(r["archived"]), "folder": r["folder"] or "",
                              "tags": json.loads(r["tags"]) if r["tags"] else []} for r in rows]
                    self.reply(200, {"tasks": tasks, "total": len(tasks)})
                    return
                if len(parts) == 7 and parts[:3] == ["", "api", "tasks"] and parts[4:6] == ["browser", "screenshots"]:
                    self.reply(200, engine.browser.screenshot(parts[3], parts[6]), "image/png")
                    return
                browser = engine.browser.route("GET", self.path)
                if browser is not None:
                    self.reply(*browser)
                    return
                bash = engine.bash.route("GET", self.path, {})
                if bash is not None:
                    self.reply(*bash)
                elif path == "/api/project":
                    self.reply(200, {"name": engine.root.name, "path": str(engine.root), "trusted": engine.trusted(), "indexer": INDEXER,
                                     **workspace_metadata(engine.root), "images": workspace_images(engine.root)})
                elif path == "/api/files/image":
                    kind, raw = workspace_image(engine.root, parse_qs(urlsplit(self.path).query).get("path", [""])[0])
                    self.reply(200, raw, kind)
                elif path == "/api/tasks":
                    self.reply(200, engine.store.list())
                elif path == "/api/events" or path == "/api/events/batch":
                    query = parse_qs(urlsplit(self.path).query)
                    limit = int(query.get("limit", ["100"])[0]) if query.get("limit", ["100"])[0].isdigit() else 100
                    self.reply(200, {"events": engine.store.recent_events(limit)})
                elif path.startswith("/api/tasks/") and path.endswith("/stream") and path.count("/") == 4:
                    after = parse_qs(urlsplit(self.path).query).get("after", ["0"])[0]
                    self.stream(path.split("/")[3], int(after) if after.isdigit() else 0)
                elif path.startswith("/api/tasks/") and path.endswith("/ws") and path.count("/") == 4:
                    after = parse_qs(urlsplit(self.path).query).get("after", ["0"])[0]
                    self.websocket(path.split("/")[3], int(after) if after.isdigit() else 0)
                elif path.startswith("/api/tasks/") and path.endswith("/events") and path.count("/") == 4:
                    query = parse_qs(urlsplit(self.path).query)
                    kind = query.get("kind", [None])[0]
                    limit = int(query.get("limit", ["200"])[0]) if query.get("limit", ["200"])[0].isdigit() else 200
                    state, events = engine.store.search_events(path.split("/")[3], kind, min(limit, 2000))
                    self.reply(200, {"state": state, "events": events})
                elif path.startswith("/api/tasks/") and path.endswith("/events/count") and path.count("/") == 5:
                    state, counts = engine.store.count_events(path.split("/")[3])
                    self.reply(200, {"state": state, **counts})
                elif path == "/api/model":
                    profile = parse_qs(urlsplit(self.path).query).get("profile", [None])[0]
                    self.reply(200, engine.models.describe(profile))
                elif path == "/api/files/content":
                    query = parse_qs(urlsplit(self.path).query)
                    self.reply(200, engine.file_text(query.get("path", [""])[0], query.get("task", [None])[0]))
                elif path == "/api/files/download":
                    query = parse_qs(urlsplit(self.path).query)
                    rel = query.get("path", [""])[0]
                    target = workspace_path(engine.root, rel)
                    if not target.is_file():
                        raise KeyError(rel)
                    raw = target.read_bytes()
                    self.reply(200, raw, "application/octet-stream")
                elif path == "/api/files/list":
                    query = parse_qs(urlsplit(self.path).query)
                    rel = query.get("path", [""])[0] or "."
                    target = workspace_path(engine.root, rel)
                    if not target.is_dir():
                        raise KeyError(rel)
                    entries = []
                    for child in sorted(target.iterdir(), key=lambda p: p.name):
                        if child.name.startswith("."):
                            continue
                        entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file",
                                         "size": child.stat().st_size if child.is_file() else None})
                    self.reply(200, {"path": rel, "entries": entries})
                elif path == "/api/files/home":
                    import os as _os
                    self.reply(200, {"home": str(Path(_os.path.expanduser("~"))),
                                     "workspace": str(engine.root)})
                elif path == "/api/files/archive":
                    import io as _io, tarfile, gzip
                    query = parse_qs(urlsplit(self.path).query)
                    rel = query.get("path", [""])[0] or "."
                    target = workspace_path(engine.root, rel)
                    if not target.is_dir():
                        raise KeyError(rel)
                    buffer = _io.BytesIO()
                    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
                        for item in sorted(target.rglob("*"), key=lambda p: str(p.relative_to(target))):
                            if item.is_file() and not any(part.startswith(".") for part in item.relative_to(target).parts):
                                tar.add(str(item), arcname=str(item.relative_to(engine.root)))
                    raw = buffer.getvalue()
                    self.reply(200, raw, "application/gzip")
                elif path == "/api/git":
                    self.reply(200, gitops.info(engine.root))
                elif path.startswith("/api/git/") and path.endswith("/diff") and path.count("/") == 4:
                    self.reply(200, gitops.diff(engine.root, path.split("/")[3]))
                elif path == "/api/git/accounts":
                    self.reply(200, git_accounts.status())
                elif path.startswith("/api/git/accounts/") and path.endswith("/login") and path.count("/") == 5:
                    self.reply(200, sign_ins.poll(path.split("/")[4]))
                elif path == "/api/git/repos":
                    query = parse_qs(urlsplit(self.path).query)
                    self.reply(200, {"repos": git_accounts.repositories(query.get("provider", ["github"])[0], query.get("q", [""])[0])})
                elif path == "/api/git/remote":
                    self.reply(200, gitops.remote_info(engine.root))
                elif path == "/api/git/search":
                    query = parse_qs(urlsplit(self.path).query)
                    q = query.get("q", [""])[0]
                    lim = int(query.get("limit", ["50"])[0]) if query.get("limit", ["50"])[0].isdigit() else 50
                    self.reply(200, {"query": q, "results": gitops.search(engine.root, q, lim)})
                elif path == "/api/agent-info":
                    self.reply(200, engine.agent_info())
                elif path.startswith("/api/tasks/") and path.endswith("/download") and path.count("/") == 4:
                    self.reply(200, engine.archive(path.split("/")[3]), "application/zip")
                elif path.startswith("/api/tasks/") and path.endswith("/diff") and path.count("/") == 4:
                    self.reply(200, engine.full_diff(path.split("/")[3]), "text/plain; charset=utf-8")
                elif path.startswith("/api/tasks/") and path.endswith("/export") and path.count("/") == 4:
                    self.reply(200, engine.store.get(path.split("/")[3]))
                elif path.startswith("/api/tasks/"):
                    self.reply(200, engine.store.get(path.removeprefix("/api/tasks/")))
                else:
                    self.reply(404, {"error": "Not found"})
            except KeyError:
                self.reply(404, {"error": "Task not found"})
            except ValueError as exc:
                self.reply(400, {"error": str(exc)})
            except OSError:
                self.reply(500, {"error": "Repository or state unavailable"})

        def do_POST(self):
            self.connection.settimeout(10)
            path = urlsplit(self.path).path
            if path == "/api/session":
                if self.same_origin():
                    self.session_request()
                return
            if not self.authorized():
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Chunked requests unsupported")
                length = int(self.headers.get("Content-Length", "0"))
                limit = 4_400_000 if path == "/api/attachments" else 262144  # 3 MB of files, base64-encoded
                if length > limit:
                    raise ValueError(f"Request must contain at most {limit} bytes")
                body = json.loads(self.rfile.read(length)) if length else {}
                if path == '/api/previews':
                    if not isinstance(body, dict) or set(body) != {'html'}:
                        raise ValueError('Preview expects an HTML string')
                    self.reply(201, previews.create(body['html'], str(engine.root)))
                    return
                answer = gateway.route(self.command, path, body, dict(self.headers))
                if answer is not None:
                    self.reply(*answer)
                    return
                if path.startswith(M4_PREFIXES):
                    if length > 65536:
                        self.reply(400, {"error": "Endpoint body limit is 64KB"})
                    else:
                        self.reply(*routes(self.command, self.path, body))
                    return
                planning = engine.planning.route('POST', self.path, body)
                if planning is not None:
                    self.reply(*planning)
                    return
                browser = engine.browser.route("POST", self.path, body)
                if browser is not None:
                    self.reply(*browser)
                    return
                bash = engine.bash.route("POST", self.path, body)
                if bash is not None:
                    self.reply(*bash)
                elif path == "/api/init":
                    self.reply(200, server_state.initialize(body, engine))
                elif path == "/api/projects/open" and isinstance(body, dict):
                    self.reply(200, projects.open(body.get("path")))
                elif path == "/api/projects/trust" and isinstance(body, dict):
                    self.reply(200, {"trusted": engine.set_trusted(body.get("trusted"))})
                elif path == "/api/projects/new":
                    self.reply(201, projects.create(body))
                elif path == "/api/projects/clone":
                    self.reply(201, projects.clone(body))
                elif path in {"/api/projects/pick", "/api/files/open-in-editor"} and remote:
                    raise ValueError("Not available when Dikdik runs on a server; type the folder path instead")
                elif path == "/api/projects/pick":
                    self.reply(200, {"path": pick_folder(str(body.get("title") or "Choose a project folder")[:80]
                                                         if isinstance(body, dict) else "Choose a project folder")})
                elif path == "/api/tasks":
                    self.reply(202, {"id": engine.submit(body)})
                elif path == "/api/tasks/batch" and isinstance(body, dict):
                    action = body.get("action")
                    ids = body.get("ids")
                    if action not in {"archive", "unarchive", "pin", "unpin", "delete"}:
                        raise ValueError("action must be archive, unarchive, pin, unpin or delete")
                    if not isinstance(ids, list) or not ids or len(ids) > 100:
                        raise ValueError("ids must be a list of 1-100 task ids")
                    count = 0
                    for task_id in ids:
                        if not isinstance(task_id, str):
                            raise ValueError("ids must be strings")
                        try:
                            if action == "archive":
                                engine.store.update_meta(task_id, {"archived": True})
                            elif action == "unarchive":
                                engine.store.update_meta(task_id, {"archived": False})
                            elif action == "pin":
                                engine.store.update_meta(task_id, {"pinned": True})
                            elif action == "unpin":
                                engine.store.update_meta(task_id, {"pinned": False})
                            elif action == "delete":
                                engine.delete(task_id)
                            count += 1
                        except KeyError:
                            pass  # skip unknown tasks
                    self.reply(200, {"action": action, "updated": count})
                elif path == "/api/workspaces" and isinstance(body, dict):
                    self.reply(201, engine.workspaces.add(body.get("path", ""), body.get("name")))
                elif path == "/api/keys" and isinstance(body, dict):
                    name = body.get("name", "")
                    scopes = body.get("scopes", ["read"])
                    try:
                        token = api_keys.create(name, scopes)
                        self.reply(201, {"token": token, "scopes": scopes})
                    except ValueError as exc:
                        self.reply(400, {"error": str(exc)})
                elif path == "/api/chatgpt/device/start":
                    try:
                        self.reply(200, engine.chatgpt.device_start())
                    except ValueError as exc:
                        self.reply(400, {"error": str(exc)})
                elif path == "/api/chatgpt/device/poll":
                    try:
                        self.reply(200, engine.chatgpt.device_poll(body))
                    except KeyError:
                        self.reply(404, {"error": "Device login not found or expired"})
                    except ValueError as exc:
                        self.reply(400, {"error": str(exc)})
                elif path == "/api/chatgpt/logout":
                    self.reply(200, engine.chatgpt.logout())
                elif path == "/api/apps" and isinstance(body, dict):
                    try:
                        self.reply(201, engine.apps.install(body))
                    except ValueError as exc:
                        self.reply(400, {"error": str(exc)})
                elif path == "/api/worktrees" and isinstance(body, dict):
                    task_id = body.get("task_id", "")
                    branch = body.get("branch")
                    if not isinstance(task_id, str) or not task_id.strip():
                        self.reply(400, {"error": "task_id is required"})
                        return
                    try:
                        wt_path = engine.worktrees.create(task_id, branch)
                        self.reply(201, {"task": task_id, "path": str(wt_path)})
                    except ValueError as exc:
                        self.reply(400, {"error": str(exc)})
                elif path.startswith("/api/apps/") and path.endswith("/enable") and path.count("/") == 4:
                    app_name = path.split("/")[3]
                    try:
                        self.reply(200, engine.apps.update(app_name, {"enabled": True}))
                    except KeyError:
                        self.reply(404, {"error": "App not found"})
                elif path.startswith("/api/apps/") and path.endswith("/disable") and path.count("/") == 4:
                    app_name = path.split("/")[3]
                    try:
                        self.reply(200, engine.apps.update(app_name, {"enabled": False}))
                    except KeyError:
                        self.reply(404, {"error": "App not found"})
                elif path.startswith("/api/keys/") and path.endswith("/rotate"):
                    try:
                        token = api_keys.rotate(path.split("/")[3])
                        self.reply(200, {"token": token})
                    except KeyError:
                        self.reply(404, {"error": "Key not found"})
                elif path.startswith("/api/tasks/") and path.endswith("/cancel"):
                    self.reply(200, {"requested": engine.cancel(path.split("/")[3])})
                elif path.startswith("/api/tasks/") and path.endswith("/interrupt"):
                    self.reply(200, {"requested": engine.interrupt(path.split("/")[3])})
                elif not isinstance(body, dict):
                    raise ValueError("Body must be a JSON object")
                elif path.startswith("/api/tasks/") and path.endswith("/apply") and path.count("/") == 4:
                    if body.get("confirm") is not True:
                        raise ValueError("Applying changes needs explicit confirmation")
                    self.reply(200, engine.apply(path.split("/")[3], body.get("files")))
                elif path.startswith("/api/tasks/") and path.endswith("/undo") and path.count("/") == 4:
                    self.reply(200, engine.undo(path.split("/")[3]))
                elif path == "/api/commands/allowed/forget":
                    self.reply(200, {"commands": engine.forget_command(str(body.get("command") or ""))})
                elif path.startswith("/api/git/accounts/") and path.endswith("/login") and path.count("/") == 5:
                    if remote:
                        raise ValueError("Sign in on the server itself (run gh auth login there)")
                    self.reply(200, sign_ins.start(path.split("/")[4]))
                elif path == "/api/git/accounts/github/setup-git":
                    self.reply(200, sign_ins.setup_git())
                elif path == "/api/git/fetch":
                    self.reply(200, gitops.fetch(engine.root))
                elif path == "/api/git/pull":
                    self.reply(200, gitops.pull(engine.root))
                elif path == "/api/git/push":
                    self.reply(200, gitops.push(engine.root, body.get("approve_push")))
                elif path == "/api/git/branch":
                    self.reply(200, gitops.switch_branch(engine.root, body.get("name"), body.get("create") is True))
                elif path == "/api/git/pr":
                    self.reply(200, gitops.create_pr(engine.root, body))
                elif path == "/api/git/commit":
                    activity["last"] = time.monotonic()
                    self.reply(200, gitops.commit(engine.root, body.get("message"), body.get("files"), engine.git_identity()))
                elif path == "/api/files/open-in-editor":
                    self.reply(200, open_in_editor(engine.root, str(body.get("path") or "."), body.get("line")))
                elif path == "/api/files/upload" and isinstance(body, dict):
                    activity["last"] = time.monotonic()
                    rel = body.get("path")
                    content = body.get("content")
                    target = workspace_path(engine.root, rel, write=True)
                    if not isinstance(content, str) or len(content) > 1_000_000:
                        raise ValueError("content must be text under 1MB")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    self.reply(201, {"path": rel, "bytes": len(content.encode("utf-8"))})
                elif path == "/api/files/mkdir" and isinstance(body, dict):
                    activity["last"] = time.monotonic()
                    rel = body.get("path")
                    workspace_path(engine.root, rel, write=True).mkdir(parents=True, exist_ok=True)
                    self.reply(201, {"path": rel, "created": True})
                elif path == "/api/files/delete" and isinstance(body, dict):
                    activity["last"] = time.monotonic()
                    rel = body.get("path")
                    target = workspace_path(engine.root, rel, write=True)
                    if not target.exists():
                        raise KeyError(rel)
                    if target.is_dir():
                        import shutil as _shutil
                        _shutil.rmtree(target)
                    else:
                        target.unlink()
                    self.reply(200, {"deleted": rel})
                elif path.startswith("/api/tasks/") and path.endswith("/messages") and path.count("/") == 4:
                    task = path.split("/")[3]
                    self.reply(202, {"id": task, "queued": engine.send(task, body)})
                elif path == "/api/attachments":
                    self.reply(201, engine.attachments.add(body))
                elif path.startswith("/api/tasks/") and path.count("/") in (4, 5):
                    task, action = path.split("/")[3], "/".join(path.split("/")[4:])
                    if action == "pause":
                        self.reply(200, {"requested": engine.pause(task)})
                    elif action == "resume":
                        self.reply(202, {"resumed": engine.resume(task, body)})
                    elif action == "confirm":
                        self.reply(200, {"decided": engine.confirm(task, body)})
                    elif action == "ask":
                        self.reply(200, engine.ask(task, body))
                    elif action == "condense":
                        self.reply(200, engine.condense(task, body.get("approve_external")))
                    elif action == "fork":
                        self.reply(201, engine.fork(task, body))
                    elif action == "secret":
                        self.reply(200, {"answered": engine.answer_secret(task, body)})
                    elif action == "title":
                        self.reply(200, engine.auto_title(task))
                    elif action in {"goal/stop", "goal/resume"}:
                        self.reply(200, engine.goal_control(task, action.split("/")[1]))
                    else:
                        self.reply(404, {"error": "Not found"})
                else:
                    self.reply(404, {"error": "Not found"})
            except KeyError:
                self.reply(404, {"error": "Task not found"})
            except ApplyConflict as exc:
                self.reply(409, {"error": str(exc), "conflicts": exc.paths})
            except (ValueError, TypeError, TimeoutError) as exc:
                self.reply(400, {"error": str(exc)})

        def session_request(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError("Request must contain 1–1024 bytes")
                self.start_session(json.loads(self.rfile.read(length)))
            except (ValueError, TypeError, TimeoutError) as exc:
                self.reply(400, {"error": str(exc)})

        # M4 contract endpoints use PATCH and DELETE; dispatched through the
        # same shared routing table (no other handler restructuring).
        def do_PATCH(self):
            self.api_dispatch("PATCH")

        def do_DELETE(self):
            self.api_dispatch("DELETE")

        def api_dispatch(self, method):
            self.connection.settimeout(10)
            if not self.authorized():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 65536:
                    raise ValueError("Endpoint body limit is 64KB")
                body = json.loads(self.rfile.read(length)) if length else {}
                path = urlsplit(self.path).path
                browser = engine.browser.route(method, self.path, body)
                if browser is not None:
                    self.reply(*browser)
                    return
                bash = engine.bash.route(method, self.path, body)
                if bash is not None:
                    self.reply(*bash)
                    return
                if path == "/api/settings/app" and method == "PATCH":
                    self.reply(200, engine.update_app_settings(body))
                    return
                if path == "/api/projects/recent" and method == "DELETE" and isinstance(body, dict):
                    engine.workspaces.remove(str(body.get("path") or ""))
                    self.reply(200, projects.describe())
                    return
                if path.startswith("/api/workspaces/") and method == "DELETE":
                    engine.workspaces.remove(path.split("/")[3])
                    self.reply(200, {"removed": path.split("/")[3]})
                    return
                if path.startswith("/api/keys/") and method == "DELETE":
                    try:
                        api_keys.delete(path.split("/")[3])
                        self.reply(200, {"deleted": path.split("/")[3]})
                    except KeyError:
                        self.reply(404, {"error": "Key not found"})
                    return
                if path.startswith("/api/apps/") and method == "DELETE" and path.count("/") == 3:
                    try:
                        engine.apps.delete(path.split("/")[3])
                        self.reply(200, {"deleted": path.split("/")[3]})
                    except KeyError:
                        self.reply(404, {"error": "App not found"})
                    return
                if path.startswith("/api/worktrees/") and method == "DELETE" and path.count("/") == 3:
                    try:
                        engine.worktrees.remove(path.split("/")[3])
                        self.reply(200, {"removed": path.split("/")[3]})
                    except KeyError:
                        self.reply(404, {"error": "Worktree not found"})
                    return
                if path.startswith("/api/tasks/") and path.count("/") == 3:
                    task = path.split("/")[3]
                    if method == "PATCH":
                        engine.store.update_meta(task, body)
                        self.reply(200, {"id": task, **{k: v for k, v in body.items()}})
                    else:
                        engine.delete(task)
                        self.reply(200, {"deleted": task})
                    return
                self.reply(*routes(method, self.path, body))
            except KeyError:
                self.reply(404, {"error": "Task not found"})
            except (ValueError, TypeError, TimeoutError) as exc:
                self.reply(400, {"error": str(exc)})

    server = ThreadingHTTPServer((remote.listen if remote else "127.0.0.1", port), Handler)
    server.daemon_threads = True
    context = remote.tls_context() if remote else None
    if context is not None:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    wiring.port = server.server_port
    routes.oauth.port = server.server_port
    server.projects = projects  # the engine open right now: projects.engine
    return server


def early_exits(parser: argparse.ArgumentParser) -> None:
    """Answer --version / --info and refuse flag conflicts before anything starts."""
    argv = sys.argv[1:]
    flags = [flag for flag in argv if flag in {"-v", "--version", "--info"}]
    if not flags:
        return
    if len(flags) != 1 or set(argv) - set(flags):
        parser.error("--version and --info are standalone; no other flags may be given")
    if "--info" in argv:
        print(json.dumps({
            "version": DIKDIK_VERSION, "python": platform.python_version(),
            "platform": platform.platform(), "docker": shutil.which("docker") is not None,
            "rust_indexer": importlib.util.find_spec("dikdik.dikdik_index") is not None
                            or importlib.util.find_spec("dikdik_index") is not None,
        }, indent=2))
    else:
        print(f"Dikdik {DIKDIK_VERSION}")
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description="Dikdik — Small name. Serious intelligence.",
                                     prog="python -m dikdik.server")
    parser.add_argument("-v", "--version", action="store_true", help="Print the version and exit")
    parser.add_argument("--info", action="store_true", help="Print runtime information as JSON and exit")
    parser.add_argument("workspace_arg", nargs="?", metavar="FOLDER",
                        help="Project folder to open (default: the current folder, else the last project)")
    parser.add_argument("--workspace", help="Same as FOLDER")
    parser.add_argument("--data", default=str(Path.home() / ".dikdik"), help="Private durable state directory outside workspace")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--rotate-token", action="store_true", help="Issue a new session secret, signing out every browser")
    server_flags = parser.add_argument_group("running on a server (see docs/CLOUD.md)")
    server_flags.add_argument("--listen", default="127.0.0.1", help="Address to listen on; anything but loopback needs the flags below")
    server_flags.add_argument("--public-url", help="The https:// address people open, e.g. https://dikdik.example.com")
    server_flags.add_argument("--tls-cert", help="Certificate file for built-in HTTPS")
    server_flags.add_argument("--tls-key", help="Private key file for built-in HTTPS")
    server_flags.add_argument("--behind-proxy", action="store_true", help="A reverse proxy terminates HTTPS in front of Dikdik")
    server_flags.add_argument("--projects-root", help="On a server, every project must live inside this folder")
    args = parser.parse_args()
    early_exits(parser)
    check_config_or_exit()
    try:
        remote = remote_mode.check(args.listen, args.public_url, args.tls_cert, args.tls_key, args.behind_proxy, args.projects_root)
    except ValueError as exc:
        parser.error(str(exc))
    probe = socket.socket()
    probe.settimeout(0.5)
    if probe.connect_ex(("127.0.0.1", args.port)) == 0:
        probe.close()
        parser.error(f"Port {args.port} is already in use. Stop whatever is listening on it or choose another port.")
    probe.close()
    data = Path(args.data).resolve()
    chosen = args.workspace or args.workspace_arg
    note = ""
    try:
        root = check_location(Path(chosen or os.getcwd()), data, remote.projects_root if remote else None)
    except (OSError, ValueError) as exc:
        if chosen:
            parser.error(f"Workspace {str(exc)}")
        root = last_project(data)  # started from a folder that cannot be a project, such as the home folder
        if root is None:
            parser.error(f"{str(exc).rstrip('.')}. Start Dikdik inside a project folder, or name one: dikdik C:\\path\\to\\project")
        note = f"Note: {str(exc).rstrip('.')}. Opened your last project instead."
    try:
        engine = Engine(root, data)
    except ValueError as exc:
        parser.error(str(exc))
    token = session_token(data / "web.token", args.rotate_token)
    try:
        server = make_server(engine, args.port, token, remote)
    except OSError as exc:
        engine.close()
        if getattr(exc, "errno", None) in (errno_module.EADDRINUSE, errno_module.EACCES):
            parser.error(f"Port {args.port} is not available. Stop whatever is using it or pick another port.")
        raise
    url = remote.public_url if remote else f"http://127.0.0.1:{server.server_port}"
    # The token rides in the URL fragment, which browsers never send to a server.
    link = f"{url}/#token={token}"
    print(f"Dikdik is running at {url}\nProject: {root}"
          + (f"\n{note}" if note else "")
          + f"\nSign-in link (needed once per browser): {link}", flush=True)
    if not args.no_browser and not remote:
        webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.projects.engine.close()


def last_project(data: Path) -> Path | None:
    """The most recently opened project folder that still exists, if any."""
    from .workspace_registry import WorkspaceRegistry
    for entry in WorkspaceRegistry(data).list():
        try:
            return check_location(Path(entry["path"]), data)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


if __name__ == "__main__":
    main()
