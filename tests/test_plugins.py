import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dikdik.agent import ToolCall
from dikdik.control import blocked_by
from dikdik.engine import Engine
from dikdik.plugins import fill_prompt
from dikdik.server_routes import build_routes

MANIFEST = {
    "name": "review-kit", "version": "1.2.0", "description": "Careful reviews",
    "hooks": {"block": [{"tool": "run_command", "pattern": "git push", "message": "No pushes from review-kit"}]},
    "mcp_servers": [{"name": "issues", "transport": "http", "url": "http://127.0.0.1:9/mcp"}],
    "prompt": "Review {{ branch }} (strict: {{ strict }}, depth {{ depth }})",
    "parameters": [{"name": "branch", "type": "text"}, {"name": "strict", "type": "boolean"}, {"name": "depth", "type": "number"}],
}


def write_plugin(folder: Path, manifest=None, skill_text="Read every changed line."):
    (folder / "skills" / "line-by-line").mkdir(parents=True)
    (folder / "skills" / "line-by-line" / "SKILL.md").write_text(f"---\nname: line-by-line\ndescription: Review habits\n---\n\n{skill_text}\n", encoding="utf-8")
    (folder / "agents").mkdir()
    (folder / "agents" / "auditor.md").write_text("---\nname: auditor\ndescription: Checks security\n---\nLook for secrets.\n", encoding="utf-8")
    if manifest is not False:
        (folder / "plugin.json").write_text(json.dumps(manifest or MANIFEST), encoding="utf-8")
    return folder


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "repo").mkdir()
        self.engine = Engine(self.base / "repo", self.base / "data")
        self.addCleanup(self.engine.close)
        self.routes = build_routes(self.engine)

    def call(self, method, path, body=None):
        return self.routes(method, path, body)

    def ok(self, method, path, body=None):
        code, data = self.call(method, path, body)
        self.assertIn(code, (200, 201), data)
        return data

    def active_parts(self):
        _, skills = self.engine.skills.context()
        hooks = self.engine.agent_info()["hooks"]
        agents = [a["name"] for a in self.engine.agent_info()["subagents"]]
        return skills, hooks, agents

    def test_install_trust_enable_and_what_it_adds(self):
        source = write_plugin(self.base / "src" / "review-kit")
        installed = self.ok("POST", "/api/plugins/install", {"source": str(source)})
        self.assertEqual((installed["name"], installed["version"], installed["enabled"]), ("review-kit", "1.2.0", False))
        listed = self.ok("GET", "/api/plugins")[0]
        self.assertEqual(listed["adds"]["skills"], ["line-by-line"])
        self.assertEqual(listed["adds"]["agents"], ["auditor"])
        self.assertEqual(listed["adds"]["mcp_servers"][0]["target"], "http://127.0.0.1:9/mcp")
        skills, hooks, agents = self.active_parts()
        self.assertNotIn("line-by-line", skills)  # nothing happens before trust
        self.assertEqual(hooks, [])
        self.assertEqual(self.engine.mcp.list(), [])
        code, data = self.call("PATCH", "/api/plugins/review-kit", {"enabled": True})
        self.assertEqual(code, 400)
        self.assertIn("trust", data["error"])
        self.ok("PATCH", "/api/plugins/review-kit", {"enabled": True, "trust": True})
        skills, hooks, agents = self.active_parts()
        self.assertIn("line-by-line", skills)
        self.assertEqual(hooks[0]["source"], "plugin review-kit")
        self.assertIn("auditor", agents)
        servers = self.engine.mcp.list()
        self.assertEqual([(s["name"], s["enabled"]) for s in servers], [("review-kit-issues", False)])  # added switched off
        rules = __import__("dikdik.control", fromlist=["load_hooks"]).load_hooks(self.engine.root, self.engine.plugins.hook_documents())
        self.assertEqual(blocked_by(rules, ToolCall("x", "run_command", {"command": "git push origin"})), "No pushes from review-kit")
        self.ok("PATCH", "/api/plugins/review-kit", {"enabled": False})
        self.assertNotIn("line-by-line", self.active_parts()[0])
        self.ok("PATCH", "/api/plugins/review-kit", {"enabled": True})  # already trusted
        self.ok("DELETE", "/api/plugins/review-kit")
        self.assertEqual(self.engine.mcp.list(), [])  # its servers go with it
        self.assertEqual(self.call("GET", "/api/plugins/review-kit")[0], 404)

    def test_reinstall_keeps_choices_unless_contents_change(self):
        source = write_plugin(self.base / "src" / "review-kit")
        self.ok("POST", "/api/plugins/install", {"source": str(source)})
        self.ok("PATCH", "/api/plugins/review-kit", {"enabled": True, "trust": True})
        self.assertEqual(self.call("POST", "/api/plugins/install", {"source": str(source)})[0], 409)
        self.assertTrue(self.ok("POST", "/api/plugins/install", {"source": str(source), "force": True})["enabled"])
        (source / "agents" / "pusher.md").write_text("---\nname: pusher\n---\nPush things.\n", encoding="utf-8")
        self.assertFalse(self.ok("POST", "/api/plugins/install", {"source": str(source), "force": True})["enabled"])  # new contents: review again

    def test_project_plugins_are_inert_until_trusted(self):
        write_plugin(self.engine.root / ".dikdik" / "plugins" / "review-kit")
        listed = self.ok("GET", "/api/plugins")
        self.assertEqual([(p["name"], p["scope"], p["enabled"]) for p in listed], [("review-kit", "project", False)])
        self.assertNotIn("line-by-line", self.active_parts()[0])
        self.ok("PATCH", "/api/plugins/review-kit", {"enabled": True, "trust": True})
        self.assertIn("line-by-line", self.active_parts()[0])
        self.assertEqual(self.call("DELETE", "/api/plugins/review-kit")[0], 400)  # part of the project

    def test_invalid_plugins_and_names(self):
        bad = self.base / "src" / "bad"
        bad.mkdir(parents=True)
        (bad / "plugin.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.call("POST", "/api/plugins/install", {"source": str(bad)})[0], 400)
        (bad / "plugin.json").write_text(json.dumps({"name": "Bad Name"}), encoding="utf-8")
        self.assertEqual(self.call("POST", "/api/plugins/install", {"source": str(bad)})[0], 400)
        (bad / "plugin.json").write_text(json.dumps({"name": "ok", "skills": "../outside"}), encoding="utf-8")
        self.assertEqual(self.call("POST", "/api/plugins/install", {"source": str(bad)})[0], 400)
        self.assertEqual(list((self.engine.data / "plugins").iterdir()), [])  # nothing left behind
        self.assertEqual(self.call("GET", "/api/plugins/Bad_Name")[0], 400)
        self.assertEqual(self.call("GET", "/api/plugins/missing")[0], 404)
        self.assertIn("approval", self.call("POST", "/api/plugins/install", {"source": "github:owner/repo"})[1]["error"])

    def test_files_and_launch(self):
        self.ok("POST", "/api/plugins/install", {"source": str(write_plugin(self.base / "src" / "review-kit"))})
        detail = self.ok("GET", "/api/plugins/review-kit")
        self.assertIn("skills/line-by-line/SKILL.md", detail["files"])
        self.assertNotIn(".installed.json", detail["files"])
        self.assertIn("Read every changed line.", self.ok("GET", "/api/plugins/review-kit/files?path=skills/line-by-line/SKILL.md")["text"])
        self.assertEqual(self.call("GET", "/api/plugins/review-kit/files?path=../../secrets.bin")[0], 404)
        self.assertEqual(self.call("POST", "/api/plugins/review-kit/launch", {})[0], 400)  # not trusted yet
        self.ok("PATCH", "/api/plugins/review-kit", {"enabled": True, "trust": True})
        prompt = self.ok("POST", "/api/plugins/review-kit/launch", {"parameters": {"branch": "main", "strict": True}})["prompt"]
        self.assertEqual(prompt, "Review main (strict: yes, depth 0)")
        self.assertEqual(fill_prompt("{{ x }}", [{"name": "x", "type": "text"}], {"x": "日本"}), "日本")

    @unittest.skipUnless(shutil.which("git"), "git is needed for Git-source installs")
    def test_git_install_named_after_the_repository_and_refresh(self):
        repo = self.base / "tool-belt"
        write_plugin(repo, manifest=False)
        run = lambda *args: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True)
        run("init", "-q"); run("add", "."); run("commit", "-q", "-m", "one")
        installed = self.ok("POST", "/api/plugins/install", {"source": repo.as_uri()})
        self.assertEqual(installed["name"], "tool-belt")
        self.assertEqual(len(installed["resolved_ref"]), 40)
        (repo / "skills" / "line-by-line" / "SKILL.md").write_text("---\nname: line-by-line\n---\nUpdated.\n", encoding="utf-8")
        run("commit", "-q", "-am", "two")
        self.ok("POST", "/api/plugins/tool-belt/refresh", {})
        self.assertIn("Updated.", self.ok("GET", "/api/plugins/tool-belt/files?path=skills/line-by-line/SKILL.md")["text"])

    @unittest.skipUnless(shutil.which("git"), "git is needed for marketplaces")
    def test_catalog_from_a_marketplace(self):
        market = self.base / "market"
        write_plugin(market / "plugins" / "review-kit")
        run = lambda *args: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], cwd=market, check=True, capture_output=True)
        run("init", "-q"); run("add", "."); run("commit", "-q", "-m", "one")
        mid = self.ok("POST", "/api/skills/marketplaces", {"name": "Team", "source": market.as_uri()})["id"]
        self.ok("POST", f"/api/skills/marketplaces/{mid}/sync", {})
        catalog = self.ok("GET", "/api/plugins/catalog")
        self.assertEqual([(e["name"], e["installed"]) for e in catalog], [("review-kit", False)])
        self.ok("POST", "/api/plugins/catalog/install", {"marketplace": mid, "repo_path": catalog[0]["repo_path"]})
        self.assertTrue(self.ok("GET", "/api/plugins/catalog")[0]["installed"])


if __name__ == "__main__":
    unittest.main()
