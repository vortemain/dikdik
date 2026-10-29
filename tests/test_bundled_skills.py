"""Bundled skills seed on first run and respect a user's uninstall."""
import tempfile
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.skills import SkillIndex
from dikdik.skills_market import SkillStore


class BundledSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("x = 1\n")
        self.data = base / "data"

    def make_engine(self):
        self.engine = Engine(self.root, self.data)
        self.addCleanup(self.engine.close)
        return self.engine

    def test_bundled_skills_are_installed_on_first_run(self):
        engine = self.make_engine()
        installed = engine.skills.installed_dir
        self.assertTrue((installed / "planning" / "SKILL.md").is_file())
        rows = {row["id"]: row for row in engine.skills.enumerate_skills()}
        self.assertIn("planning", rows)
        self.assertEqual(rows["planning"]["scope"], "installed")
        self.assertIn("plan", rows["planning"]["description"].lower())

    def test_skills_are_listed_with_real_descriptions(self):
        engine = self.make_engine()
        rows = {row["id"]: row for row in engine.skills.enumerate_skills()}
        for name in ("brainstorming", "planning", "test-driven-development", "debugging", "code-review",
                     "research-first", "security-review", "ui-ux-design", "document-creation", "spec-to-ship"):
            with self.subTest(name=name):
                self.assertIn(name, rows, f"{name} should be bundled")
                self.assertTrue(rows[name]["description"], f"{name} needs a description")

    def test_reseding_does_not_overwrite_user_changes(self):
        engine = self.make_engine()
        target = engine.skills.installed_dir / "planning" / "SKILL.md"
        target.write_text("---\nname: planning\ndescription: My custom version\n---\nDo it my way.\n", encoding="utf-8")
        # A second engine over the same data dir should not reseed
        engine.close()
        self.make_engine()
        self.assertEqual(self.engine.skills.installed_dir / "planning" / "SKILL.md", target)
        self.assertIn("Do it my way.", target.read_text())

    def test_uninstalled_bundled_skill_does_not_reseed(self):
        engine = self.make_engine()
        store = SkillStore(self.data, engine.store)
        self.assertEqual(store.uninstall("test-driven-development"), {"deleted": "test-driven-development"})
        self.assertFalse((engine.skills.installed_dir / "test-driven-development").exists())
        engine.close()
        # A fresh engine should not bring it back
        self.make_engine()
        self.assertFalse((self.engine.skills.installed_dir / "test-driven-development").exists(),
                         "A bundled skill the user removed must stay removed")

    def test_bundled_skills_reach_the_model_as_untrusted_context(self):
        engine = self.make_engine()
        text, names = engine.skills.context()
        self.assertIn("test-driven-development", text)  # catalog survives body budget limits
        self.assertIn("brainstorming", text)
        self.assertIn("planning", names)
        self.assertTrue(text.startswith("<!-- dikdik-untrusted-data"))
        self.assertIn("spec-to-ship", text)
        self.assertIn("Spec", engine.skills.load("spec-to-ship")["content"])
        self.assertNotIn("name: planning", text)  # front matter is not repeated


if __name__ == "__main__":
    unittest.main()
