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
from . import gitops
from . import __version__ as DIKDIK_VERSION
from .control import IMAGE_TYPES, workspace_image, workspace_images
from . import gitops
from .repository import INDEXER, approved_root, inventory, metadata
from .server_routes import ROUTE_PREFIXES, build_routes
from .gateway import Gateway
from .openapi import document as openapi_document


WEB = Path(__file__).parent / "web"
M4_PREFIXES = ROUTE_PREFIXES
FONTS = ("inter-latin-wght-normal", "inter-latin-ext-wght-normal", "jetbrains-mono-latin-wght-normal", "jetbrains-mono-latin-ext-wght-normal")
# An explicit allowlist: nothing else under web/ (or outside it) is ever served.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    **{f"/fonts/{name}.woff2": (f"fonts/{name}.woff2", "font/woff2") for name in FONTS},
}
SESSION_AGE = 30 * 24 * 3600
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


def make_server(engine: Engine, port: int, token: str):
    routes = build_routes(engine)
    gateway = Gateway(engine)
    engine.gateway = gateway  # the gateway routes after authentication in this handler  # M4: shared (method, path) routing table
    expected = ("Bearer " + token).encode("utf-8")
    started_at = time.monotonic()
    activity = {"last": time.monotonic()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never log authorization, objectives or repository contents.

        def reply(self, status, data, mime="application/json", cookie=None):
            body = json.dumps(data).encode() if mime == "application/json" else data
            self.send_response(status)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
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
            origin = self.headers.get("Origin", "http://" + host)
            if self.headers.get("Host") != host:
                self.refuse(403, {"error": "Invalid host or origin"})
                return False
            if origin == "http://" + host:
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
            if not hmac.compare_digest(supplied.encode("utf-8"), expected):
                self.refuse(401, {"error": "Not signed in. Open the link printed in the Dikdik terminal."})
                return False
            return True

        def start_session(self, body):
            supplied = body.get("token") if isinstance(body, dict) else None
            if not isinstance(supplied, str) or not hmac.compare_digest(("Bearer " + supplied).encode("utf-8"), expected):
                self.reply(401, {"error": "That sign-in link is not valid for this server. Use the one printed in the Dikdik terminal."})
                return
            cookie = f"{self.cookie_name}={token}; Path=/; Max-Age={SESSION_AGE}; HttpOnly; SameSite=Strict"
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
                self.reply(200, {"status": "ready"})
                return
            if path == "/server_info" or path == "/api/server-info":
                now = time.monotonic()
                self.reply(200, {"version": DIKDIK_VERSION, "python": platform.python_version(),
                                 "platform": platform.platform(), "workspace": engine.root.name,
                                 "rust_indexer": INDEXER, "port": self.server.server_port,
                                 "uptime": round(now - started_at, 3),
                                 "idle_time": round(now - activity["last"], 3)})
                return
            if path == "/openapi.json" or path == "/api/openapi":
                self.reply(200, openapi_document())
                return
            if not self.authorized():
                return
            answer = gateway.route("GET", path, {})
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
                    self.reply(200, {"name": engine.root.name, "indexer": INDEXER, **metadata(inventory(engine.root)), "images": workspace_images(engine.root)})
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
                    self.reply(200, engine.models.describe())
                elif path == "/api/files/content":
                    query = parse_qs(urlsplit(self.path).query)
                    self.reply(200, engine.file_text(query.get("path", [""])[0], query.get("task", [None])[0]))
                elif path == "/api/files/download":
                    query = parse_qs(urlsplit(self.path).query)
                    rel = query.get("path", [""])[0]
                    if not isinstance(rel, str) or not rel.strip() or len(rel) > 500 or rel.startswith("-"):
                        raise ValueError("path must be a safe relative path")
                    target = (engine.root / rel).resolve()
                    if engine.root.resolve() not in target.parents and target != engine.root.resolve():
                        raise ValueError("path must stay inside the workspace")
                    if not target.is_file():
                        raise KeyError(rel)
                    raw = target.read_bytes()
                    self.reply(200, raw, "application/octet-stream")
                elif path == "/api/files/list":
                    query = parse_qs(urlsplit(self.path).query)
                    rel = query.get("path", [""])[0] or "."
                    if not isinstance(rel, str) or len(rel) > 500 or rel.startswith("-"):
                        raise ValueError("path must be a safe relative path")
                    target = (engine.root / rel).resolve()
                    if engine.root.resolve() not in target.parents and target != engine.root.resolve():
                        raise ValueError("path must stay inside the workspace")
                    if not target.is_dir():
                        raise KeyError(rel)
                    entries = []
                    for child in sorted(target.iterdir(), key=lambda p: p.name):
                        if child.name.startswith("."):
                            continue
                        entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file",
                                         "size": child.stat().st_size if child.is_file() else None})
                    self.reply(200, {"path": rel, "entries": entries})
                elif path == "/api/git":
                    self.reply(200, gitops.info(engine.root))
                elif path.startswith("/api/git/") and path.endswith("/diff") and path.count("/") == 4:
                    self.reply(200, gitops.diff(engine.root, path.split("/")[3]))
                elif path == "/api/git/search":
                    query = parse_qs(urlsplit(self.path).query)
                    q = query.get("q", [""])[0]
                    lim = int(query.get("limit", ["50"])[0]) if query.get("limit", ["50"])[0].isdigit() else 50
                    self.reply(200, {"query": q, "results": gitops.search(engine.root, q, lim)})
                elif path == "/api/agent-info":
                    self.reply(200, engine.agent_info())
                elif path.startswith("/api/tasks/") and path.endswith("/download") and path.count("/") == 4:
                    self.reply(200, engine.archive(path.split("/")[3]), "application/zip")
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
                if not 0 < length <= limit:
                    raise ValueError(f"Request must contain 1â€“{limit} bytes")
                body = json.loads(self.rfile.read(length))
                answer = gateway.route(self.command, path, body)
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
                elif path == "/api/git/commit":
                    activity["last"] = time.monotonic()
                    self.reply(200, gitops.commit(engine.root, body.get("message"), body.get("files"), engine.git_identity()))
                elif path == "/api/files/upload" and isinstance(body, dict):
                    activity["last"] = time.monotonic()
                    rel = body.get("path")
                    content = body.get("content")
                    if not isinstance(rel, str) or not rel.strip() or len(rel) > 500 or rel.startswith("-"):
                        raise ValueError("path must be a safe relative path")
                    if not isinstance(content, str) or len(content) > 1_000_000:
                        raise ValueError("content must be text under 1MB")
                    target = (engine.root / rel).resolve()
                    if engine.root.resolve() not in target.parents:
                        raise ValueError("path must stay inside the workspace")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    self.reply(201, {"path": rel, "bytes": len(content.encode("utf-8"))})
                elif path == "/api/files/delete" and isinstance(body, dict):
                    activity["last"] = time.monotonic()
                    rel = body.get("path")
                    if not isinstance(rel, str) or not rel.strip() or len(rel) > 500 or rel.startswith("-"):
                        raise ValueError("path must be a safe relative path")
                    target = (engine.root / rel).resolve()
                    if engine.root.resolve() not in target.parents and target != engine.root.resolve():
                        raise ValueError("path must stay inside the workspace")
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
                    raise ValueError("Request must contain 1â€“1024 bytes")
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

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    routes.oauth.port = server.server_port
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
    parser = argparse.ArgumentParser(description="Dikdik â€” Small name. Serious intelligence.",
                                     prog="python -m dikdik.server")
    parser.add_argument("-v", "--version", action="store_true", help="Print the version and exit")
    parser.add_argument("--info", action="store_true", help="Print runtime information as JSON and exit")
    parser.add_argument("--workspace", help="Explicitly approved repository directory")
    parser.add_argument("--data", default=str(Path.home() / ".dikdik"), help="Private durable state directory outside workspace")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--rotate-token", action="store_true", help="Issue a new session secret, signing out every browser")
    args = parser.parse_args()
    early_exits(parser)
    if not args.workspace:
        parser.error("--workspace is required: pass an explicitly approved repository directory")
    probe = socket.socket()
    probe.settimeout(0.5)
    if probe.connect_ex(("127.0.0.1", args.port)) == 0:
        probe.close()
        parser.error(f"Port {args.port} is already in use. Stop whatever is listening on it or choose another port.")
    probe.close()
    try:
        root = approved_root(args.workspace)
    except (OSError, ValueError) as exc:
        parser.error(f"Workspace {str(exc)}")
        raise SystemExit(2)
    data = Path(args.data).resolve()
    if data == root or root in data.parents or data in root.parents:
        parser.error("Data and workspace directories must be separate, not ancestors")
    try:
        engine = Engine(root, data)
    except ValueError as exc:
        parser.error(str(exc))
    token = session_token(data / "web.token", args.rotate_token)
    try:
        server = make_server(engine, args.port, token)
    except OSError as exc:
        engine.close()
        if getattr(exc, "errno", None) in (errno_module.EADDRINUSE, errno_module.EACCES):
            parser.error(f"Port {args.port} is not available. Stop whatever is using it or pick another port.")
        raise
    url = f"http://127.0.0.1:{server.server_port}"
    # The token rides in the URL fragment, which browsers never send to a server.
    link = f"{url}/#token={token}"
    print(f"Dikdik is running at {url}\nWorkspace: {root}\nSign-in link (needed once per browser): {link}", flush=True)
    if not args.no_browser:
        webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()


if __name__ == "__main__":
    main()
