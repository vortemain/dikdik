import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.server_routes import build_routes
from dikdik.skills_market import remove_tree

SKILL = "---\nname: {name}\ndescription: {description}\ncategory: testing\n---\n\nAlways write a test first.\n"


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True)


@unittest.skipUnless(shutil.which("git"), "git is needed for Git-source installs")
class SkillMarketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        (base / "repo").mkdir()
        self.engine = Engine(base / "repo", base / "data")
        self.addCleanup(self.engine.close)
        self.routes = build_routes(self.engine)
        self.base = base

    def call(self, method, path, body=None):
        return self.routes(method, path, body)

    def ok(self, method, path, body=None):
        code, data = self.call(method, path, body)
        self.assertIn(code, (200, 201), data)
        return data

    def folder(self, name, description="Test-first habits", text=None):
        folder = self.base / "src" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(text or SKILL.format(name=name, description=description), encoding="utf-8")
        return folder

    def repo(self):
        repo = self.base / "remote"
        (repo / "skills" / "tdd").mkdir(parents=True)
        (repo / "skills" / "tdd" / "SKILL.md").write_text(SKILL.format(name="tdd", description="first"), encoding="utf-8")
        git(repo, "init", "-q")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "one")
        first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        (repo / "skills" / "tdd" / "SKILL.md").write_text(SKILL.format(name="tdd", description="second"), encoding="utf-8")
        git(repo, "commit", "-q", "-am", "two")
        return repo, first

    def test_install_from_a_local_folder_and_conflicts(self):
        self.folder("tdd")
        self.assertEqual(self.ok("POST", "/api/skills/install", {"source": str(self.base / "src" / "tdd")})["name"], "tdd")
        listed = next(s for s in self.ok("GET", "/api/skills") if s["id"] == "tdd")
        self.assertEqual((listed["scope"], listed["category"], listed["enabled"]), ("installed", "testing", True))
        detail = self.ok("GET", "/api/skills/tdd")
        self.assertIn("Always write a test first.", detail["content"])
        self.assertEqual(self.call("POST", "/api/skills/install", {"source": str(self.base / "src" / "tdd")})[0], 409)
        self.ok("PUT", "/api/skills/tdd", {"enabled": False})
        self.ok("POST", "/api/skills/install", {"source": str(self.base / "src" / "tdd"), "force": True})
        self.assertFalse(next(s for s in self.ok("GET", "/api/skills") if s["id"] == "tdd")["enabled"])  # the off switch survives
        _, names = self.engine.skills.context()
        self.assertNotIn("tdd", names)
        self.ok("DELETE", "/api/skills/tdd")
        self.assertEqual(self.call("GET", "/api/skills/tdd")[0], 404)

    def test_invalid_skills_are_refused(self):
        bad = self.folder("broken", text="---\nname: broken\nthis line is not a key\n---\nx")
        self.assertEqual(self.call("POST", "/api/skills/install", {"source": str(bad)})[0], 400)
        bom = self.folder("bom", text="﻿" + SKILL.format(name="bom", description="saved by a Windows editor"))
        self.assertEqual(self.ok("POST", "/api/skills/install", {"source": str(bom)})["name"], "bom")
        nameless = self.folder("nameless", text="no front matter")
        self.assertEqual(self.call("POST", "/api/skills/install", {"source": str(nameless)})[0], 400)
        self.assertEqual(self.call("POST", "/api/skills/install", {"source": str(self.base / "missing")})[0], 400)
        self.folder("tdd")
        self.assertEqual(self.call("POST", "/api/skills/install", {"source": str(self.base / "src"), "repo_path": "../.."})[0], 400)
        self.assertEqual(self.call("POST", "/api/skills/install", {"source": str(self.base / "src" / "tdd"), "name": "other"})[0], 400)
        for source in ("ssh://host/x.git", "ext::sh -c x", "http://plain.example/x.git"):
            self.assertEqual(self.call("POST", "/api/skills/install", {"source": source})[0], 400, source)
        self.assertIn("approval", self.call("POST", "/api/skills/install", {"source": "github:OpenHands/extensions"})[1]["error"])

    def test_git_install_pins_a_ref_and_refreshes(self):
        repo, first = self.repo()
        url = repo.as_uri()
        installed = self.ok("POST", "/api/skills/install", {"source": url, "repo_path": "skills/tdd", "ref": first})
        self.assertEqual(installed["resolved_ref"], first)
        self.assertIn("description: first", (self.engine.data / "skills-installed" / "tdd" / "SKILL.md").read_text(encoding="utf-8"))
        record = json.loads((self.engine.data / "skills-installed" / "tdd" / ".installed.json").read_text(encoding="utf-8"))
        self.assertEqual((record["requested_ref"], record["repo_path"]), (first, "skills/tdd"))
        self.assertEqual(self.call("POST", "/api/skills/install", {"source": url, "repo_path": "skills/tdd", "ref": "no-such-ref", "force": True})[0], 400)
        self.assertIn("description: first", (self.engine.data / "skills-installed" / "tdd" / "SKILL.md").read_text(encoding="utf-8"))
        # Unpinned: refresh follows the default branch.
        self.ok("DELETE", "/api/skills/tdd")
        self.ok("POST", "/api/skills/install", {"source": url, "repo_path": "skills/tdd"})
        (repo / "skills" / "tdd" / "SKILL.md").write_text(SKILL.format(name="tdd", description="third"), encoding="utf-8")
        git(repo, "commit", "-q", "-am", "three")
        self.ok("POST", "/api/skills/tdd/refresh", {})
        self.assertIn("description: third", (self.engine.data / "skills-installed" / "tdd" / "SKILL.md").read_text(encoding="utf-8"))
        remove_tree(repo)
        self.assertEqual(self.call("POST", "/api/skills/tdd/refresh", {})[0], 400)
        self.assertTrue((self.engine.data / "skills-installed" / "tdd" / "SKILL.md").exists())  # kept

    def test_hand_copied_skills_are_listed_and_project_skills_win(self):
        target = self.engine.data / "skills-installed" / "manual"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(SKILL.format(name="manual", description="by hand"), encoding="utf-8")
        listed = next(s for s in self.ok("GET", "/api/skills") if s["id"] == "manual")
        self.assertEqual((listed["scope"], listed["source"]), ("installed", "local"))
        self.assertEqual(self.call("POST", "/api/skills/manual/refresh", {})[0], 400)
        project = self.engine.root / ".dikdik" / "skills" / "manual"
        project.mkdir(parents=True)
        (project / "SKILL.md").write_text(SKILL.format(name="manual", description="project"), encoding="utf-8")
        rows = [s for s in self.ok("GET", "/api/skills") if s["id"] == "manual"]
        self.assertEqual([(r["scope"], r["description"]) for r in rows], [("project", "project")])

    def test_marketplace_sync_catalog_and_install(self):
        repo, _ = self.repo()
        market = self.ok("POST", "/api/skills/marketplaces", {"name": "Team", "source": repo.as_uri(), "repo_path": "skills"})["id"]
        self.assertEqual(self.ok("GET", "/api/skills/catalog"), [])  # nothing until synced
        self.assertEqual(self.ok("POST", f"/api/skills/marketplaces/{market}/sync", {})["status"], "ok")
        catalog = self.ok("GET", "/api/skills/catalog")
        self.assertEqual([(e["name"], e["installed"], e["repo_path"]) for e in catalog], [("tdd", False, "skills/tdd")])
        self.ok("POST", "/api/skills/catalog/install", {"marketplace": market, "repo_path": "skills/tdd"})
        self.assertTrue(self.ok("GET", "/api/skills/catalog")[0]["installed"])
        self.assertEqual(self.call("POST", "/api/skills/catalog/install", {"marketplace": market, "repo_path": "skills/tdd"})[0], 409)
        remove_tree(repo)  # offline: a failed sync keeps the cached copy
        self.assertTrue(self.ok("POST", f"/api/skills/marketplaces/{market}/sync", {})["status"].startswith("error"))
        self.assertEqual(len(self.ok("GET", "/api/skills/catalog")), 1)
        net = self.ok("POST", "/api/skills/marketplaces", {"name": "Public", "source": "github:OpenHands/extensions"})["id"]
        self.assertEqual(self.call("POST", f"/api/skills/marketplaces/{net}/sync", {})[0], 400)  # needs approval
        self.ok("DELETE", f"/api/skills/marketplaces/{market}")
        self.assertEqual(self.ok("GET", "/api/skills/catalog"), [])


if __name__ == "__main__":
    unittest.main()
