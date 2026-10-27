"""Automation Git sync is deterministic, scoped, hook-free and push-gated."""

import json
import subprocess
import unittest

from dikdik.automation import AutomationScheduler
from dikdik.automation_git import AutomationGitSync
from dikdik.server_routes import build_routes
from tests.test_skills_automation import Harness


class AutomationGitTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        subprocess.run(["git", "init"], cwd=self.h.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "sync@example.test"], cwd=self.h.root, check=True)
        subprocess.run(["git", "config", "user.name", "Sync Test"], cwd=self.h.root, check=True)
        (self.h.root / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.h.root, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.h.root, check=True, capture_output=True)
        self.scheduler = AutomationScheduler(self.h, clock=self.h.clock)
        self.sync = AutomationGitSync(self.h, self.scheduler)

    def tearDown(self):
        self.h.close()

    def make(self, name="Review"):
        return self.scheduler.create({"name": name, "objective": "Review the workspace", "schedule": {"interval_seconds": 60},
                                      "approve_external": True, "mode": "agent", "verification": "syntax"})[1]

    def configure(self, **changes):
        body = {"repository": ".", "directory": ".dikdik/automations", "branch": self.branch(), "remote": "origin", **changes}
        return self.sync.save(body, self.h.clock())[1]

    def branch(self):
        return subprocess.run(["git", "branch", "--show-current"], cwd=self.h.root, check=True, capture_output=True, text=True).stdout.strip()

    def test_preview_commit_delete_and_secret_free_files(self):
        made = self.make()
        self.configure()
        status = self.sync.status()[1]
        self.assertEqual(status["pending"], 2)
        result = self.sync.sync({"approve_push": False}, self.h.clock())[1]
        self.assertTrue(result["committed"])
        self.assertFalse(result["pushed"])
        files = list((self.h.root / ".dikdik" / "automations").rglob("*.json"))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertIn("dikdik-automation", combined)
        self.assertNotIn("secret", combined.lower())
        self.assertEqual(self.sync.status()[1]["pending"], 0)
        self.scheduler.delete(made["id"])
        preview = self.sync.status()[1]
        self.assertTrue(preview["deleted"])
        self.sync.sync({"approve_push": False}, self.h.clock())
        self.assertFalse(any(path.name == "automation.json" for path in (self.h.root / ".dikdik" / "automations").rglob("*.json")))

    def test_push_requires_fresh_explicit_boolean(self):
        self.make()
        remote = self.h.tmp.name + "\\remote.git"
        subprocess.run(["git", "init", "--bare", remote], check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=self.h.root, check=True)
        self.configure()
        with self.assertRaises(ValueError):
            self.sync.sync({"approve_push": 1}, self.h.clock())
        first = self.sync.sync({"approve_push": False}, self.h.clock())[1]
        self.assertFalse(first["pushed"])
        refs = subprocess.run(["git", "for-each-ref", "--format=%(refname)", "refs/heads"], cwd=remote, check=True, capture_output=True, text=True).stdout
        self.assertEqual(refs, "")
        pushed = self.sync.sync({"approve_push": True}, self.h.clock())[1]
        self.assertTrue(pushed["pushed"])
        refs = subprocess.run(["git", "for-each-ref", "--format=%(refname)", "refs/heads"], cwd=remote, check=True, capture_output=True, text=True).stdout
        self.assertIn(self.branch(), refs)

    def test_failing_push_keeps_the_commit_and_manifest(self):
        self.make()
        self.configure(remote="absent-origin")
        first = self.sync.sync({"approve_push": False}, self.h.clock())[1]
        self.assertTrue(first["committed"])
        with self.assertRaises(ValueError):
            self.sync.sync({"approve_push": True}, self.h.clock())
        again = self.sync.sync({"approve_push": False}, self.h.clock())[1]
        self.assertFalse(again["committed"])  # nothing new staged on the second attempt
        self.assertFalse(again["pushed"])
        self.assertEqual(self.sync.status()[1]["pending"], 0)

    def test_validation_refuses_outside_and_unmanaged_directories(self):
        with self.assertRaises(ValueError):
            self.sync.save({"repository": "..", "directory": "sync", "branch": self.branch(), "remote": "origin"}, 0)
        self.configure(directory="existing")
        (self.h.root / "existing").mkdir()
        (self.h.root / "existing" / "mine.txt").write_text("do not touch", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not managed"):
            self.sync.status()

    def test_routes_configure_preview_and_sync(self):
        self.make()

        class Stores:
            pass
        stores = Stores()
        stores.store, stores.data, stores.root, stores.automation = self.h.store, self.h.data, self.h.root, self.scheduler
        routes = build_routes(stores)
        self.assertEqual(routes("GET", "/api/automations/git-sync", None)[1]["configured"], False)
        body = {"repository": ".", "directory": ".dikdik/automations", "branch": self.branch(), "remote": "origin"}
        self.assertEqual(routes("PATCH", "/api/automations/git-sync", body)[0], 200)
        self.assertGreater(routes("GET", "/api/automations/git-sync/status", None)[1]["pending"], 0)
        self.assertEqual(routes("POST", "/api/automations/git-sync/sync", {"approve_push": False})[0], 200)


if __name__ == "__main__":
    unittest.main()
