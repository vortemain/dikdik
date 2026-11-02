"""The dependency graph, cycles, change impact and decision records come from real imports."""

import threading
import unittest

from dikdik import architecture
from dikdik.agent import ModelResponse, ToolCall, run_agent

PROJECT = {
    "app/__init__.py": "",
    "app/models.py": "import json\nfrom .db import connect\n",
    "app/db.py": "import sqlite3\nfrom app import models\n",           # cycle with models
    "app/api.py": "from app.models import User\nfrom . import util\nimport requests\n",
    "app/util.py": "VALUE = 0\n",
    "app/main.py": "from app import api\n",
    "tests/test_api.py": "from app.api import handler\n",
    "tests/test_util.py": "from app import util\n",
    "tests/test_cli.py": "import subprocess\n",                         # imports nothing internal
    "web/src/index.ts": "import { view } from './view';\nimport React from 'react';\nexport * from './lib/format.js';\n",
    "web/src/view.tsx": "const chart = await import('./chart');\nimport { z } from '@scope/zod/extra';\n",
    "web/src/chart.ts": "const fs = require('node:fs');\n",
    "web/src/lib/format.ts": "",
    "web/src/lib/index.ts": "import '../view';\n",
    "web/src/view.test.ts": "import { view } from './view';\n",
}


class GraphTests(unittest.TestCase):
    def setUp(self):
        graph = architecture.dependency_graph(PROJECT)
        self.edges, self.external = graph["edges"], graph["external"]

    def test_python_imports_resolve_absolute_relative_and_submodules(self):
        self.assertEqual(self.edges["app/models.py"], ["app/__init__.py", "app/db.py"])
        self.assertEqual(self.edges["app/api.py"], ["app/__init__.py", "app/models.py", "app/util.py"])
        self.assertEqual(self.external["app/api.py"], ["requests"])
        self.assertEqual(self.edges["tests/test_util.py"], ["app/__init__.py", "app/util.py"])

    def test_javascript_imports_resolve_extensions_index_and_js_to_ts(self):
        self.assertEqual(self.edges["web/src/index.ts"], ["web/src/lib/format.ts", "web/src/view.tsx"])
        self.assertEqual(self.edges["web/src/view.tsx"], ["web/src/chart.ts"])
        self.assertEqual(self.edges["web/src/lib/index.ts"], ["web/src/view.tsx"])
        self.assertEqual(self.external["web/src/index.ts"], ["react"])
        self.assertEqual(self.external["web/src/view.tsx"], ["@scope/zod"])
        self.assertEqual(self.external["web/src/chart.ts"], ["fs"])

    def test_cycles(self):
        self.assertIn(["app/db.py", "app/models.py"], architecture.cycles(self.edges))
        self.assertEqual(architecture.cycles({"a": ["b"], "b": ["c"], "c": []}), [])
        self.assertEqual(architecture.cycles({"a": ["b"], "b": ["c"], "c": ["a"]}), [["a", "b", "c"]])

    def test_change_impact_selects_dependent_tests_and_reports_unmapped(self):
        result = architecture.impact(self.edges, ["app/util.py"])
        self.assertEqual(result["tests"], ["tests/test_api.py", "tests/test_util.py"])
        self.assertIn("app/main.py", result["affected_modules"])
        self.assertEqual(result["unmapped_tests"], ["tests/test_cli.py"])
        self.assertEqual(architecture.impact(self.edges, ["web/src/chart.ts"])["tests"], ["web/src/view.test.ts"])
        self.assertEqual(architecture.impact(self.edges, ["README.md"])["unresolved_changes"], ["README.md"])

    def test_summary(self):
        report = architecture.summary(PROJECT)
        self.assertEqual((report["modules"], report["test_modules"]), (15, 4))
        self.assertIn("app/util.py", [m["module"] for m in report["most_depended_on"]])
        self.assertIn("app/main.py", [p for p in PROJECT if p not in report["not_imported"]])  # entry points are expected roots
        self.assertTrue(report["mermaid"].startswith("flowchart LR"))


class DecisionRecordTests(unittest.TestCase):
    FIELDS = {"title": "Use SQLite for local state", "context": "Single owner, local first.",
              "decision": "Keep SQLite with WAL.", "consequences": "No server to run."}

    def test_numbering_and_sections(self):
        self.assertEqual(architecture.next_adr(["docs/adr/0003-x.md", "docs/adr/0010-y.md", "README.md"])[0], 11)
        self.assertEqual(architecture.next_adr([])[0], 1)
        path, text = architecture.adr(4, {**self.FIELDS, "reversal": "Multi-user hosting."}, "2026-11-01")
        self.assertEqual(path, "docs/adr/0004-use-sqlite-for-local-state.md")
        self.assertIn("# ADR-0004: Use SQLite for local state", text)
        self.assertIn("## Reversal conditions", text)
        self.assertNotIn("## Risks", text)

    def test_validation(self):
        for bad in ({"context": ""}, {"status": "maybe"}, {"owner": "me"}, {"decision": "x" * 7000}):
            with self.assertRaises(ValueError):
                architecture.adr(1, {**self.FIELDS, **bad}, "2026-11-01")


class AgentToolTests(unittest.TestCase):
    def drive(self, batches, creation=True):
        script = iter([[ToolCall(str(i), name, args) for name, args in batch] for i, batch in enumerate(batches)])
        class Model:
            def respond(self, messages, tools):
                return ModelResponse("", next(script), 1)
        events = []
        outcome = run_agent(dict(PROJECT), "Refactor", Model(), lambda files: {"kind": "unittest", "status": "passed"},
                            lambda kind, data: events.append((kind, data)), threading.Event(),
                            creation_policy=lambda path: creation)
        ends = {d["name"]: d["observation"] for k, d in events if k == "tool" and d.get("phase") == "end"}
        return outcome, ends

    def test_impacted_tests_defaults_to_this_turns_changes(self):
        _, ends = self.drive([[("edit_file", {"path": "app/util.py", "before": "VALUE = 0", "after": "VALUE = 1"})],
                              [("impacted_tests", {})], [("architecture_map", {})], [("finish", {})]])
        self.assertEqual(ends["impacted_tests"]["changed"], ["app/util.py"])
        self.assertEqual(ends["impacted_tests"]["tests"], ["tests/test_api.py", "tests/test_util.py"])
        self.assertIn(["app/db.py", "app/models.py"], ends["architecture_map"]["cycles"])

    def test_record_decision_creates_a_numbered_file_through_the_creation_policy(self):
        outcome, ends = self.drive([[("record_decision", DecisionRecordTests.FIELDS)], [("run_tests", {})], [("finish", {})]])
        self.assertEqual(ends["record_decision"]["created"], "docs/adr/0001-use-sqlite-for-local-state.md")
        self.assertIn("## Decision", outcome.files["docs/adr/0001-use-sqlite-for-local-state.md"])
        _, ends = self.drive([[("record_decision", DecisionRecordTests.FIELDS)], [("finish", {})]], creation=False)
        self.assertIn("approval", ends["record_decision"]["error"])


if __name__ == "__main__":
    unittest.main()
