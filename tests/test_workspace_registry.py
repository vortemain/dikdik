"""A workspace registry tracks recently used roots."""

import json
import unittest
from pathlib import Path
import tempfile

from dikdik.workspace_registry import WorkspaceRegistry


class WorkspaceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reg = WorkspaceRegistry(Path(self.temp.name))

    def test_add_and_list(self):
        entry = self.reg.add("/some/path", "My Project")
        self.assertEqual(entry["name"], "My Project")
        entries = self.reg.list()
        self.assertEqual(len(entries), 1)
        self.assertIn("path", entries[0])
        self.assertIn("added", entries[0])

    def test_add_dedupes(self):
        self.reg.add("/path/a")
        self.reg.add("/path/a", "Renamed")
        entries = self.reg.list()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "Renamed")

    def test_remove(self):
        self.reg.add("/path/a")
        self.reg.add("/path/b")
        self.reg.remove("/path/a")
        entries = self.reg.list()
        self.assertEqual(len(entries), 1)
        self.assertNotIn("/path/a", [e["path"] for e in entries])


if __name__ == "__main__":
    unittest.main()