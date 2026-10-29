import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import Engine
from dikdik.control import load_subagents
from dikdik.skills import CONTEXT_LIMIT, SKILL_UNTRUSTED_MARKER, _bundled_skills_dir, _front_matter


class SkillDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("x=1\n")
        self.engine = Engine(self.root, base / "data")
        self.addCleanup(self.engine.close)
        self.skills = self.engine.skills

    def test_all_34_bundles_are_distinct_and_discoverable(self):
        files = sorted(_bundled_skills_dir().glob("*/SKILL.md"))
        self.assertEqual(len(files), 34)
        bodies = set()
        catalog, _ = self.skills.context()
        for file in files:
            meta, body = _front_matter(file.read_text(encoding="utf-8"))
            self.assertEqual(meta["name"], file.parent.name)
            self.assertRegex(meta["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertTrue(meta["description"])
            digest = hashlib.sha256(body.encode()).hexdigest()
            self.assertNotIn(digest, bodies)
            bodies.add(digest)
            self.assertIn(meta["name"], catalog)
            self.assertTrue(self.skills.load(meta["name"])["content"].startswith(SKILL_UNTRUSTED_MARKER))
        self.assertLessEqual(len(catalog.encode()), CONTEXT_LIMIT + 1024)

    def test_explicit_skill_activation_and_relevant_routing(self):
        text, names = self.skills.context("Use $retrieval-rag to inspect these sources", "agent")
        self.assertIn("retrieval-rag", names)
        self.assertIn("source ids", text)
        self.assertNotIn("release-management", names)
        _, names = self.skills.context("Recover from a tool failure", "agent")
        self.assertIn("error-recovery", names)

    def test_overrides_do_not_repeat_bundled_skill(self):
        path = self.root / ".dikdik" / "skills" / "verification.md"
        path.parent.mkdir(parents=True)
        path.write_text("User verification convention")
        rows = [r for r in self.skills.enumerate_skills() if r["id"] == "verification"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scope"], "project")
        self.assertIn("User verification convention", self.skills.load("verification")["content"])

    def test_disabled_skill_is_not_discoverable_or_loadable(self):
        self.skills.set_enabled("retrieval-rag", False)
        catalog, names = self.skills.context("$retrieval-rag", "agent")
        self.assertNotIn("retrieval-rag", catalog)
        self.assertNotIn("retrieval-rag", names)
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.skills.load("retrieval-rag")

    def test_a_large_body_does_not_hide_a_later_small_skill(self):
        directory = self.root / ".dikdik" / "skills"
        directory.mkdir(parents=True)
        for index in range(4):
            (directory / f"a{index}.md").write_text("x" * 20000)
        (directory / "z-small.md").write_text("Unique small workflow")
        context, names = self.skills.context()
        self.assertIn("z-small", names)
        self.assertIn("Unique small workflow", context)
        self.assertIn("retrieval-rag", context)  # catalog entry survives even when bodies do not fit
        self.assertLessEqual(len(context.encode()), CONTEXT_LIMIT + 1024)

    def test_resource_pagination_and_path_confinement(self):
        folder = self.root / ".dikdik" / "skills" / "resources"
        (folder / "references").mkdir(parents=True)
        (folder / "SKILL.md").write_text("---\nname: resources\ndescription: Resource test\n---\nStart here.")
        (folder / "references" / "guide.txt").write_text("abcdef")
        loaded = self.skills.load("resources")
        self.assertEqual(loaded["resources"], ["references/guide.txt"])
        page = self.skills.load("resources", "references/guide.txt", 1, 3)
        self.assertTrue(page["content"].endswith("bcd"))
        self.assertEqual(page["next_offset"], 4)
        for resource in ("../app.py", "C:/secrets", "/secrets", "references\\guide.txt", "missing", []):
            with self.subTest(resource=resource), self.assertRaises(ValueError):
                self.skills.load("resources", resource)
        for offset, size in ((-1, 1), (True, 2), (0, 0), (0, 16001)):
            with self.assertRaises(ValueError):
                self.skills.load("resources", offset=offset, max_chars=size)

    def test_escaping_resource_symlink_is_refused(self):
        folder = self.root / ".dikdik" / "skills" / "links"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("Links")
        outside = self.root / "private.txt"
        outside.write_text("outside")
        try:
            (folder / "escape.txt").symlink_to(outside)
        except OSError:
            self.skipTest("Creating symlinks is unavailable")
        with self.assertRaises(ValueError):
            self.skills.load("links", "escape.txt")
        self.assertNotIn("escape.txt", self.skills.load("links")["resources"])

    def test_symlinked_plugin_skill_and_agent_sources_are_not_loaded(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("Private skill data")
        (outside / "agent.md").write_text("---\nname: private-agent\n---\nPrivate agent data")
        skills = self.root / ".dikdik" / "skills"
        agents = self.root / ".dikdik" / "agents"
        skills.mkdir(parents=True)
        agents.mkdir()
        try:
            (skills / "escape").symlink_to(outside, target_is_directory=True)
            (agents / "private.md").symlink_to(outside / "agent.md")
        except OSError:
            self.skipTest("Creating symlinks is unavailable")
        self.assertNotIn("escape", [row["id"] for row in self.skills.enumerate_skills()])
        self.assertNotIn("private-agent", [row["name"] for row in load_subagents(self.root, self.root / "personal-agents")])

    def test_native_read_skill_is_a_hooked_tool_and_profile_can_disable_it(self):
        class Reader:
            def __init__(self):
                self.count, self.tools = 0, []

            def respond(self, messages, tools):
                self.count += 1
                self.tools = [t["function"]["name"] for t in tools]
                if self.count == 1 and "read_skill" in self.tools:
                    return ModelResponse("", [ToolCall("skill", "read_skill", {"id": "retrieval-rag"})])
                if self.count == 2:
                    self.observation = messages[-1]["content"]
                return ModelResponse("Done", [])

        model = Reader()
        request = {"objective": "Explain", "mode": "agent", "verification": "syntax", "approve_external": True}
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            task = self.engine.submit(request)
            self.engine.pool.submit(lambda: None).result(timeout=30)
        self.assertIn("source ids", model.observation)
        events = self.engine.store.get(task)["events"]
        tool_events = [e["data"] for e in events if e["kind"] == "hook_event" and e["data"].get("tool") == "read_skill"]
        self.assertEqual([e["event"] for e in tool_events], ["tool.before", "tool.after"])
        profile = self.engine.agents.create({"name": "No skills", "use_skills": False})
        model = Reader()
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            self.engine.submit({**request, "agent_profile": profile["id"]})
            self.engine.pool.submit(lambda: None).result(timeout=30)
        self.assertNotIn("read_skill", model.tools)


if __name__ == "__main__":
    unittest.main()
