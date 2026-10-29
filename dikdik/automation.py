"""In-process automation scheduler owned by Engine lifecycle.

Each run is one durable task submission via `engine.submit()`, made with the mode and
approvals the user chose when creating the automation: a schedule never grants
consent on its own. Schedules may be interval, daily, five-field cron, or a local Git
revision change. Persistence lives in the `automations` table of the shared Store; run
intents survive restarts so that a catch-up after restart submits each missed run
exactly once (idempotency key = automation_id + due time). Three consecutive failures
disable the automation and emit an `automation_disabled` event.

The scheduler thread is started with the engine and joined on close().
"""

import json
import subprocess
import re
import threading
import time
import uuid
from pathlib import Path

from .cron import next_after as cron_next_after, parse as parse_cron, timezone as cron_timezone

MAX_CONSECUTIVE_FAILURES = 3
MIN_INTERVAL = 60
ACTIVE_STATES = {"queued", "running"}
BUSY = ("One task at a time", "Another task is running")
DAILY = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, schedule TEXT NOT NULL,
    workspace_path TEXT NOT NULL, objective TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    last_state TEXT, next_run REAL NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
    last_task_id TEXT, last_key TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS automations_runs (
    run_key TEXT PRIMARY KEY, automation_id TEXT NOT NULL,
    task_id TEXT, ran_at REAL
);
CREATE TABLE IF NOT EXISTS automation_drafts (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_draft_runs (
    id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, task_id TEXT NOT NULL,
    created REAL NOT NULL
);
"""


def validate_schedule(schedule) -> dict:
    if not isinstance(schedule, dict):
        raise ValueError("Schedule must be an object")
    if "cron" in schedule:
        if set(schedule) - {"cron", "timezone"}:
            raise ValueError("Cron schedules contain cron and optional timezone only")
        parse_cron(schedule["cron"])
        zone = schedule.get("timezone", "local")
        cron_timezone(zone)
        return {"cron": schedule["cron"], "timezone": zone or "local"}
    if "event" in schedule:
        if set(schedule) != {"event"} or not isinstance(schedule["event"], dict):
            raise ValueError("Event schedules contain event only")
        event = schedule["event"]
        if set(event) - {"type", "repository", "ref"} or event.get("type") != "git_commit":
            raise ValueError("Only git_commit event schedules are supported")
        repository, ref = event.get("repository", "."), event.get("ref", "HEAD")
        if not isinstance(repository, str) or not repository.strip() or len(repository) > 260 or any(ord(c) < 32 for c in repository):
            raise ValueError("Event repository must be a path of at most 260 characters")
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 200 or ref.startswith("-") or any(ord(c) < 33 for c in ref):
            raise ValueError("Event ref must be a safe Git revision")
        return {"event": {"type": "git_commit", "repository": repository.strip(), "ref": ref.strip()}}
    if len(schedule) != 1 or set(schedule) - {"interval_seconds", "daily_at"}:
        raise ValueError("Schedule must contain interval_seconds, daily_at, cron, or event")
    if "daily_at" in schedule:
        if not isinstance(schedule["daily_at"], str) or not DAILY.match(schedule["daily_at"]):
            raise ValueError("Daily time must be HH:MM in 24-hour time")
        return {"daily_at": schedule["daily_at"]}
    interval = schedule["interval_seconds"]
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < MIN_INTERVAL:
        raise ValueError("Interval must be an integer of at least 60 seconds")
    return {"interval_seconds": interval}


def validate_run(body: dict) -> dict:
    """What each run submits. The approvals are the user's, given now, not implied later."""
    mode = body.get("mode", "agent")
    verification = body.get("verification", "syntax")
    if mode not in {"model", "agent"}:
        raise ValueError("Scheduled runs use agent or single-proposal mode")
    if verification not in {"syntax", "docker"}:
        raise ValueError("Choose syntax or docker verification")
    if body.get("approve_external") is not True:
        raise ValueError("Approve sending code to your model provider for these scheduled runs")
    if verification == "docker" and body.get("approve_execution") is not True:
        raise ValueError("Approve running tests in Docker for these scheduled runs")
    run = {"mode": mode, "verification": verification, "approve_external": True, "approve_execution": verification == "docker"}
    profile = body.get("agent_profile")
    if profile not in (None, ""):
        if not isinstance(profile, str) or mode != "agent":
            raise ValueError("An agent profile applies to agent mode only")
        run["agent_profile"] = profile
    return run


def validate_repositories(value, workspace: str = "") -> list[str]:
    if value is None:
        return [workspace] if workspace else []
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ValueError("Repositories must contain 1–10 paths")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 260 or any(ord(c) < 32 for c in item):
            raise ValueError("Repository paths must contain 1–260 printable characters")
        if item.strip() not in result:
            result.append(item.strip())
    return result


def validate_timeout(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 60 <= value <= 86400:
        raise ValueError("Timeout must be an integer from 60 to 86400 seconds")
    return value


def next_after(schedule: dict, after: float) -> float:
    """The first scheduled time strictly after `after`."""
    if "interval_seconds" in schedule:
        return after + schedule["interval_seconds"]
    if "cron" in schedule:
        return cron_next_after(schedule["cron"], after, schedule.get("timezone", "local"))
    if "event" in schedule:
        return after + MIN_INTERVAL
    hour, minute = map(int, schedule["daily_at"].split(":"))
    day = time.localtime(after)
    candidate = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, hour, minute, 0, 0, 0, -1))
    while candidate <= after:
        day = time.localtime(candidate + 86400)
        candidate = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, hour, minute, 0, 0, 0, -1))
    return candidate


class AutomationScheduler:
    def __init__(self, engine, poll_seconds: float = 1.0, clock=time.time):
        self.engine = engine
        self.store = engine.store
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.started_at = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        with self.store.connect() as db:
            db.executescript(_SCHEMA)
            # Added after the first release: the run request (mode + approvals). Old rows
            # have none and stay paused until the user edits them and approves.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(automations)")}
            migrations = (
                ("request", "TEXT"), ("repositories", "TEXT"),
                ("timeout_seconds", "INTEGER NOT NULL DEFAULT 1800"),
                ("source", "TEXT NOT NULL DEFAULT 'custom'"), ("event_cursor", "TEXT"),
                ("updated", "REAL"),
            )
            for name, kind in migrations:
                if name not in columns:
                    db.execute(f"ALTER TABLE automations ADD COLUMN {name} {kind}")
            db.execute("UPDATE automations SET updated=created WHERE updated IS NULL")

    # --- lifecycle -----------------------------------------------------
    def start(self):
        self.started_at = self.clock()
        self._thread = threading.Thread(target=self._loop, name="dikdik-automation", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.poll_seconds * 4, 2.0))

    def _loop(self):
        while not self._stop.wait(self.poll_seconds):
            try:
                self.tick()
            except Exception:
                pass  # never kill the thread; the next tick retries

    def _event_revision(self, schedule: dict) -> str | None:
        """Resolve a local Git event without invoking a shell or any hooks."""
        if "event" not in schedule:
            return None
        event = schedule["event"]
        root = Path(getattr(self.engine, "root", "")).resolve()
        repository = (root / event["repository"]).resolve()
        try:
            repository.relative_to(root)
        except ValueError:
            raise ValueError("Event repository must stay inside the workspace") from None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", event["ref"]], cwd=repository,
                capture_output=True, text=True, timeout=5, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Could not inspect the event repository") from exc
        revision = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
            raise ValueError("Event repository or Git revision is not available")
        return revision.lower()

    # --- CRUD API --------------------------------------------------------
    def list_automations(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM automations ORDER BY created").fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"], "name": r["name"], "schedule": json.loads(r["schedule"]),
                "workspace_path": r["workspace_path"], "objective": r["objective"],
                "enabled": bool(r["enabled"]), "last_state": r["last_state"],
                "next_run": r["next_run"], "failures": r["failures"],
                "run": json.loads(r["request"]) if r["request"] else None,
                "last_task_id": r["last_task_id"],
                "repositories": json.loads(r["repositories"]) if r["repositories"] else [r["workspace_path"]],
                "timeout_seconds": r["timeout_seconds"], "source": r["source"],
                "created": r["created"], "updated": r["updated"] or r["created"],
            })
        return 200, result

    def _fields(self, body, creating: bool) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Body must be a JSON object")
        fields = {}
        if creating or "name" in body:
            name = body.get("name")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
                raise ValueError("Name must contain 1–64 characters")
            fields["name"] = name.strip()
        if creating or "objective" in body:
            objective = body.get("objective")
            if not isinstance(objective, str) or not objective.strip() or len(objective) > 4000:
                raise ValueError("Objective must contain 1–4000 characters")
            fields["objective"] = objective.strip()
        if creating or "schedule" in body:
            fields["schedule"] = validate_schedule(body.get("schedule"))
        if creating or {"mode", "verification", "approve_external", "approve_execution", "agent_profile"} & set(body):
            fields["run"] = validate_run(body)
        if "enabled" in body or creating:
            enabled = body.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError("Enabled must be a boolean")
            fields["enabled"] = enabled
        if creating or "repositories" in body:
            fields["repositories"] = validate_repositories(body.get("repositories"), str(getattr(self.engine, "root", "")))
        if creating or "timeout_seconds" in body:
            fields["timeout_seconds"] = validate_timeout(body.get("timeout_seconds", 1800))
        if creating or "source" in body:
            source = body.get("source", "custom")
            if source not in {"custom", "template", "import"}:
                raise ValueError("Source must be custom, template, or import")
            fields["source"] = source
        return fields

    def create(self, body):
        fields = self._fields(body, creating=True)
        automation_id = uuid.uuid4().hex
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM automations WHERE name=?", (fields["name"],)).fetchone():
                raise ValueError("An automation with this name already exists")
            now = self.clock()
            if self.started_at is None:
                self.started_at = now
            next_run = next_after(fields["schedule"], max(self.started_at, now))
            cursor = self._event_revision(fields["schedule"]) if "event" in fields["schedule"] else None
            db.execute("INSERT INTO automations(id,name,schedule,workspace_path,objective,enabled,last_state,next_run,failures,last_task_id,last_key,created,request,repositories,timeout_seconds,source,event_cursor,updated) VALUES(?,?,?,?,?,?,NULL,?,0,NULL,NULL,?,?,?,?,?,?,?)",
                       (automation_id, fields["name"], json.dumps(fields["schedule"]), str(getattr(self.engine, "root", "")),
                        fields["objective"], int(fields["enabled"]), next_run, now, json.dumps(fields["run"]),
                        json.dumps(fields["repositories"]), fields["timeout_seconds"], fields["source"], cursor, now))
        return 201, {"id": automation_id, "next_run": next_run}

    def update(self, automation_id: str, body):
        fields = self._fields(body, creating=False)
        if not fields:
            raise ValueError("Nothing to update")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
            if row is None:
                raise ValueError(f"Automation not found (id={automation_id})")
            if "name" in fields and db.execute("SELECT 1 FROM automations WHERE name=? AND id<>?", (fields["name"], automation_id)).fetchone():
                raise ValueError("An automation with this name already exists")
            for key in ("name", "objective"):
                if key in fields:
                    db.execute(f"UPDATE automations SET {key}=? WHERE id=?", (fields[key], automation_id))
            if "enabled" in fields:
                db.execute("UPDATE automations SET enabled=?, failures=CASE WHEN ? THEN 0 ELSE failures END WHERE id=?", (int(fields["enabled"]), int(fields["enabled"]), automation_id))
            if "run" in fields:
                db.execute("UPDATE automations SET request=?, last_state=CASE WHEN last_state='needs_setup' THEN NULL ELSE last_state END WHERE id=?", (json.dumps(fields["run"]), automation_id))
            if "schedule" in fields:
                cursor = self._event_revision(fields["schedule"]) if "event" in fields["schedule"] else None
                db.execute("UPDATE automations SET schedule=?, next_run=?, event_cursor=? WHERE id=?", (json.dumps(fields["schedule"]), next_after(fields["schedule"], self.clock()), cursor, automation_id))
            if "repositories" in fields:
                db.execute("UPDATE automations SET repositories=? WHERE id=?", (json.dumps(fields["repositories"]), automation_id))
            if "timeout_seconds" in fields:
                db.execute("UPDATE automations SET timeout_seconds=? WHERE id=?", (fields["timeout_seconds"], automation_id))
            if "source" in fields:
                db.execute("UPDATE automations SET source=? WHERE id=?", (fields["source"], automation_id))
            db.execute("UPDATE automations SET updated=? WHERE id=?", (self.clock(), automation_id))
        return 200, {"id": automation_id}

    def toggle(self, automation_id: str, body):
        if not isinstance(body, dict) or "enabled" not in body:
            raise ValueError("Body must set enabled")
        enabled = body["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError("Enabled must be a boolean")
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM automations WHERE id=?", (automation_id,)).fetchone():
                raise ValueError(f"Automation not found (id={automation_id})")
            db.execute("UPDATE automations SET enabled=? WHERE id=?", (int(enabled), automation_id))
        return 200, {"id": automation_id, "enabled": enabled}

    def delete(self, automation_id: str):
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM automations WHERE id=?", (automation_id,)).fetchone():
                raise ValueError(f"Automation not found (id={automation_id})")
            db.execute("DELETE FROM automations WHERE id=?", (automation_id,))
            db.execute("DELETE FROM automations_runs WHERE automation_id=?", (automation_id,))
        return 200, {"deleted": automation_id}

    def runs(self, automation_id: str):
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM automations WHERE id=?", (automation_id,)).fetchone():
                raise ValueError(f"Automation not found (id={automation_id})")
            rows = db.execute(
                "SELECT r.task_id, r.ran_at AS created, t.state FROM automations_runs r LEFT JOIN tasks t ON t.id=r.task_id WHERE r.automation_id=? ORDER BY r.ran_at DESC LIMIT 100",
                (automation_id,)).fetchall()
        return 200, [{"task_id": r["task_id"], "created": r["created"], "state": r["state"]} for r in rows]

    def run_detail(self, automation_id: str, task_id: str):
        with self.store.connect() as db:
            linked = db.execute("SELECT 1 FROM automations_runs WHERE automation_id=? AND task_id=?", (automation_id, task_id)).fetchone()
        if linked is None:
            raise ValueError("Automation run not found")
        try:
            task = self.store.get(task_id)
        except KeyError:
            raise ValueError("Automation run task not found") from None
        # Requests and event payloads are bounded, but omit approval flags from the
        # detail response because they add no diagnostic value.
        request = {key: value for key, value in task["request"].items() if not key.startswith("approve_")}
        return 200, {"task_id": task_id, "created": task["created"], "state": task["state"],
                     "request": request, "result": task["result"], "events": task["events"][-200:]}

    def export(self, automation_id: str):
        rows = [row for row in self.list_automations()[1] if row["id"] == automation_id]
        if not rows:
            raise ValueError(f"Automation not found (id={automation_id})")
        row = rows[0]
        spec = {key: row[key] for key in ("name", "schedule", "objective", "repositories", "timeout_seconds")}
        spec.update(row["run"] or {})
        return 200, {"version": 1, "kind": "dikdik-automation", "spec": spec}

    def import_spec(self, body):
        if not isinstance(body, dict) or body.get("version") != 1 or body.get("kind") != "dikdik-automation" or not isinstance(body.get("spec"), dict):
            raise ValueError("Import must be a version 1 dikdik-automation document")
        spec = dict(body["spec"])
        spec["enabled"] = False
        spec["source"] = "import"
        return self.create(spec)

    def _draft_errors(self, spec) -> list[str]:
        try:
            self._fields(spec, creating=True)
        except (TypeError, ValueError) as exc:
            return [str(exc)]
        return []

    def list_drafts(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM automation_drafts ORDER BY updated DESC").fetchall()
        return 200, [{"id": r["id"], "name": r["name"], "spec": json.loads(r["spec"]),
                      "errors": self._draft_errors(json.loads(r["spec"])), "created": r["created"], "updated": r["updated"]} for r in rows]

    def save_draft(self, body, draft_id: str | None = None):
        if not isinstance(body, dict) or not isinstance(body.get("spec"), dict):
            raise ValueError("Draft must contain a spec object")
        encoded = json.dumps(body["spec"])
        if len(encoded.encode()) > 65536:
            raise ValueError("Draft is larger than 64 KiB")
        name = body.get("name") or body["spec"].get("name") or "Untitled draft"
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
            raise ValueError("Draft name must contain 1–64 characters")
        now = self.clock()
        with self.store.connect() as db:
            if draft_id:
                if not db.execute("SELECT 1 FROM automation_drafts WHERE id=?", (draft_id,)).fetchone():
                    raise ValueError("Automation draft not found")
                db.execute("UPDATE automation_drafts SET name=?,spec=?,updated=? WHERE id=?", (name.strip(), encoded, now, draft_id))
                status = 200
            else:
                draft_id, status = uuid.uuid4().hex, 201
                db.execute("INSERT INTO automation_drafts VALUES(?,?,?,?,?)", (draft_id, name.strip(), encoded, now, now))
        return status, {"id": draft_id, "name": name.strip(), "spec": body["spec"], "errors": self._draft_errors(body["spec"]), "updated": now}

    def delete_draft(self, draft_id: str):
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM automation_drafts WHERE id=?", (draft_id,)).fetchone():
                raise ValueError("Automation draft not found")
            db.execute("DELETE FROM automation_drafts WHERE id=?", (draft_id,))
            db.execute("DELETE FROM automation_draft_runs WHERE draft_id=?", (draft_id,))
        return 200, {"deleted": draft_id}

    def test_draft(self, draft_id: str):
        with self.store.connect() as db:
            row = db.execute("SELECT spec FROM automation_drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            raise ValueError("Automation draft not found")
        fields = self._fields(json.loads(row["spec"]), creating=True)
        task_id = self.engine.submit({"objective": fields["objective"], **fields["run"]})
        run_id, now = uuid.uuid4().hex, self.clock()
        with self.store.connect() as db:
            db.execute("INSERT INTO automation_draft_runs VALUES(?,?,?,?)", (run_id, draft_id, task_id, now))
        return 202, {"task_id": task_id, "draft_id": draft_id}

    def run_now(self, automation_id: str):
        """Start one run immediately; the schedule is unchanged."""
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
        if row is None:
            raise ValueError(f"Automation not found (id={automation_id})")
        auto = dict(row)
        if not auto["request"]:
            raise ValueError("Edit this automation to choose its mode and approvals first")
        if auto["last_task_id"] and self._task_state(auto["last_task_id"]) in ACTIVE_STATES:
            raise ValueError("This automation is already running")
        task_id = self.engine.submit(self._submit_request(auto))
        now = self.clock()
        with self.store.connect() as db:
            db.execute("INSERT INTO automations_runs(run_key,automation_id,task_id,ran_at) VALUES(?,?,?,?)", (f"{automation_id}|manual|{now:.6f}", automation_id, task_id, now))
            db.execute("UPDATE automations SET last_task_id=?, last_state='queued' WHERE id=?", (task_id, automation_id))
        return 202, {"task_id": task_id}

    # --- scheduler core --------------------------------------------------
    def _rows(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM automations WHERE enabled=1 ORDER BY next_run")]

    def _task_state(self, task_id: str | None):
        if not task_id:
            return None
        with self.store.connect() as db:
            row = db.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
        return row["state"] if row else None

    def _task_info(self, task_id: str | None):
        if not task_id:
            return None
        with self.store.connect() as db:
            return db.execute("SELECT state,created FROM tasks WHERE id=?", (task_id,)).fetchone()

    def _record_result(self, automation_id: str, task_id: str | None, state: str) -> bool:
        """Record a finished run; return True if the automation is still enabled."""
        with self.store.connect() as db:
            row = db.execute("SELECT failures FROM automations WHERE id=?", (automation_id,)).fetchone()
            if row is None:
                return False
            failures = row["failures"]
            if state == "completed":
                failures = 0
            else:
                failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                db.execute("UPDATE automations SET failures=?, last_state=?, enabled=0, last_task_id=NULL WHERE id=?", (failures, state, automation_id))
                if task_id:
                    db.execute("INSERT INTO events(task_id,time,kind,data) VALUES(?,?,?,?)",
                               (task_id, time.time(), "automation_disabled", json.dumps({"automation": automation_id, "consecutive_failures": failures, "state": state})))
                return False
            db.execute("UPDATE automations SET failures=?, last_state=?, last_task_id=NULL WHERE id=?", (failures, state, automation_id))
            return True

    def _submit_request(self, auto: dict) -> dict:
        return {"objective": auto["objective"], **json.loads(auto["request"])}

    def tick(self):
        now = self.clock()
        for auto in self._rows():
            if not auto["request"]:
                # Made before runs stored their own approvals: wait for the user to edit it.
                if auto["last_state"] != "needs_setup":
                    with self.store.connect() as db:
                        db.execute("UPDATE automations SET last_state='needs_setup' WHERE id=?", (auto["id"],))
                continue
            # Harvest a finished run before considering a new one (single flight:
            # while a task is queued/running, never submit a second one).
            if auto["last_task_id"]:
                task = self._task_info(auto["last_task_id"])
                state = task["state"] if task else None
                if state is None or state in ACTIVE_STATES:
                    if task and state in ACTIVE_STATES and now - task["created"] >= auto["timeout_seconds"] and auto["last_state"] != "cancelling":
                        cancel = getattr(self.engine, "cancel", None)
                        if callable(cancel) and cancel(auto["last_task_id"]):
                            with self.store.connect() as db:
                                db.execute("UPDATE automations SET last_state='cancelling' WHERE id=?", (auto["id"],))
                            self.store.event(auto["last_task_id"], "automation_timeout", {"automation": auto["id"], "timeout_seconds": auto["timeout_seconds"]})
                    continue
                if not self._record_result(auto["id"], auto["last_task_id"], state):
                    continue  # automation was disabled by this harvest; do not run
            if now < auto["next_run"]:
                continue
            schedule = json.loads(auto["schedule"])
            if "event" in schedule:
                self._poll_event(auto, schedule, now)
                continue
            self._run(auto, now)

    def _poll_event(self, auto: dict, schedule: dict, now: float):
        try:
            revision = self._event_revision(schedule)
        except ValueError:
            with self.store.connect() as db:
                db.execute("UPDATE automations SET next_run=? WHERE id=?", (now + MIN_INTERVAL, auto["id"]))
            self._record_result(auto["id"], None, "invalid")
            return
        if revision == auto["event_cursor"]:
            with self.store.connect() as db:
                db.execute("UPDATE automations SET next_run=? WHERE id=?", (now + MIN_INTERVAL, auto["id"]))
            return
        key = f"{auto['id']}|git|{revision}"
        with self.store.connect() as db:
            existing = db.execute("SELECT task_id FROM automations_runs WHERE run_key=?", (key,)).fetchone()
        if existing is not None:
            with self.store.connect() as db:
                db.execute("UPDATE automations SET event_cursor=?,next_run=?,last_task_id=COALESCE(last_task_id,?) WHERE id=?",
                           (revision, now + MIN_INTERVAL, existing["task_id"], auto["id"]))
            return
        try:
            task_id = self.engine.submit(self._submit_request(auto))
        except ValueError as exc:
            if str(exc).startswith(BUSY):
                return
            with self.store.connect() as db:
                db.execute("UPDATE automations SET event_cursor=?,next_run=? WHERE id=?", (revision, now + MIN_INTERVAL, auto["id"]))
            self._record_result(auto["id"], None, "invalid")
            return
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO automations_runs(run_key,automation_id,task_id,ran_at) VALUES(?,?,?,?)", (key, auto["id"], task_id, now))
            db.execute("UPDATE automations SET event_cursor=?,next_run=?,last_task_id=?,last_key=?,last_state='queued' WHERE id=?",
                       (revision, now + MIN_INTERVAL, task_id, key, auto["id"]))

    def _run(self, auto: dict, now: float):
        due = auto["next_run"]
        schedule = json.loads(auto["schedule"])
        key = f"{auto['id']}|{due:.6f}"
        with self.store.connect() as db:
            intent = db.execute("SELECT task_id FROM automations_runs WHERE run_key=?", (key,)).fetchone()
        if intent is None:
            try:
                task_id = self.engine.submit(self._submit_request(auto))
            except ValueError as exc:
                if str(exc).startswith(BUSY):
                    return  # engine busy (single flight); retry next tick
                # An invalid run (e.g. its agent profile was deleted) counts as a failure.
                with self.store.connect() as db:
                    db.execute("UPDATE automations SET next_run=? WHERE id=?", (next_after(schedule, max(due, now)), auto["id"]))
                self._record_result(auto["id"], None, "invalid")
                return
            with self.store.connect() as db:
                db.execute("INSERT OR IGNORE INTO automations_runs(run_key,automation_id,task_id,ran_at) VALUES(?,?,?,?)", (key, auto["id"], task_id, now))
                # Catch-up: a run behind the restart submits once, then aligns to now.
                epoch = self.started_at if self.started_at is not None else now
                base = max(epoch, now) if due < epoch else due
                db.execute("UPDATE automations SET last_task_id=?, last_key=?, last_state='queued', next_run=? WHERE id=?",
                           (task_id, key, next_after(schedule, base), auto["id"]))
            return
        # Restart/interrupt recovery: intent exists, never submit twice.
        with self.store.connect() as db:
            db.execute("UPDATE automations SET next_run=?, last_task_id=COALESCE(last_task_id, ?) WHERE id=?",
                       (max(next_after(schedule, due), now), intent["task_id"], auto["id"]))
