"""Expanded automation schedules, portable specs, drafts and run diagnostics."""

import subprocess
import unittest

from dikdik.automation import AutomationScheduler, validate_schedule
from dikdik.server_routes import build_routes
from tests.test_skills_automation import Harness


class AutomationSuiteTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.scheduler = AutomationScheduler(self.h, clock=self.h.clock)

        class Stores:
            pass
        stores = Stores()
        stores.store, stores.data, stores.root = self.h.store, self.h.data, self.h.root
        stores.automation = self.scheduler
        self.routes = build_routes(stores)

    def tearDown(self):
        self.h.close()

    def payload(self, **changes):
        value = {"name": "quality", "objective": "Review changes", "schedule": {"interval_seconds": 60},
                 "approve_external": True, "mode": "agent", "verification": "syntax"}
        value.update(changes)
        return value

    def test_cron_schedule_and_richer_metadata(self):
        schedule = validate_schedule({"cron": "0 9 * * 1-5", "timezone": "UTC"})
        self.assertEqual(schedule["timezone"], "UTC")
        status, made = self.scheduler.create(self.payload(schedule=schedule, repositories=[str(self.h.root)], timeout_seconds=600))
        self.assertEqual(status, 201)
        row = self.scheduler.list_automations()[1][0]
        self.assertEqual(row["repositories"], [str(self.h.root)])
        self.assertEqual(row["timeout_seconds"], 600)
        self.assertGreater(made["next_run"], 0)

    def test_export_import_is_versioned_and_import_starts_disabled(self):
        _, made = self.scheduler.create(self.payload())
        status, document = self.scheduler.export(made["id"])
        self.assertEqual((status, document["version"], document["kind"]), (200, 1, "dikdik-automation"))
        document["spec"]["name"] = "Imported quality"
        status, imported = self.scheduler.import_spec(document)
        self.assertEqual(status, 201)
        rows = {row["id"]: row for row in self.scheduler.list_automations()[1]}
        self.assertFalse(rows[imported["id"]]["enabled"])
        self.assertEqual(rows[imported["id"]]["source"], "import")

    def test_draft_can_be_invalid_then_tested_when_complete(self):
        status, draft = self.scheduler.save_draft({"name": "work", "spec": {"name": "work"}})
        self.assertEqual(status, 201)
        self.assertTrue(draft["errors"])
        status, draft = self.scheduler.save_draft({"spec": self.payload(name="work")}, draft["id"])
        self.assertEqual((status, draft["errors"]), (200, []))
        status, started = self.scheduler.test_draft(draft["id"])
        self.assertEqual(status, 202)
        self.assertEqual(started["task_id"], self.h.submitted[-1][0])

    def test_run_detail_is_scoped_to_automation(self):
        _, made = self.scheduler.create(self.payload())
        status, started = self.scheduler.run_now(made["id"])
        self.assertEqual(status, 202)
        task = started["task_id"]
        self.h.store.event(task, "note", {"message": "checked"})
        status, detail = self.scheduler.run_detail(made["id"], task)
        self.assertEqual((status, detail["task_id"], detail["state"]), (200, task, "queued"))
        self.assertNotIn("approve_external", detail["request"])
        with self.assertRaises(ValueError):
            self.scheduler.run_detail("other", task)

    def test_time_limit_requests_cancellation_and_records_evidence(self):
        _, made = self.scheduler.create(self.payload(timeout_seconds=60))
        _, started = self.scheduler.run_now(made["id"])
        task_id = started["task_id"]
        with self.h.store.connect() as db:
            db.execute("UPDATE tasks SET created=-61 WHERE id=?", (task_id,))
        cancelled = []

        def cancel(task):
            cancelled.append(task)
            self.h.finish(task, "cancelled")
            return True

        self.h.cancel = cancel
        self.scheduler.tick()
        self.assertEqual(cancelled, [task_id])
        self.scheduler.tick()
        row = self.scheduler.list_automations()[1][0]
        self.assertEqual((row["last_state"], row["failures"]), ("cancelled", 1))
        self.assertIn("automation_timeout", [event["kind"] for event in self.h.store.get(task_id)["events"]])

    def test_git_event_runs_once_per_new_commit(self):
        subprocess.run(["git", "init"], cwd=self.h.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "automation@example.test"], cwd=self.h.root, check=True)
        subprocess.run(["git", "config", "user.name", "Automation Test"], cwd=self.h.root, check=True)
        tracked = self.h.root / "tracked.txt"
        tracked.write_text("one", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.h.root, check=True)
        subprocess.run(["git", "commit", "-m", "first"], cwd=self.h.root, check=True, capture_output=True)
        schedule = {"event": {"type": "git_commit", "repository": ".", "ref": "HEAD"}}
        self.scheduler.create(self.payload(schedule=schedule))
        self.h.clock.advance(61)
        self.scheduler.tick()
        self.assertEqual(self.h.submitted, [])
        tracked.write_text("two", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.h.root, check=True)
        subprocess.run(["git", "commit", "-m", "second"], cwd=self.h.root, check=True, capture_output=True)
        self.h.clock.advance(61)
        self.scheduler.tick()
        self.assertEqual(len(self.h.submitted), 1)
        self.scheduler.tick()
        self.assertEqual(len(self.h.submitted), 1)

    def test_http_templates_drafts_import_export_and_detail(self):
        self.assertGreaterEqual(len(self.routes("GET", "/api/automations/templates", None)[1]), 3)
        status, draft = self.routes("POST", "/api/automations/drafts", {"name": "x", "spec": {"name": "x"}})
        self.assertEqual(status, 201)
        self.assertEqual(self.routes("GET", "/api/automations/drafts", None)[0], 200)
        self.assertEqual(self.routes("DELETE", f"/api/automations/drafts/{draft['id']}", None)[0], 200)
        _, made = self.routes("POST", "/api/automations", self.payload())
        status, document = self.routes("GET", f"/api/automations/{made['id']}/export", None)
        self.assertEqual(status, 200)
        document["spec"]["name"] = "copy"
        self.assertEqual(self.routes("POST", "/api/automations/import", document)[0], 201)
        _, started = self.routes("POST", f"/api/automations/{made['id']}/run", None)
        self.assertEqual(self.routes("GET", f"/api/automations/{made['id']}/runs/{started['task_id']}", None)[0], 200)


if __name__ == "__main__":
    unittest.main()
