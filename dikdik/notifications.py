"""Native desktop notifications from durable events, independent of the open chat."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit


KINDS = {"completed", "failed", "blocked", "paused", "confirmation_requested", "question_requested", "secret_requested"}
TITLES = {"completed": "Task finished", "failed": "Task failed", "blocked": "Task needs attention", "paused": "Task paused",
          "confirmation_requested": "Approval needed", "question_requested": "Dikdik has a question", "secret_requested": "Input needed"}
MESSAGES = {"completed": "Your task has finished. Open Dikdik to review the result.",
            "failed": "Your task could not finish. Open Dikdik to review what happened.",
            "blocked": "Your task needs attention before it can continue.",
            "paused": "Progress is saved. Open Dikdik to continue when you are ready.",
            "confirmation_requested": "Dikdik is waiting for your approval. Open the task to review it.",
            "question_requested": "Dikdik is waiting for your answer. Open the task to reply.",
            "secret_requested": "Dikdik is waiting for input. Open the task to review the request."}


@dataclass(frozen=True)
class Notice:
    task: str
    project: str
    title: str
    message: str
    attention: bool = False


def notice_for(event, task, settings):
    """Use generic copy by default; never put outputs, commands or secrets in a toast."""
    kind = event["kind"]
    if kind not in KINDS or settings.get("notifications", "background") == "off":
        return None
    result, request = task.get("result") or {}, task.get("request") or {}
    if result.get("browser_session") or result.get("summary") == "Workspace tools ready.":
        return None
    if kind in {"completed", "failed", "blocked", "paused"}:
        if task["state"] != kind:
            return None  # A newer turn already superseded this state.
    elif task["state"] not in {"queued", "running"}:
        return None  # An answered request or stopped task needs no notification.
    message = MESSAGES[kind]
    if settings.get("notification_previews") is True:
        title = str(task.get("title") or request.get("objective") or "Task")
        title = " ".join(title.split())[:120]
        message = f"{title} · {Path(task['workspace']).name}\n{message}"
    return Notice(task["id"], task["workspace"], TITLES[kind], message, kind != "completed")


class DesktopNotifications:
    """One native notifier and a lightweight event cursor for this desktop host."""
    def __init__(self, server, window, base_url, backend_factory=None):
        self.server, self.window, self.base_url = server, window, base_url
        self.backend_factory = backend_factory
        self._thread = None
        self._loop = None
        self._stop = threading.Event()
        self._cursor = 0
        self._status_lock = threading.Lock()
        self._status = {"desktop": True, "ready": False, "available": False, "permission": "unknown", "error": "", "sent": 0}
        self._notifier = self._sdk = None
        self._sent = OrderedDict()

    def status(self):
        with self._status_lock:
            return dict(self._status)

    def _set_status(self, **values):
        with self._status_lock:
            self._status.update(values)

    def start(self):
        if self._thread is not None or self._stop.is_set():
            return
        store = self.server.projects.engine.store
        with store.connect() as db:
            self._cursor = db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
        self._thread = threading.Thread(target=self._run, name="dikdik-notifications", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=12)

    def _run(self):
        asyncio.run(self._watch())

    async def _watch(self):
        self._loop = asyncio.get_running_loop()
        try:
            if self.backend_factory:
                self._notifier, self._sdk = self.backend_factory()
            else:
                import desktop_notifier as sdk
                icon = sdk.Icon(path=Path(__file__).parent / "web" / "icon.png")
                # Match the installed shortcut/window identity on Windows.
                import sys
                if sys.platform == "win32":
                    from desktop_notifier.backends.winrt import register_hkey
                    from .windows_shortcut import ensure_shortcut
                    ensure_shortcut()
                self._notifier = sdk.DesktopNotifier(app_name="Dikdik.Desktop" if sys.platform == "win32" else "Dikdik", app_icon=icon)
                self._notifier.app_name = "Dikdik"
                if sys.platform == "win32":
                    register_hkey("Dikdik.Desktop", "Dikdik", icon)
                self._sdk = sdk
            capabilities = await self._notifier.get_capabilities()
            if not capabilities:
                raise RuntimeError("Native notifications require a supported desktop session; macOS requires a signed app bundle")
            allowed = await self._notifier.has_authorisation()
            self._set_status(ready=True, available=True, permission="allowed" if allowed else "not_allowed")
            while not self._stop.is_set():
                try:
                    await self.poll_once()
                except Exception as exc:
                    self._set_status(error=f"Notification check failed: {str(exc)[:180]}")
                await asyncio.sleep(.5)
        except Exception as exc:
            self._set_status(ready=True, available=False, error=f"Native notifications unavailable: {str(exc)[:200]}")
        finally:
            self._loop = None

    def _events(self):
        engine = self.server.projects.engine
        store = engine.store
        with store.connect() as db:
            high = db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            placeholders = ",".join("?" for _ in KINDS)
            rows = db.execute(f"SELECT e.seq,e.task_id,e.kind,e.data,t.state,t.title,t.request,t.result,t.workspace "
                              f"FROM events e JOIN tasks t ON t.id=e.task_id WHERE e.seq>? AND e.seq<=? "
                              f"AND e.kind IN ({placeholders}) AND t.workspace=? ORDER BY e.seq LIMIT 100",
                              (self._cursor, high, *sorted(KINDS), store.workspace)).fetchall()
            events = []
            for row in rows:
                data = json.loads(row["data"])
                if row["kind"].endswith("_requested"):
                    later = db.execute("SELECT kind,data FROM events WHERE task_id=? AND seq>? AND kind IN ('confirmation_resolved','question_answered','secret_answered')", (row["task_id"], row["seq"])).fetchall()
                    resolved_kind = {"confirmation_requested": "confirmation_resolved", "question_requested": "question_answered", "secret_requested": "secret_answered"}[row["kind"]]
                    if any(record["kind"] == resolved_kind and json.loads(record["data"]).get("id") == data.get("id") for record in later):
                        continue
                task = {"id": row["task_id"], "workspace": row["workspace"], "state": row["state"], "title": row["title"],
                        "request": json.loads(row["request"]), "result": json.loads(row["result"]) if row["result"] else None,
                        "notification_run": db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE task_id=? AND seq<=? AND kind IN ('queued','user_message','resumed')", (row["task_id"], row["seq"])).fetchone()[0]}
                events.append(({"seq": row["seq"], "kind": row["kind"], "data": data}, task))
        return engine, events, rows[-1]["seq"] if len(rows) == 100 else high

    def _presence(self):
        try:
            if urlsplit(self.window.get_current_url()).netloc != urlsplit(self.base_url).netloc:
                return {"focused": False, "task": None}
            return self.window.evaluate_js("({focused:document.hasFocus()&&!document.hidden,task:new URLSearchParams(location.search).get('task')})") or {}
        except Exception:
            return {}

    async def poll_once(self):
        engine, events, cursor = self._events()
        settings = engine.app_settings()
        notices = {}
        for event, task in events:
            notice = notice_for(event, task, settings)
            if notice:
                key = (notice.task, task["notification_run"], event["kind"], event["data"].get("id", ""))
                if key not in self._sent:
                    notices[notice.task] = (key, notice)  # Latest state per conversation in this batch.
        self._cursor = cursor  # Advance even while muted; never replay old notifications.
        presence = self._presence() if notices and settings.get("notifications", "background") == "background" else {}
        for key, notice in notices.values():
            if self._stop.is_set():
                return
            if settings.get("notifications", "background") == "background" and presence.get("focused") is True and presence.get("task") == notice.task:
                continue
            self._sent[key] = True
            if len(self._sent) > 2048:
                self._sent.popitem(last=False)
            await self._send(notice, settings)

    async def _send(self, notice, settings):
        allowed = await self._notifier.request_authorisation()
        if not allowed:
            self._set_status(permission="not_allowed", error="Notifications are disabled in your operating system")
            return {"sent": False, "error": "Notifications are disabled in your operating system"}
        await self._notifier.send(title=notice.title, message=notice.message,
            on_clicked=lambda: self.activate(notice), thread="Dikdik tasks",
            sound=self._sdk.DEFAULT_SOUND if settings.get("sounds") else None)
        self._set_status(permission="allowed", error="", sent=self.status()["sent"] + 1)
        return {"sent": True}

    def test(self):
        status = self.status()
        if not status["ready"] or not status["available"] or self._loop is None:
            raise ValueError(status["error"] or "Native notifications are still starting")
        notice = Notice("", str(self.server.projects.engine.root), "Dikdik notifications are ready", "Task updates will appear here. Click to return to Dikdik.")
        return asyncio.run_coroutine_threadsafe(self._send(notice, self.server.projects.engine.app_settings()), self._loop).result(timeout=12)

    def activate(self, notice):
        """A click only opens the task; approvals always stay inside the workspace."""
        if self._stop.is_set():
            return
        try:
            self.window.restore()
            self.window.show()
            if not notice.task or not re.fullmatch(r"[a-f0-9]{32}", notice.task):
                return
            if urlsplit(self.window.get_current_url()).netloc != urlsplit(self.base_url).netloc:
                return
            detail = {"task": notice.task, "project": notice.project}
            self.window.evaluate_js("window.dispatchEvent(new CustomEvent('dikdik-open-notification',{detail:" + json.dumps(detail) + "}))")
        except Exception as exc:
            self._set_status(error=f"Could not open notification: {str(exc)[:160]}")
