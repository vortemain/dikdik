"""Browser captures and durable screenshots: Playwright's Chromium when it is installed,
otherwise the computer's own Edge or Chrome (`host_browser`: fresh profile, every request
through a public-only proxy). Neither gets the workspace."""

import base64
import json
import threading
import time
import uuid
from urllib.parse import urlsplit

from .agent import BROWSER_ACTION_TOOLS, BROWSER_TOOL, SHELL_TOOL, TOOLS, WEB_FETCH_TOOL
from .browser_network import public_url
from . import host_browser
from .interactive_browser import BrowserSessions, available as interactive_available, web_fetch
from .planning import TASK_TRACKER, WRITE_PLAN, READ_PLAN
from . import migrations


HOST_PNG_LIMIT = 8 * 1024 * 1024  # a full-colour 1440x900 page from a real browser
PRESETS = {"mobile": (390, 844), "tablet": (768, 1024), "desktop": (1440, 900)}
# Agent tool -> browser action. Each returns the page's accessibility tree after acting, except
# the tools that read records or values, which skip looking at the page again.
AGENT_ACTIONS = {"browser_snapshot": "snapshot", "browser_click": "click", "browser_type": "type", "browser_fill": "fill",
                 "browser_select": "select", "browser_press": "press", "browser_hover": "hover", "browser_drag": "drag",
                 "browser_scroll": "scroll", "browser_wait": "wait", "browser_resize": "resize",
                 "browser_screenshot": "screenshot", "browser_inspect": "inspect", "browser_evaluate": "evaluate",
                 "browser_console": "console", "browser_network": "network"}
QUIET_ACTIONS = {"inspect", "evaluate", "console", "network", "screenshot"}


class CaptureCancel:
    """A tool can stop independently while still observing its task's stop flag."""
    def __init__(self, parent=None):
        self.parent, self.local = parent, threading.Event()

    def set(self):
        self.local.set()

    def is_set(self):
        return self.local.is_set() or (self.parent is not None and self.parent.is_set())

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            self.local.wait(.05 if deadline is None else min(.05, max(0, deadline - time.monotonic())))
        return True


def _text(value, limit):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value[:limit] if ord(c) >= 32 or c in "\n\t")


class BrowserService:
    def __init__(self, engine):
        self.engine, self.store = engine, engine.store
        self._lock = threading.Lock()
        self._active: dict[str, CaptureCancel] = {}
        self._closed = False
        self.sessions = BrowserSessions(preview_port=self.app_port)
        self._launch_verified = False
        self._idle = threading.Event()
        self._idle.set()
        migrations.ensure(self.store.path, "browser_snapshots", [(1, "browser screenshots", migrations.script(
            "CREATE TABLE IF NOT EXISTS browser_snapshots (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, created REAL NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, png BLOB NOT NULL);\n"
            "CREATE INDEX IF NOT EXISTS browser_snapshots_task ON browser_snapshots(task_id,created);\n"))])
        with self.store.connect() as db:
            unfinished = db.execute("SELECT task_id,data FROM events WHERE seq IN (SELECT MAX(seq) FROM events WHERE kind IN ('browser_started','browser_result') GROUP BY task_id) AND kind='browser_started'").fetchall()
        for row in unfinished:
            self.store.event(row["task_id"], "browser_result", {"status": "interrupted", "error": "Browser capture was interrupted by a service restart"})

    def enabled(self):
        with self.store.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='enable_browser'").fetchone()
        return row is None or row[0] == "true"

    def configure(self, body):
        if not isinstance(body, dict) or set(body) != {"enabled"} or type(body["enabled"]) is not bool:
            raise ValueError("enabled must be a Boolean")
        with self.store.connect() as db:
            db.execute("INSERT INTO settings(key,value) VALUES('enable_browser',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(body["enabled"]),))
        if not body["enabled"]:
            with self._lock:
                for cancel in self._active.values():
                    cancel.set()
            if not self._idle.wait(75):
                raise RuntimeError("Browser actions are still stopping")
            self.sessions.close()
            self.sessions = BrowserSessions(preview_port=self.app_port)
        return self.info()

    def info(self):
        if interactive_available():
            return {"enabled": self.enabled(), "runtime": "Chromium", "browser": "Chromium",
                    "network": "public_only_proxy", "availability": "interactive_ready", "interactive": True,
                    "launch_verified": self._launch_verified, "approval_required": ["approve_execution", "approve_browser"]}
        host = host_browser.find_browser()
        return {"enabled": self.enabled(), "runtime": "this computer",
                "browser": host and host.replace("\\", "/").rsplit("/", 1)[-1],
                "network": "public_only_proxy",
                "availability": "host_ready" if host else "unavailable",
                "launch_verified": self._launch_verified, "approval_required": ["approve_execution", "approve_browser"]}

    def app_port(self):
        """The port of the project's app that preview_app left running, so the agent's browser can
        open it at http://preview.dikdik.example/ and test it step by step; None when none runs."""
        live = getattr(self.engine, "live_preview", None)
        status = live.status() if live is not None else {}
        return status.get("port") if status.get("running") else None

    def live_frames(self, task, seconds=1800):
        """JPEG frames of the task's page as it changes, for the Browser panel's live view. The
        newest frame is resent every few seconds so a closed panel is noticed and the stream ends."""
        self.store.status(task)
        if not self.enabled():
            raise PermissionError("Browser tools are disabled in Settings")
        sessions = self.sessions
        if not sessions.has_page(task):
            raise KeyError(task)
        return self._frames(task, sessions, seconds)  # checked above, before any response is sent

    def _frames(self, task, sessions, seconds):
        seq, last, sent = 0, None, time.monotonic()
        deadline = time.monotonic() + seconds
        with sessions.watch(task):
            while time.monotonic() < deadline and not sessions.closed and not self._closed and sessions is self.sessions:
                got = sessions.frame(task, seq, timeout=4)
                if got:
                    seq, last = got
                    sent = time.monotonic()
                    yield last
                elif not sessions.has_page(task):
                    return
                elif last is not None and time.monotonic() - sent > 4:
                    sent = time.monotonic()
                    yield last

    def busy(self, task):
        with self._lock:
            return task in self._active

    def close(self):
        with self._lock:
            self._closed = True
            for cancel in self._active.values():
                cancel.set()
        if not self._idle.wait(95):
            raise RuntimeError("Browser capture did not stop; keep the data directory locked")
        self.sessions.close()

    def stop(self, task):
        self.store.status(task)
        with self._lock:
            cancel = self._active.get(task)
            if cancel:
                cancel.set()
        return {"stop_requested": cancel is not None}

    def visit(self, task, url, approval, cancel=None, options=None):
        self.store.status(task)
        if approval.get("approve_execution") is not True or approval.get("approve_browser") is not True:
            raise PermissionError("Approve running tools and public web browsing for this task")
        url = public_url(url)
        if urlsplit(url).hostname == host_browser.PREVIEW_HOST and self.app_port() is None:
            raise ValueError("The project's app is not running. Run preview_app first; it keeps the dev server running "
                             "so http://preview.dikdik.example/ opens the app here.")
        if not self.enabled():
            raise PermissionError("Browser tools are disabled in Settings")
        cancel = CaptureCancel(cancel)
        with self.engine.lock:
            self.store.status(task)  # coordinate task deletion with launch
            with self._lock:
                if not self.enabled():
                    raise PermissionError("Browser tools are disabled in Settings")
                if self._closed or len(self._active) >= 2 or task in self._active:
                    raise ValueError("A browser capture is already running; wait for it to finish")
                self._active[task] = cancel
                self._idle.clear()
        started, snapshot = time.monotonic(), uuid.uuid4().hex
        try:
            interactive = interactive_available()
            on_host = not interactive and self.info()["availability"] == "host_ready"
            self.store.event(task, "browser_started", {"url": url, "runtime": "Chromium" if interactive else "this computer"})
            try:
                if interactive:
                    result = self.sessions.execute(task, "navigate", {"url": url, "viewport": approval.get("viewport"), **(options or {})}, cancel)
                elif on_host:
                    result = host_browser.capture(url, cancel)
                else:
                    result = {"status": "blocked", "error": "No browser available: install Playwright's Chromium, Edge or Chrome"}
            except Exception:
                result = {"status": "inconclusive", "error": "Browser capture failed"}
            if on_host and len(result.get("png") or b"") > HOST_PNG_LIMIT:
                result = {"status": "failed", "error": "The page screenshot was too large to keep"}
            if cancel.is_set():
                result = {"status": "cancelled", "error": "Browser capture cancelled"}
            png = result.pop("png", None)
            result = {**result, "seconds": round(time.monotonic() - started, 3)}
            if result["status"] == "passed" and png:
                with self._lock:
                    self._launch_verified = True
                with self.store.connect() as db:
                    db.execute("INSERT INTO browser_snapshots VALUES(?,?,?,?,?,?,?)", (snapshot, task, time.time(), result["url"], result["title"], result["text"], png))
                    db.execute("DELETE FROM browser_snapshots WHERE task_id=? AND id NOT IN (SELECT id FROM browser_snapshots WHERE task_id=? ORDER BY created DESC LIMIT 10)", (task, task))
                    db.execute("DELETE FROM browser_snapshots WHERE id NOT IN (SELECT id FROM browser_snapshots ORDER BY created DESC LIMIT 100)")
                result["screenshot"] = f"/api/tasks/{task}/browser/screenshots/{snapshot}"
            self.store.event(task, "browser_result", result)
            return result
        finally:
            with self._lock:
                self._active.pop(task, None)
                if not self._active:
                    self._idle.set()

    def agent_action(self, task, tool, arguments, cancel=None):
        """An agent's browser_* tool call, translated to an action on its task's page."""
        args = dict(arguments or {})
        if tool == "browser_check":
            action = "check" if args.pop("checked", True) is not False else "uncheck"
        elif tool == "browser_navigate":
            action = args.pop("direction", None)
            if action not in {"back", "forward", "reload"}:
                raise ValueError("direction is back, forward or reload")
        elif tool in AGENT_ACTIONS:
            action = AGENT_ACTIONS[tool]
        else:
            raise ValueError("Tool unavailable")
        if action == "resize" and "preset" in args:
            preset = args.pop("preset")
            if preset not in PRESETS:
                raise ValueError("preset is mobile, tablet or desktop")
            args["width"], args["height"] = PRESETS[preset]
        if action == "inspect":
            args = {"selectors": args.get("targets")}
        args.setdefault("observe", "none" if action in QUIET_ACTIONS else "snapshot")
        return self.action(task, action, args, cancel, attach=action == "screenshot")

    def action(self, task, action_type, arguments, cancel=None, attach=False):
        """Act on the same persistent page as browse_url and the workspace panel."""
        self.store.status(task)
        if not self.enabled():
            raise PermissionError("Browser tools are disabled in Settings")
        stop = CaptureCancel(cancel)
        with self.engine.lock:
            self.store.status(task)
            with self._lock:
                if not self.enabled():
                    raise PermissionError("Browser tools are disabled in Settings")
                if self._closed or task in self._active:
                    raise ValueError("A browser action is already running")
                self._active[task] = stop; self._idle.clear()
        # A pointer moving over the live view is not history; everything else is recorded.
        record = action_type != "hover" or (arguments or {}).get("observe") != "none"
        try:
            if record:
                self.store.event(task, "browser_started", {"action": action_type, "runtime": "Chromium"})
            result = self.sessions.execute(task, action_type, arguments, stop)
            png = result.pop("png", None)
            if attach and png and result.get("status") == "passed" and len(png) <= 3 * 1024 * 1024:
                result["attach_images"] = ["data:image/png;base64," + base64.b64encode(png).decode("ascii")]
            if png and result["status"] == "passed":
                snapshot = uuid.uuid4().hex
                with self.store.connect() as db:
                    db.execute("INSERT INTO browser_snapshots VALUES(?,?,?,?,?,?,?)", (snapshot, task, time.time(), result["url"], result["title"], result["text"], png))
                    db.execute("DELETE FROM browser_snapshots WHERE task_id=? AND id NOT IN (SELECT id FROM browser_snapshots WHERE task_id=? ORDER BY created DESC LIMIT 10)", (task, task))
                    db.execute("DELETE FROM browser_snapshots WHERE id NOT IN (SELECT id FROM browser_snapshots ORDER BY created DESC LIMIT 100)")
                result["screenshot"] = f"/api/tasks/{task}/browser/screenshots/{snapshot}"
            if record:
                self.store.event(task, "browser_result", {k: v for k, v in result.items() if k != "attach_images"})
            return result
        finally:
            with self._lock:
                self._active.pop(task, None)
                if not self._active: self._idle.set()

    def screenshot(self, task, snapshot):
        self.store.status(task)
        with self.store.connect() as db:
            row = db.execute("SELECT png FROM browser_snapshots WHERE task_id=? AND id=?", (task, snapshot)).fetchone()
        if row is None:
            raise KeyError(snapshot)
        return row[0]

    def fetch(self, task, url):
        self.store.status(task)
        if not self.enabled():
            raise PermissionError("Browser tools are disabled in Settings")
        return web_fetch(url)

    def route(self, method, raw_path, body=None):
        path = urlsplit(raw_path).path
        try:
            actions = BROWSER_ACTION_TOOLS
            tools = TOOLS + [SHELL_TOOL, BROWSER_TOOL, WEB_FETCH_TOOL, TASK_TRACKER, WRITE_PLAN, READ_PLAN] + actions
            if method == 'GET' and path in {'/api/tools', '/api/tools/'}:
                return 200, [tool['function']['name'] for tool in tools]
            if method == 'GET' and path in {'/api/tools/catalog', '/server_info'}:
                info = self.info()
                browser_usable = info['enabled'] and info['availability'] in {'host_ready', 'interactive_ready'}
                if path == '/server_info':
                    return 200, {'conversation_runtime': 'this computer', 'browser': info, 'usable_tools': [tool['function']['name'] for tool in tools if (browser_usable if tool is BROWSER_TOOL else info['enabled'] if tool is WEB_FETCH_TOOL else info['enabled'] and info.get('interactive') if tool in actions else True)]}
                return 200, {'tools': [{'name': tool['function']['name'], 'user_selectable': tool not in [WRITE_PLAN, READ_PLAN], 'usable': (browser_usable if tool is BROWSER_TOOL else info['enabled'] if tool is WEB_FETCH_TOOL else bool(info['enabled'] and info.get('interactive')) if tool in actions else True),
                                        'description': tool['function']['description'], 'in_default_set': tool in TOOLS or tool is TASK_TRACKER,
                                        **({'approval_required': info['approval_required']} if tool is BROWSER_TOOL else {})} for tool in tools]}
            if path == "/api/browser":
                return (200, self.info()) if method == "GET" else (200, self.configure(body)) if method == "PATCH" else None
            if path == "/api/browser/sessions" and method == "POST":
                if not isinstance(body, dict) or body.get("approve_browser") is not True or body.get("approve_execution") is not True:
                    raise PermissionError("Approve browser tools to start a browser session")
                if not self.enabled():
                    raise PermissionError("Browser tools are disabled in Settings")
                task = self.store.create({"objective": "Browse the web", "mode": "agent", "verification": "syntax", "edits": [], "approve_execution": True, "approve_browser": True, "approve_external": False, "session": "browser"})
                self.store.state(task, "completed", {"browser_session": True, "summary": "Workspace browser session"})
                return 201, {"id": task}
            parts = path.split("/")
            if len(parts) == 6 and parts[:3] == ["", "api", "tasks"] and parts[4:] == ["browser", "actions"] and method == "POST":
                if not isinstance(body, dict) or body.get("approve_browser") is not True or body.get("approve_execution") is not True:
                    raise PermissionError("Approve browser actions for this request")
                return 200, self.action(parts[3], body.get("action"), body.get("arguments") or {})
            if len(parts) == 6 and parts[:3] == ["", "api", "tasks"] and parts[4:] == ["browser", "stop"] and method == "POST":
                return 200, self.stop(parts[3])
            if len(parts) != 5 or parts[:3] != ["", "api", "tasks"] or parts[4] != "browser":
                return None
            task = parts[3]
            if method == "GET":
                events = self.store.get(task)["events"]
                return 200, {"latest": next((e["data"] for e in reversed(events) if e["kind"] == "browser_result"), None),
                             "live": self.sessions.has_page(task), **self.info()}
            if method == "POST":
                if not isinstance(body, dict):
                    raise ValueError("Body must be a JSON object")
                return 200, self.visit(task, body.get("url"), body)
        except PermissionError as exc:
            return 403, {"error": str(exc)}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        except KeyError:
            return 404, {"error": "Task not found"}
        return None
