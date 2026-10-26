import json
import time
import unittest

from dikdik.automation import AutomationScheduler, next_after, validate_schedule
from tests.test_skills_automation import Harness

BASE = {"name": "nightly", "schedule": {"interval_seconds": 60}, "objective": "Tidy up"}


class AutomationRunTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.sched = AutomationScheduler(self.h, poll_seconds=0.01, clock=self.h.clock)

    def test_runs_need_the_users_approvals(self):
        with self.assertRaisesRegex(ValueError, "Approve sending code"):
            self.sched.create(BASE)
        with self.assertRaisesRegex(ValueError, "Docker"):
            self.sched.create({**BASE, "approve_external": True, "verification": "docker"})
        with self.assertRaisesRegex(ValueError, "agent or single-proposal"):
            self.sched.create({**BASE, "approve_external": True, "mode": "offline"})

    def test_each_run_submits_exactly_what_was_approved(self):
        self.sched.create({**BASE, "approve_external": True, "mode": "agent", "verification": "docker", "approve_execution": True, "agent_profile": "p1"})
        self.h.clock.advance(61)
        self.sched.tick()
        self.assertEqual(self.h.submitted[0][1], {"objective": "Tidy up", "mode": "agent", "verification": "docker",
                                                   "approve_external": True, "approve_execution": True, "agent_profile": "p1"})
        row = self.sched.list_automations()[1][0]
        self.assertEqual((row["run"]["mode"], row["last_task_id"]), ("agent", self.h.submitted[0][0]))

    def test_old_automations_wait_for_setup(self):
        created = self.sched.create({**BASE, "approve_external": True})[1]
        with self.h.store.connect() as db:
            db.execute("UPDATE automations SET request=NULL WHERE id=?", (created["id"],))
        self.h.clock.advance(120)
        self.sched.tick()
        self.assertEqual(self.h.submitted, [])
        self.assertEqual(self.sched.list_automations()[1][0]["last_state"], "needs_setup")
        with self.assertRaisesRegex(ValueError, "Edit this automation"):
            self.sched.run_now(created["id"])
        self.sched.update(created["id"], {"approve_external": True, "mode": "model"})
        self.sched.tick()
        self.assertEqual(self.h.submitted[0][1]["mode"], "model")

    def test_daily_schedule(self):
        self.assertEqual(validate_schedule({"daily_at": "07:30"}), {"daily_at": "07:30"})
        for bad in ({"daily_at": "7:30"}, {"daily_at": "24:00"}, {"daily_at": "07:30", "interval_seconds": 60}, {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_schedule(bad)
        start = time.mktime((2026, 10, 26, 6, 0, 0, 0, 0, -1))
        first = next_after({"daily_at": "07:30"}, start)
        self.assertEqual(time.localtime(first)[:5], (2026, 10, 26, 7, 30))
        self.assertEqual(time.localtime(next_after({"daily_at": "07:30"}, first))[:5], (2026, 10, 27, 7, 30))

    def test_edit_and_run_now(self):
        created = self.sched.create({**BASE, "approve_external": True})[1]
        self.sched.update(created["id"], {"name": "renamed", "objective": "New goal", "schedule": {"interval_seconds": 3600}})
        row = self.sched.list_automations()[1][0]
        self.assertEqual((row["name"], row["objective"], row["schedule"]), ("renamed", "New goal", {"interval_seconds": 3600}))
        status, started = self.sched.run_now(created["id"])
        self.assertEqual((status, self.h.submitted[0][1]["objective"]), (202, "New goal"))
        with self.assertRaisesRegex(ValueError, "already running"):
            self.sched.run_now(created["id"])
        runs = self.sched.runs(created["id"])[1]
        self.assertEqual(runs[0]["task_id"], started["task_id"])
        self.assertEqual(self.sched.list_automations()[1][0]["next_run"], row["next_run"])  # schedule unchanged
        with self.assertRaises(ValueError):
            self.sched.update(created["id"], {})

    def test_invalid_runs_count_as_failures_instead_of_retrying_forever(self):
        def reject(request):
            raise ValueError("That agent profile no longer exists; pick another")

        self.h.submit = reject
        self.sched.create({**BASE, "approve_external": True})
        for _ in range(3):
            self.h.clock.advance(61)
            self.sched.tick()
        row = self.sched.list_automations()[1][0]
        self.assertEqual((row["enabled"], row["failures"], row["last_state"]), (False, 3, "invalid"))
