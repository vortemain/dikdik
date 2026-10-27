"""Skills enumeration + persistence, and automation scheduler with fake clocks."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dikdik.automation import AutomationScheduler
from dikdik.server_routes import build_routes
from dikdik.skills import SKILL_UNTRUSTED_MARKER, SkillIndex
from dikdik.store import Store


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Harness:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "ws"
        data = Path(self.tmp.name) / "data"
        (root / ".dikdik" / "skills").mkdir(parents=True)
        (data / "skills").mkdir(parents=True)
        self.root, self.data = root, data
        self.store = Store(data / "state.sqlite3")
        self.clock = FakeClock()
        self.submitted = []
        # by default, submission "succeeds" but the task never finishes

    def submit(self, request):
        # Use the real Store-issued task id so finish()/_task_state can find it.
        task_id = self.store.create(dict(request))
        self.submitted.append((task_id, request))
        return task_id

    def finish(self, task_id, state="completed"):
        self.store.state(task_id, state, {})

    def close(self):
        self.tmp.cleanup()


class SkillTests(unittest.TestCase, Harness):
    def setUp(self):
        Harness.__init__(self)

    def tearDown(self):
        self.close()

    def index(self):
        return SkillIndex(self.store, self.data / "skills", self.root / ".dikdik" / "skills")

    def engine(self):
        class Stores:
            pass
        stores = Stores()
        stores.store = self.store
        stores.data = self.data
        stores.root = self.root
        stores.automation = None
        return build_routes(stores)

    def test_enumeration_scopes(self):
        (self.data / "skills" / "alpha.md").write_bytes(b"# personal alpha")
        (self.root / ".dikdik" / "skills" / "beta.md").write_bytes(b"project beta")
        rows = {r["id"]: r for r in self.index().enumerate_skills()}
        self.assertEqual(rows["alpha"]["scope"], "personal")
        self.assertEqual(rows["beta"]["scope"], "project")
        self.assertEqual(set(rows["alpha"]), {"id", "name", "description", "scope", "enabled", "path"})
        self.assertTrue(rows["alpha"]["enabled"])  # enumerated data defaults on

    def test_untrusted_marker_constant(self):
        self.assertIn("untrusted", SKILL_UNTRUSTED_MARKER)
        self.assertIn("data", SKILL_UNTRUSTED_MARKER)
        # The marker must reference isolation, never execution/permission grants.
        self.assertIn("never instructions", SKILL_UNTRUSTED_MARKER)

    def test_disabled_persisted_in_settings(self):
        (self.data / "skills" / "gamma.md").write_bytes(b"text")
        routes = self.engine()
        index = self.index()
        status, _ = index.set_enabled("gamma", False)
        self.assertEqual(status, 200)
        rows = index.enumerate_skills()
        self.assertEqual([r["enabled"] for r in rows], [False])
        status, _ = routes("PUT", "/api/skills/gamma", {"enabled": True})
        self.assertEqual(status, 200)
        self.assertEqual(index.enumerate_skills()[0]["enabled"], True)
        # persistence survives a fresh store handle
        fresh = SkillIndex(self.store, self.data / "skills", self.root / ".dikdik" / "skills")
        self.assertTrue(fresh.find("gamma")["enabled"])

    def test_traversal_ids_rejected(self):
        (self.data / "skills" / "x.md").write_bytes(b"x")
        index = self.index()
        self.assertIsNone(index.find("..%2fx"))
        self.assertIsNone(index.find("../../etc/passwd"))
        with self.assertRaises(KeyError):
            index.set_enabled("../secret", True)

    def test_http_enabled_toggle_validation(self):
        (self.root / ".dikdik" / "skills" / "omega.md").write_bytes(b"text")
        routes = self.engine()
        self.assertEqual(routes("PUT", "/api/skills/omega", {"enabled": False})[0], 200)
        self.assertEqual(routes("PUT", "/api/skills/omega", {"enabled": "yes"})[0], 400)
        self.assertEqual(routes("PUT", "/api/skills/omega", {})[0], 400)
        self.assertEqual(routes("PUT", "/api/skills/ghost", {"enabled": True})[0], 404)
        status, rows = routes("GET", "/api/skills", None)
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "omega")


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.engine = self.h

    def tearDown(self):
        self.h.close()

    def scheduler(self, poll=0.01):
        return AutomationScheduler(self.engine, poll_seconds=poll, clock=self.h.clock)

    def make(self, sched, **body):
        payload = {"name": "n", "schedule": {"interval_seconds": 60}, "objective": "obj", "approve_external": True, **body}
        status, created = sched.create(payload)
        self.assertEqual(status, 201)
        return created

    # --- validation ------------------------------------------------------
    def test_interval_gating_minimum_60(self):
        sched = self.scheduler()
        for bad in (59, 0, "60", None, {"interval_seconds": 60, "daily": "07:00"}):
            with self.assertRaises(ValueError):
                sched.create({"name": "n", "schedule": bad, "objective": "o", "approve_external": True})
        self.assertEqual(self.make(sched)["next_run"], 60)

    # --- scheduler core -----------------------------------------------------
    def test_interval_gating_with_fake_clock(self):
        sched = self.scheduler()
        created = self.make(sched)
        self.h.clock.advance(30)
        sched.tick()
        self.assertEqual(self.h.submitted, [])  # not due yet
        self.h.clock.advance(31)  # now 61 >= next_run 60
        sched.tick()
        self.assertEqual(len(self.h.submitted), 1)
        # second tick at the same due time must not re-submit (exactly once)
        sched.tick()
        self.assertEqual(len(self.h.submitted), 1)
        # automation.next_run stays aligned with the interval after the catch-up
        status, rows = sched.list_automations()
        self.assertGreaterEqual(rows[0]["next_run"], 120)

    def test_finish_and_next_interval(self):
        sched = self.scheduler()
        self.make(sched)
        self.h.clock.advance(61)
        sched.tick()  # run 1 at 61
        task_id = self.h.submitted[0][0]
        self.h.finish(task_id)
        self.h.clock.advance(58)  # 1.5 seconds before the next interval boundary
        sched.tick()
        self.assertEqual(len(self.h.submitted), 1)
        self.h.clock.advance(2)  # past the boundary
        sched.tick()
        self.assertEqual(len(self.h.submitted), 2)

    def test_consecutive_failures_disable(self):
        sched = self.scheduler()
        self.make(sched)
        for cycle in range(4):
            if self.h.submitted:
                self.h.finish(self.h.submitted[-1][0], state="failed")
            self.h.clock.advance(61)
            sched.tick()
            if cycle < 3:
                self.assertEqual(len(self.h.submitted), cycle + 1)
                status, rows = sched.list_automations()
                self.assertEqual(rows[0]["enabled"], True, f"premature disable after {cycle + 1} failures")
        status, rows = sched.list_automations()
        self.assertEqual(status, 200)
        self.assertEqual(rows[0]["enabled"], False)
        self.assertEqual(rows[0]["failures"], 3)
        with self.h.store.connect() as db:
            kinds = [r[0] for r in db.execute("SELECT kind FROM events")]
        self.assertIn("automation_disabled", kinds)
        # disabled automation never schedules again
        self.h.clock.advance(600)
        sched.tick()
        self.assertEqual(len(self.h.submitted), 3)

    def test_completed_resets_failures(self):
        sched = self.scheduler()
        self.make(sched)
        for state in ("failed", "completed", "failed", "completed", "failed", "completed"):
            if self.h.submitted:
                self.h.finish(self.h.submitted[-1][0], state)
            self.h.clock.advance(61)
            sched.tick()
        status, rows = sched.list_automations()
        self.assertEqual(rows[0]["enabled"], True)
        self.assertEqual(rows[0]["failures"], 0)

    def test_restart_catchup_exactly_once(self):
        sched = self.scheduler()
        self.make(sched)
        self.h.clock.advance(1000)  # several missed intervals
        sched.started_at = self.h.clock.now  # simulate the restart moment
        sched.tick()
        self.assertEqual(len(self.h.submitted), 1)  # exactly once for catch-up
        self.h.clock.advance(1)
        sched.tick()
        self.assertEqual(len(self.h.submitted), 1)
        status, rows = sched.list_automations()
        self.assertGreaterEqual(rows[0]["next_run"], self.h.clock.now)
        # crash-recovery path: intent row exists without automation link update
        with self.h.store.connect() as db:
            runs = db.execute("SELECT run_key, task_id FROM automations_runs").fetchall()
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0][1] is None)

    def test_busy_engine_retries_without_counting_failure(self):
        original = self.h.submit

        def submit(request):
            if getattr(self.h, "busy", False):
                raise ValueError("One task at a time; wait or cancel the active task")
            return original(request)

        self.h.submit = submit
        sched = self.scheduler()
        self.make(sched)
        self.h.busy = True
        self.h.clock.advance(61)
        sched.tick()
        self.assertEqual(self.h.submitted, [])
        status, rows = sched.list_automations()
        self.assertEqual(rows[0]["failures"], 0)
        self.h.busy = False
        sched.tick()
        self.assertEqual(len(self.h.submitted), 1)

    def test_thread_lifecycle_and_shutdown(self):
        sched = self.scheduler(poll=0.05)
        sched.start()
        self.make(sched)
        sched.close()
        self.assertFalse(sched._thread.is_alive())

    def test_http_automations_endpoints(self):
        class Stores:
            pass
        stores = Stores()
        stores.store = self.h.store
        stores.data = self.h.data
        stores.root = self.h.root
        stores.automation = self.scheduler()
        routes = build_routes(stores)
        status, created = routes("POST", "/api/automations", {"name": "daily", "schedule": {"interval_seconds": 90}, "objective": "Tidy", "enabled": True, "approve_external": True})
        self.assertEqual(status, 201)
        status, rows = routes("GET", "/api/automations", None)
        self.assertEqual(status, 200)
        self.assertEqual(set(rows[0]), {"id", "name", "schedule", "workspace_path", "objective", "enabled", "last_state", "next_run", "failures", "run", "last_task_id",
                                        "repositories", "timeout_seconds", "source", "created", "updated"})
        self.assertEqual(rows[0]["schedule"], {"interval_seconds": 90})
        self.assertEqual(routes("POST", "/api/automations", {"name": "daily", "schedule": {"interval_seconds": 5}, "objective": "x", "enabled": True})[0], 400)
        self.assertEqual(routes("POST", "/api/automations", {"name": "x", "schedule": {"interval_seconds": 61}, "objective": ""})[0], 400)
        self.assertEqual(routes("POST", "/api/automations", "oops")[0], 400)
        automation_id = created["id"]
        self.assertEqual(routes("PUT", f"/api/automations/{automation_id}", {"enabled": False})[0], 200)
        status, rows = routes("GET", "/api/automations", None)
        self.assertEqual(rows[0]["enabled"], False)
        self.assertEqual(routes("PUT", f"/api/automations/{automation_id}", {"objective": "no"})[0], 400)
        self.assertEqual(routes("GET", f"/api/automations/{automation_id}/runs", None)[0], 200)
        self.assertEqual(routes("DELETE", f"/api/automations/{automation_id}", {})[0], 200)
        self.assertEqual(routes("DELETE", f"/api/automations/{automation_id}", {})[0], 400)
        self.assertEqual(routes("GET", f"/api/automations/{automation_id}/runs", None)[0], 400)


if __name__ == "__main__":
    unittest.main()
