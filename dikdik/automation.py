"""In-process automation scheduler owned by Engine lifecycle.

Each run is one durable task submission via `engine.submit()`, made with the mode and
approvals the user chose when creating the automation: a schedule never grants
consent on its own. Schedules are `{interval_seconds >= 60}` or `{daily_at: "HH:MM"}`
(local time). Persistence lives in the `automations` table of the shared Store; run
intents survive restarts so that a catch-up after restart submits each missed run
exactly once (idempotency key = automation_id + due time). Three consecutive failures
disable the automation and emit an `automation_disabled` event.

The scheduler thread is started with the engine and joined on close().
"""

import json
import re
import threading
import time
import uuid

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
"""


def validate_schedule(schedule) -> dict:
    if not isinstance(schedule, dict) or len(schedule) != 1 or set(schedule) - {"interval_seconds", "daily_at"}:
        raise ValueError("Schedule must contain interval_seconds or daily_at only")
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


def next_after(schedule: dict, after: float) -> float:
    """The first scheduled time strictly after `after`."""
    if "interval_seconds" in schedule:
        return after + schedule["interval_seconds"]
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
            if "request" not in {row["name"] for row in db.execute("PRAGMA table_info(automations)")}:
                db.execute("ALTER TABLE automations ADD COLUMN request TEXT")

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
            db.execute("INSERT INTO automations(id,name,schedule,workspace_path,objective,enabled,last_state,next_run,failures,last_task_id,last_key,created,request) VALUES(?,?,?,?,?,?,NULL,?,0,NULL,NULL,?,?)",
                       (automation_id, fields["name"], json.dumps(fields["schedule"]), str(getattr(self.engine, "root", "")),
                        fields["objective"], int(fields["enabled"]), next_run, now, json.dumps(fields["run"])))
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
                db.execute("UPDATE automations SET schedule=?, next_run=? WHERE id=?", (json.dumps(fields["schedule"]), next_after(fields["schedule"], self.clock()), automation_id))
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
                state = self._task_state(auto["last_task_id"])
                if state is None or state in ACTIVE_STATES:
                    continue
                if not self._record_result(auto["id"], auto["last_task_id"], state):
                    continue  # automation was disabled by this harvest; do not run
            if now < auto["next_run"]:
                continue
            self._run(auto, now)

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
