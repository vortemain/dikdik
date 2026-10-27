"""Loopback-only authenticated HTTP interface. No repository code runs on host."""

import argparse
import hmac
import json
import os
import secrets
import time
import webbrowser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .engine import ApplyConflict, Engine, TERMINAL
from .control import IMAGE_TYPES, workspace_image, workspace_images
from . import gitops
from .repository import INDEXER, approved_root, inventory, metadata
from .server_routes import ROUTE_PREFIXES, build_routes


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
    routes = build_routes(engine)  # M4: shared (method, path) routing table
    expected = ("Bearer " + token).encode("utf-8")

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

        def same_origin(self):
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host or self.headers.get("Origin", "http://" + host) != "http://" + host:
                self.refuse(403, {"error": "Invalid host or origin"})
                return False
            return True

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

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in STATIC:
                filename, mime = STATIC[path]
                self.reply(200, (WEB / filename).read_bytes(), mime)
                return
            if not self.authorized():
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
                elif path.startswith("/api/tasks/") and path.endswith("/stream") and path.count("/") == 4:
                    after = parse_qs(urlsplit(self.path).query).get("after", ["0"])[0]
                    self.stream(path.split("/")[3], int(after) if after.isdigit() else 0)
                elif path == "/api/model":
                    self.reply(200, engine.models.describe())
                elif path == "/api/files/content":
                    query = parse_qs(urlsplit(self.path).query)
                    self.reply(200, engine.file_text(query.get("path", [""])[0], query.get("task", [None])[0]))
                elif path == "/api/git":
                    self.reply(200, gitops.info(engine.root))
                elif path == "/api/agent-info":
                    self.reply(200, engine.agent_info())
                elif path.startswith("/api/tasks/") and path.endswith("/download") and path.count("/") == 4:
                    self.reply(200, engine.archive(path.split("/")[3]), "application/zip")
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
                    raise ValueError(f"Request must contain 1–{limit} bytes")
                body = json.loads(self.rfile.read(length))
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
                    self.reply(200, gitops.commit(engine.root, body.get("message"), body.get("files"), engine.git_identity()))
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
    return server


def main():
    parser = argparse.ArgumentParser(description="Dikdik — Small name. Serious intelligence.")
    parser.add_argument("--workspace", required=True, help="Explicitly approved repository directory")
    parser.add_argument("--data", default=str(Path.home() / ".dikdik"), help="Private durable state directory outside workspace")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--rotate-token", action="store_true", help="Issue a new session secret, signing out every browser")
    args = parser.parse_args()
    root = approved_root(args.workspace)
    data = Path(args.data).resolve()
    if data == root or root in data.parents or data in root.parents:
        parser.error("Data and workspace directories must be separate, not ancestors")
    engine = Engine(root, data)
    token = session_token(data / "web.token", args.rotate_token)
    server = make_server(engine, args.port, token)
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
