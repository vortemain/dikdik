"""Docker browser lifecycle and durable screenshots; no workspace mount or host JS."""

import base64
import http.client
import json
import queue
import struct
import subprocess
import threading
import time
import uuid
from urllib.parse import urlsplit

from .agent import BROWSER_TOOL, SHELL_TOOL, TOOLS
from .browser_network import fetch_public, public_url
from .sandbox import cleanup_container
from .planning import TASK_TRACKER, WRITE_PLAN, READ_PLAN


IMAGE = "dikdik-browser:1"
PNG_LIMIT = 2 * 1024 * 1024
FRAME_LIMIT = 3 * 1024 * 1024
NETWORK_LIMIT = 8 * 1024 * 1024


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


def browser_command(name: str) -> list[str]:
    return ["docker", "run", "--name", name, "--rm", "--pull=never", "--init", "-i",
            "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user=65534:65534", "--memory=1g", "--memory-swap=1g", "--cpus=1", "--pids-limit=128",
            "--ulimit=nofile=256:256", "--log-driver=none", "--shm-size=128m",
            "--tmpfs=/tmp:rw,exec,nosuid,size=256m", "--env=HOME=/tmp", IMAGE,
            "timeout", "-k", "5", "65", "node", "/opt/dikdik-browser/worker.cjs"]


def screenshot_bytes(value) -> bytes:
    if not isinstance(value, str) or len(value) > (PNG_LIMIT * 4 // 3 + 4):
        raise ValueError("Invalid browser screenshot")
    try:
        png = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError("Invalid browser screenshot") from None
    if len(png) < 33 or len(png) > PNG_LIMIT or png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR" or struct.unpack(">II", png[16:24]) != (1440, 900):
        raise ValueError("Invalid browser screenshot dimensions")
    return png


def capture(url: str, cancel: threading.Event, *, name: str) -> dict:
    """Only the fixed worker runs in Docker; all egress passes the public GET relay."""
    if cancel.is_set():
        return {"status": "cancelled", "error": "Browser capture cancelled"}
    try:
        proc = subprocess.Popen(browser_command(name), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, shell=False)
    except OSError:
        return {"status": "blocked", "error": "Docker CLI unavailable"}
    frames, overflow = queue.Queue(maxsize=68), threading.Event()
    def read():
        total = 0
        try:
            while line := proc.stdout.readline(FRAME_LIMIT + 1):
                total += len(line)
                if len(line) > FRAME_LIMIT or total > FRAME_LIMIT * 2 or not line.endswith(b"\n"):
                    overflow.set()
                    return
                try:
                    frames.put(json.loads(line), timeout=1)
                except (ValueError, queue.Full):
                    overflow.set()
                    return
        finally:
            try:
                frames.put(None, timeout=1)
            except queue.Full:
                pass
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline, ready, requests, received = time.monotonic() + 60, False, 0, 0
    result = {"status": "blocked", "error": "Browser image or Docker runtime is unavailable"}
    try:
        proc.stdin.write((json.dumps({"url": url}) + "\n").encode())
        proc.stdin.flush()
        while time.monotonic() < deadline:
            if cancel.is_set():
                result = {"status": "cancelled", "error": "Browser capture cancelled"}
                break
            if overflow.is_set():
                result = {"status": "failed", "error": "Browser protocol exceeded its bounds"}
                break
            try:
                frame = frames.get(timeout=.1)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if frame is None:
                break
            if not isinstance(frame, dict):
                raise ValueError("Invalid browser protocol")
            if frame.get("kind") == "ready":
                ready = True
                result = {"status": "failed", "error": "Browser could not capture this page"}
            elif frame.get("kind") == "request":
                requests += 1
                if not ready or requests > 64 or type(frame.get("id")) is not int or frame["id"] != requests:
                    raise ValueError("Invalid browser request sequence")
                reply = {"id": frame["id"]}
                try:
                    if received >= NETWORK_LIMIT:
                        raise ValueError("Browser page exceeds its network budget")
                    resource = fetch_public(frame.get("url"), timeout=min(5, max(.1, deadline - time.monotonic())))
                    received += len(resource["body"]) * 3 // 4
                    if received > NETWORK_LIMIT:
                        raise ValueError("Browser page exceeds its network budget")
                    reply.update(resource)
                except (ValueError, OSError, http.client.HTTPException):
                    reply["error"] = "Resource unavailable or blocked by the public URL policy"
                proc.stdin.write((json.dumps(reply) + "\n").encode())
                proc.stdin.flush()
            elif frame.get("kind") == "result" and ready:
                png = screenshot_bytes(frame.get("png"))
                result = {"status": "passed", "url": public_url(frame.get("url")), "png": png,
                          "title": _text(frame.get("title"), 256), "text": _text(frame.get("text"), 12000)}
                break
            elif frame.get("kind") == "error":
                break
            else:
                raise ValueError("Invalid browser protocol")
        else:
            result = {"status": "timed_out", "error": "Browser capture exceeded 60 seconds"}
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        result = {"status": "failed" if ready else "blocked", "error": "Browser could not capture this page"}
    finally:
        warning = cleanup_container(name)
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        reader.join(timeout=5)
        proc.stdin.close()
        proc.stdout.close()
        if warning:
            result["cleanup_warning"] = warning
            if result["status"] == "passed":
                result = {"status": "inconclusive", "error": warning}
    return result


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
        self._probe_at, self._image_ready = 0.0, False
        self._launch_verified = False
        self._idle = threading.Event()
        self._idle.set()
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS browser_snapshots (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, created REAL NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, png BLOB NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS browser_snapshots_task ON browser_snapshots(task_id,created)")
            unfinished = db.execute("SELECT task_id,data FROM events WHERE seq IN (SELECT MAX(seq) FROM events WHERE kind IN ('browser_started','browser_result') GROUP BY task_id) AND kind='browser_started'").fetchall()
        for row in unfinished:
            container = json.loads(row["data"]).get("container")
            warning = cleanup_container(container) if isinstance(container, str) and len(container) == 39 and container.startswith("dikdik-") and all(c in "0123456789abcdef" for c in container[7:]) else None
            self.store.event(row["task_id"], "browser_result", {"status": "interrupted", "error": "Browser capture was interrupted by a service restart", **({"cleanup_warning": warning} if warning else {})})

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
        return self.info()

    def info(self):
        # This checks the local image protocol, not a successful Chromium launch.
        now = time.monotonic()
        with self._lock:
            if now - self._probe_at > 30:
                try:
                    probe = subprocess.run(['docker', 'image', 'inspect', '--format={{index .Config.Labels "org.dikdik.browser.protocol"}}', IMAGE],
                                           capture_output=True, timeout=3, shell=False)
                    self._image_ready = probe.returncode == 0 and probe.stdout.strip() == b'1'
                except (OSError, subprocess.TimeoutExpired):
                    self._image_ready = False
                self._probe_at = now
            image_ready = self._image_ready
        return {"enabled": self.enabled(), "image": IMAGE, "runtime": "docker", "network": "public_get_relay",
                "availability": "image_ready" if image_ready else "unavailable", "launch_verified": self._launch_verified,
                "approval_required": ["approve_execution", "approve_browser"]}

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

    def stop(self, task):
        self.store.get(task)
        with self._lock:
            cancel = self._active.get(task)
            if cancel:
                cancel.set()
        return {"stop_requested": cancel is not None}

    def visit(self, task, url, approval, cancel=None):
        self.store.get(task)
        if approval.get("approve_execution") is not True or approval.get("approve_browser") is not True:
            raise PermissionError("Approve Docker execution and public web browsing for this task")
        url = public_url(url)
        if not self.enabled():
            raise PermissionError("Browser tools are disabled in Settings")
        cancel = CaptureCancel(cancel)
        with self.engine.lock:
            self.store.get(task)  # coordinate task deletion with launch
            with self._lock:
                if not self.enabled():
                    raise PermissionError("Browser tools are disabled in Settings")
                if self._closed or len(self._active) >= 2 or task in self._active:
                    raise ValueError("A browser capture is already running; wait for it to finish")
                self._active[task] = cancel
                self._idle.clear()
        started, snapshot = time.monotonic(), uuid.uuid4().hex
        try:
            self.store.event(task, "browser_started", {"url": url, "container": "dikdik-" + snapshot})
            try:
                result = capture(url, cancel, name="dikdik-" + snapshot)
            except Exception:
                result = {"status": "inconclusive", "error": "Browser capture failed"}
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

    def screenshot(self, task, snapshot):
        self.store.get(task)
        with self.store.connect() as db:
            row = db.execute("SELECT png FROM browser_snapshots WHERE task_id=? AND id=?", (task, snapshot)).fetchone()
        if row is None:
            raise KeyError(snapshot)
        return row[0]

    def route(self, method, raw_path, body=None):
        path = urlsplit(raw_path).path
        try:
            tools = TOOLS + [SHELL_TOOL, BROWSER_TOOL, TASK_TRACKER, WRITE_PLAN, READ_PLAN]
            if method == 'GET' and path in {'/api/tools', '/api/tools/'}:
                return 200, [tool['function']['name'] for tool in tools]
            if method == 'GET' and path in {'/api/tools/catalog', '/server_info'}:
                info = self.info()
                browser_usable = info['enabled'] and info['availability'] == 'image_ready'
                if path == '/server_info':
                    return 200, {'conversation_runtime': 'docker', 'browser': info, 'usable_tools': [tool['function']['name'] for tool in tools if tool is not BROWSER_TOOL or browser_usable]}
                return 200, {'tools': [{'name': tool['function']['name'], 'user_selectable': tool not in [WRITE_PLAN, READ_PLAN], 'usable': tool is not BROWSER_TOOL or browser_usable,
                                        'description': tool['function']['description'], 'in_default_set': tool in TOOLS or tool is TASK_TRACKER,
                                        **({'approval_required': info['approval_required']} if tool is BROWSER_TOOL else {})} for tool in tools]}
            if path == "/api/browser":
                return (200, self.info()) if method == "GET" else (200, self.configure(body)) if method == "PATCH" else None
            parts = path.split("/")
            if len(parts) == 6 and parts[:3] == ["", "api", "tasks"] and parts[4:] == ["browser", "stop"] and method == "POST":
                return 200, self.stop(parts[3])
            if len(parts) != 5 or parts[:3] != ["", "api", "tasks"] or parts[4] != "browser":
                return None
            task = parts[3]
            if method == "GET":
                events = self.store.get(task)["events"]
                return 200, {"latest": next((e["data"] for e in reversed(events) if e["kind"] == "browser_result"), None), **self.info()}
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
