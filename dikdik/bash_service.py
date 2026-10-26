"""Persistent, task-scoped shell commands executed only in the Docker sandbox.

The service deliberately has no host/global execution mode.  Every command is
attached to a task whose request already contains explicit Docker consent, runs
on a throwaway copy, has no network, and cannot write to the approved workspace.
"""

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlsplit

from .repository import inventory
from .runners import IMAGES, run_shell


MAX_TIMEOUT = 300
MAX_LIMIT = 500
MAX_OUTPUT = 2 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
MAX_INFLIGHT = 16

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bash_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL, command_id TEXT NOT NULL,
    event_order INTEGER NOT NULL, time REAL NOT NULL, kind TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bash_events_task
    ON bash_events(task_id, seq);
CREATE INDEX IF NOT EXISTS bash_events_command
    ON bash_events(task_id, command_id, event_order, time, id);
"""


def _task_id(value: str) -> str:
    if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Task id must be 32 lowercase hexadecimal characters")
    return value


def _command_id(value: str) -> str:
    compact = value.replace("-", "")
    if len(compact) != 32 or any(c not in "0123456789abcdef" for c in compact.lower()):
        raise ValueError("Command id must be a UUID")
    return compact.lower()


def _relative_cwd(value) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > 500 or "\\" in value:
        raise ValueError("cwd must be a relative workspace path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ValueError("cwd must stay inside the task workspace")
    return str(path)


class BashService:
    """Durable command/event API owned by the Engine lifecycle."""

    def __init__(self, engine):
        self.engine = engine
        self.store = engine.store
        self._lock = threading.Lock()
        self._active: dict[str, threading.Event] = {}
        self._done: dict[str, threading.Event] = {}
        self._deleting: set[str] = set()
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dikdik-bash")
        with self.store.connect() as db:
            db.executescript(_SCHEMA)

    def close(self):
        with self._lock:
            self._closed = True
            for cancel in self._active.values():
                cancel.set()
        self._pool.shutdown(wait=True, cancel_futures=False)

    def busy(self, task_id: str) -> bool:
        with self._lock:
            return any(command.startswith(task_id + ":") for command in self._active)

    def _approved_task(self, task_id: str) -> dict:
        task = self.store.get(_task_id(task_id))
        if task["request"].get("approve_execution") is not True:
            raise PermissionError("This task has no explicit Docker execution approval")
        return task

    def _files(self, task_id: str) -> dict[str, str]:
        artifact = self.engine.data / "artifacts" / task_id
        if artifact.is_dir():
            return inventory(artifact)
        return inventory(self.engine.root)

    def _insert(self, task_id: str, command_id: str, order: int, kind: str, data: dict) -> dict:
        event_id, now = uuid.uuid4().hex, time.time()
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO bash_events(id,task_id,command_id,event_order,time,kind,data) VALUES(?,?,?,?,?,?,?)",
                (event_id, task_id, command_id, order, now, kind, json.dumps(data)),
            )
        event = {"id": event_id, "task_id": task_id, "command_id": command_id,
                 "order": order, "time": now, "kind": kind, **data}
        self.store.event(task_id, "terminal_command" if kind == "command" else "terminal_output", event)
        return event

    def _validate(self, body) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Body must be a JSON object")
        command = body.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > 4000:
            raise ValueError("Command must contain 1–4000 characters")
        timeout = body.get("timeout", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT:
            raise ValueError(f"timeout must be a whole number from 1 to {MAX_TIMEOUT}")
        image = body.get("image", "python")
        if image not in IMAGES:
            raise ValueError(f"Image must be one of {', '.join(sorted(IMAGES))}")
        return {"command": command, "timeout": timeout, "image": image, "cwd": _relative_cwd(body.get("cwd"))}

    def start(self, task_id: str, body) -> dict:
        self._approved_task(task_id)
        request = self._validate(body)
        command_id = uuid.uuid4().hex
        key = f"{task_id}:{command_id}"
        cancel, done = threading.Event(), threading.Event()
        with self._lock:
            if self._closed:
                raise ValueError("Terminal service is stopping")
            if task_id in self._deleting:
                raise ValueError("Task is being deleted")
            if len(self._active) >= MAX_INFLIGHT:
                raise ValueError(f"At most {MAX_INFLIGHT} terminal commands may be pending")
            self._done = {old_key: event for old_key, event in self._done.items()
                          if old_key in self._active or not event.is_set()}
            self._active[key] = cancel
            self._done[key] = done
        try:
            self._insert(task_id, command_id, 0, "command", {
                "command": request["command"], "cwd": request["cwd"] or None,
                "timeout": request["timeout"], "image": request["image"],
            })
            self._pool.submit(self._run, task_id, command_id, request, cancel, done)
        except Exception:
            with self._lock:
                self._active.pop(key, None)
                self._done.pop(key, None)
            done.set()
            raise
        return {"id": command_id, "task_id": task_id, "kind": "command", **request}

    def execute(self, task_id: str, body) -> dict:
        command = self.start(task_id, body)
        key = f"{task_id}:{command['id']}"
        with self._lock:
            done = self._done.get(key)
        if done is None or not done.wait(command["timeout"] + 20):
            self.stop(task_id, command["id"])
            raise TimeoutError("Sandbox command did not finish cleanly")
        outputs = self.search(task_id, {"command_id": command["id"], "kind": "output", "limit": str(MAX_LIMIT)})["items"]
        final = next((event for event in reversed(outputs) if event.get("final") is True), None)
        if final is None:
            raise RuntimeError("Sandbox command ended without a final output event")
        return {**final, "output": "".join(str(event.get("output", "")) for event in outputs)}

    def _run(self, task_id: str, command_id: str, request: dict, cancel: threading.Event, done: threading.Event):
        order, buffered, sent = 0, [], 0

        def chunk(text: str):
            nonlocal order, sent
            if not text or sent >= MAX_OUTPUT:
                return
            text = text[:MAX_OUTPUT - sent]
            sent += len(text)
            buffered.append(text)
            joined = "".join(buffered)
            while len(joined) >= CHUNK_SIZE:
                order += 1
                self._insert(task_id, command_id, order, "output", {"output": joined[:CHUNK_SIZE], "final": False})
                joined = joined[CHUNK_SIZE:]
            buffered[:] = [joined]

        command = request["command"]
        if request["cwd"]:
            # The path is already restricted to plain relative workspace segments.
            quoted = "'" + request["cwd"].replace("'", "'\\''") + "'"
            command = f"cd -- {quoted} && {command}"
        try:
            result = ({"status": "cancelled", "exit_code": -15, "output": "", "changed": {}}
                      if cancel.is_set() else run_shell(
                          self.engine.data / "bash" / command_id, self._files(task_id), command, cancel,
                          image=request["image"], timeout=request["timeout"],
                          name="dikdik-" + command_id, on_chunk=chunk, output_limit=MAX_OUTPUT,
                      ))
            if not buffered and result.get("output"):
                chunk(str(result["output"]))
            if buffered and buffered[0]:
                order += 1
                self._insert(task_id, command_id, order, "output", {"output": buffered[0], "final": False})
            order += 1
            self._insert(task_id, command_id, order, "output", {
                "output": "", "final": True, "exit_code": result.get("exit_code"),
                "status": result.get("status"), "truncated": bool(result.get("output_truncated") or sent >= MAX_OUTPUT),
                **({"error": result["error"]} if isinstance(result.get("error"), str) else {}),
            })
        except Exception:
            order += 1
            self._insert(task_id, command_id, order, "output", {
                "output": "", "final": True, "exit_code": None, "status": "inconclusive",
                "error": "Sandbox command failed",
            })
        finally:
            key = f"{task_id}:{command_id}"
            with self._lock:
                self._active.pop(key, None)
            done.set()

    def stop(self, task_id: str, command_id: str) -> dict:
        self._approved_task(task_id)
        command_id = _command_id(command_id)
        if self.get(task_id, command_id, command_event=True) is None:
            raise KeyError(command_id)
        key = f"{task_id}:{command_id}"
        with self._lock:
            cancel = self._active.get(key)
        if cancel:
            cancel.set()
        return {"id": command_id, "stop_requested": bool(cancel)}

    def get(self, task_id: str, event_id: str, command_event: bool = False):
        self._approved_task(task_id)
        value = _command_id(event_id)
        column = "command_id" if command_event else "id"
        with self.store.connect() as db:
            row = db.execute(f"SELECT * FROM bash_events WHERE task_id=? AND {column}=? " +
                             ("AND kind='command' " if command_event else "") + "ORDER BY time LIMIT 1", (task_id, value)).fetchone()
        return self._row(row) if row else None

    def search(self, task_id: str, query: dict) -> dict:
        self._approved_task(task_id)
        limit_text = query.get("limit", "100")
        after_text = query.get("after", "0")
        if not str(limit_text).isdigit() or not 1 <= int(limit_text) <= MAX_LIMIT:
            raise ValueError(f"limit must be 1–{MAX_LIMIT}")
        if not str(after_text).isdigit():
            raise ValueError("after must be a non-negative event sequence")
        clauses, args = ["task_id=?", "seq>?"], [task_id, int(after_text)]
        if query.get("command_id"):
            clauses.append("command_id=?")
            args.append(_command_id(query["command_id"]))
        if query.get("kind"):
            if query["kind"] not in {"command", "output"}:
                raise ValueError("kind must be command or output")
            clauses.append("kind=?")
            args.append(query["kind"])
        args.append(int(limit_text) + 1)
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM bash_events WHERE " + " AND ".join(clauses) +
                              " ORDER BY seq LIMIT ?", args).fetchall()
        limit = int(limit_text)
        items = [self._row(row) for row in rows[:limit]]
        return {"items": items, "next_after": items[-1]["seq"] if len(rows) > limit and items else None}

    def clear(self, task_id: str) -> dict:
        self._approved_task(task_id)
        if self.busy(task_id):
            raise ValueError("Stop running commands before clearing terminal history")
        with self.store.connect() as db:
            count = db.execute("DELETE FROM bash_events WHERE task_id=?", (task_id,)).rowcount
            db.execute("DELETE FROM events WHERE task_id=? AND kind IN ('terminal_command','terminal_output')", (task_id,))
        return {"cleared_count": count}

    def prepare_delete(self, task_id: str) -> None:
        """Prevent new commands while Store atomically deletes task history."""
        with self._lock:
            if any(command.startswith(task_id + ":") for command in self._active):
                raise ValueError("Stop running terminal commands before deleting this task")
            self._deleting.add(task_id)

    def finish_delete(self, task_id: str) -> None:
        with self._lock:
            self._deleting.discard(task_id)

    @staticmethod
    def _row(row) -> dict:
        data = json.loads(row["data"])
        return {"seq": row["seq"], "id": row["id"], "task_id": row["task_id"], "command_id": row["command_id"],
                "order": row["event_order"], "time": row["time"], "kind": row["kind"], **data}

    def route(self, method: str, raw_path: str, body=None):
        """Dispatch `/api/tasks/<id>/bash/...`; return None for other paths."""
        split = urlsplit(raw_path)
        parts = [part for part in split.path.split("/") if part]
        if len(parts) < 4 or parts[:2] != ["api", "tasks"] or parts[3] != "bash":
            return None
        task_id = parts[2]
        query = {key: values[0] for key, values in parse_qs(split.query, keep_blank_values=True).items()}
        try:
            if method == "POST" and parts[4:] == ["execute"]:
                return 200, self.execute(task_id, body)
            if method == "POST" and parts[4:] == ["start"]:
                return 202, self.start(task_id, body)
            if method == "POST" and len(parts) == 7 and parts[4] == "commands" and parts[6] == "stop":
                return 200, self.stop(task_id, parts[5])
            if method == "GET" and parts[4:] == ["events"]:
                return 200, self.search(task_id, query)
            if method == "GET" and len(parts) == 6 and parts[4] == "events":
                event = self.get(task_id, parts[5])
                return (200, event) if event else (404, {"error": "Terminal event not found"})
            if method == "DELETE" and parts[4:] == ["events"]:
                return 200, self.clear(task_id)
            return 404, {"error": "Not found"}
        except PermissionError as exc:
            return 403, {"error": str(exc)}
        except KeyError:
            return 404, {"error": "Task or command not found"}
        except (ValueError, TypeError, TimeoutError) as exc:
            return 400, {"error": str(exc)}
