"""Parallel writers: disjoint ownership, concurrent runs, merge by ownership, coordinator verifies."""

import threading
import unittest

from dikdik import parallel
from dikdik.agent import AgentBudget, ModelResponse, ToolCall, run_agent


class ScriptedModel:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.tools = None

    def respond(self, messages, tools):
        self.tools = [tool["function"]["name"] for tool in tools]
        return ModelResponse("", next(self.batches), 5)


class PlanTests(unittest.TestCase):
    def unit(self, *paths, task="work"):
        return {"task": task, "paths": list(paths)}

    def test_ownership_must_be_disjoint(self):
        for units in ([self.unit("src/"), self.unit("src/a.py")], [self.unit("a.py"), self.unit("./a.py")],
                      [self.unit("lib/x/"), self.unit("lib/")]):
            with self.subTest(units=units), self.assertRaisesRegex(ValueError, "both own"):
                parallel.plan(units)
        planned = parallel.plan([self.unit("src/api/"), self.unit("src/apiary.py", "web\\app.ts")])
        self.assertEqual(planned[1]["paths"], ["src/apiary.py", "web/app.ts"])

    def test_bad_units(self):
        for units in ([self.unit("a.py")], [self.unit("a.py")] * 5, [self.unit("../x.py"), self.unit("b.py")],
                      [self.unit("/etc/passwd"), self.unit("b.py")], [self.unit("."), self.unit("b.py")],
                      [self.unit("C:/x.py"), self.unit("b.py")], [{"task": "", "paths": ["a.py"]}, self.unit("b.py")],
                      [{"task": "x", "paths": ["a.py"], "shell": True}, self.unit("b.py")]):
            with self.subTest(units=units), self.assertRaises(ValueError):
                parallel.plan(units)

    def test_limits_are_exact(self):
        distinct = [self.unit(f"u{i}.py") for i in range(5)]
        self.assertEqual(len(parallel.plan(distinct[:4])), 4)
        with self.assertRaisesRegex(ValueError, "2-4 units"):
            parallel.plan(distinct)
        many = [f"f{i}.py" for i in range(41)]
        self.assertEqual(len(parallel.plan([self.unit(*many[:40]), self.unit("x.py")])[0]["paths"]), 40)
        with self.assertRaisesRegex(ValueError, "1-40"):
            parallel.plan([self.unit(*many), self.unit("x.py")])
        with self.assertRaisesRegex(ValueError, "4000"):
            parallel.plan([self.unit("a.py", task="x" * 4001), self.unit("b.py")])
        self.assertEqual(len(parallel.plan([self.unit("a.py", task="x" * 4000), self.unit("b" * 497 + ".py")])), 2)
        for bad in (123, "   ", "c" * 498 + ".py"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "non-empty relative path"):
                parallel.plan([self.unit(bad), self.unit("b.py")])

    def test_similar_names_do_not_overlap_and_errors_name_the_units(self):
        self.assertEqual(len(parallel.plan([self.unit("a.py"), self.unit("a.pyc"), self.unit("src"), self.unit("src2/")])), 4)
        with self.assertRaisesRegex(ValueError, "Units 2 and 3 both own src/"):
            parallel.plan([self.unit("x.py"), self.unit("src/"), self.unit("src/a.py")])

    def test_owns(self):
        self.assertTrue(parallel.owns(["src/"], "src/deep/a.py"))
        self.assertTrue(parallel.owns(["a.py"], "a.py"))
        self.assertFalse(parallel.owns(["src/"], "srcx/a.py"))
        self.assertFalse(parallel.owns(["a.py"], "a.pyc"))


class MergeTests(unittest.TestCase):
    def test_union_of_owned_changes_and_rejection_of_trespass(self):
        snapshot = {"a.py": "a = 1\n", "b.py": "b = 1\n", "c.py": "c = 1\n"}
        units = parallel.plan([{"task": "a", "paths": ["a.py"]}, {"task": "b", "paths": ["b.py"]}, {"task": "c", "paths": ["c.py"]}])
        outcomes = [{"state": "completed", "files": {**snapshot, "a.py": "a = 2\n", "new/a_helper.py": "x = 1\n"}},
                    {"state": "completed", "files": {**snapshot, "b.py": "b = 2\n", "a.py": "a = 99\n"}},  # trespass
                    {"state": "completed", "files": {k: v for k, v in snapshot.items() if k != "c.py"}}]  # removal
        merged, report = parallel.merge(snapshot, units, outcomes)
        self.assertEqual(merged, snapshot)  # a rejected unit contributes nothing, not even its owned edit
        self.assertEqual([r["status"] for r in report], ["rejected", "rejected", "rejected"])
        self.assertIn("new/a_helper.py", report[0]["reason"])  # creating outside ownership is trespass too
        self.assertIn("a.py", report[1]["reason"])
        self.assertIn("removed", report[2]["reason"])

    def test_writers_run_at_the_same_time(self):
        barrier = threading.Barrier(3, timeout=5)

        def writer(unit, files):
            barrier.wait()  # raises BrokenBarrierError unless all three writers are running together
            return {"state": "completed", "files": {**files, unit["paths"][0]: f"{unit['task']}\n"}}

        units = parallel.plan([{"task": name, "paths": [f"{name}.py"]} for name in ("x", "y", "z")])
        outcomes = parallel.run(units, {}, writer, threading.Event())
        merged, report = parallel.merge({}, units, outcomes)
        self.assertEqual(merged, {"x.py": "x\n", "y.py": "y\n", "z.py": "z\n"})
        self.assertEqual({r["status"] for r in report}, {"merged"})

    def test_edits_of_a_writer_that_did_not_finish_are_not_merged(self):
        units = parallel.plan([{"task": "a", "paths": ["a.py"]}, {"task": "b", "paths": ["b.py"]}])
        merged, report = parallel.merge({"a.py": "a\n", "b.py": "b\n"}, units, [
            {"state": "failed", "files": {"a.py": "half\n", "b.py": "b\n"}, "error": "Model call budget exhausted"},
            {"state": "completed", "files": {"a.py": "a\n", "b.py": "b = 2\n"}}])
        self.assertEqual(merged, {"a.py": "a\n", "b.py": "b = 2\n"})
        self.assertEqual(report[0]["status"], "rejected")
        self.assertIn("ended failed", report[0]["reason"])

    def test_the_merge_guards_against_two_authors_and_reports_units_by_number(self):
        # plan() never produces overlapping units; merge() still refuses a second author of a path.
        units = [{"index": 0, "task": "a", "paths": ["a.py"]}, {"index": 1, "task": "again", "paths": ["a.py"]}]
        merged, report = parallel.merge({"a.py": "a\n"}, units, [{"state": "completed", "files": {"a.py": "one\n"}},
                                                                  {"state": "completed", "files": {"a.py": "two\n"}}])
        self.assertEqual(merged, {"a.py": "one\n"})
        self.assertEqual([(r["unit"], r["status"]) for r in report], [(1, "merged"), (2, "rejected")])
        self.assertEqual(report[1]["reason"], "conflicts with unit 1")
        long_task = parallel.merge({}, [{"index": 0, "task": "t" * 300, "paths": ["a.py"]}], [{"state": "completed", "files": {}}])[1]
        self.assertEqual(len(long_task[0]["task"]), 200)

    def test_a_writer_that_returns_no_files_is_a_failure(self):
        units = parallel.plan([{"task": "a", "paths": ["a.py"]}, {"task": "b", "paths": ["b.py"]}])
        outcomes = parallel.run(units, {"a.py": "a\n"}, lambda unit, files: {"state": "completed"}, threading.Event())
        self.assertEqual([(o["state"], o["error"], o["files"]) for o in outcomes], [("failed", "The writer returned no files", {"a.py": "a\n"})] * 2)
        cancelled = threading.Event()
        cancelled.set()
        self.assertEqual({o["state"] for o in parallel.run(units, {}, lambda unit, files: self.fail("ran"), cancelled)}, {"cancelled"})

    def test_a_crashing_writer_changes_nothing(self):
        def writer(unit, files):
            if unit["index"] == 0:
                raise RuntimeError("model unavailable")
            return {"state": "completed", "files": {**files, "b.py": "b = 2\n"}}
        units = parallel.plan([{"task": "a", "paths": ["a.py"]}, {"task": "b", "paths": ["b.py"]}])
        merged, report = parallel.merge({"a.py": "a\n", "b.py": "b\n"}, units, parallel.run(units, {"a.py": "a\n", "b.py": "b\n"}, writer, threading.Event()))
        self.assertEqual(merged, {"a.py": "a\n", "b.py": "b = 2\n"})
        self.assertEqual((report[0]["state"], report[0]["status"], report[0]["error"]), ("failed", "no_changes", "model unavailable"))


class AgentIntegrationTests(unittest.TestCase):
    """The coordinator delegates to real child agent loops restricted by ``writable``."""

    def setUp(self):
        self.original = {"a.py": "A = 1\n", "b.py": "B = 1\n", "tests/test_ab.py": "import a, b\n"}
        self.cancel = threading.Event()
        self.verified = []
        self.child_tools = []

    def child(self, unit, files):
        ids = iter(range(100))
        call = lambda name, **args: ToolCall(f"{unit['index']}-{next(ids)}", name, args)
        mine, other = ("a.py", "b.py") if unit["paths"] == ["a.py"] else ("b.py", "a.py")
        model = ScriptedModel([
            [call("edit_file", path=other, before="1", after="3")],         # refused: another writer owns it
            [call("edit_file", path="tests/test_ab.py", before="import", after="#")],  # refused: not owned
            [call("edit_file", path=mine, before="1", after="2")],
            [call("run_tests")], [call("finish")]])
        outcome = run_agent(self.original, unit["task"], model, None, lambda kind, data: None, self.cancel,
                            AgentBudget(max_model_calls=8, max_tool_calls=10), start=files,
                            writable=lambda path: parallel.owns(unit["paths"], path))
        self.child_tools.append(model.tools)
        return {"state": outcome.state, "files": outcome.files, "error": outcome.error}

    def verify(self, files):
        self.verified.append(dict(files))
        return {"kind": "unittest", "status": "passed" if files["a.py"] == "A = 2\n" and files["b.py"] == "B = 2\n" else "failed"}

    def test_parallel_edit_merges_owned_changes_then_the_coordinator_verifies(self):
        events = []
        model = ScriptedModel([
            [ToolCall("p1", "parallel_edit", {"units": [{"task": "Set A to 2", "paths": ["a.py"]}, {"task": "Set B to 2", "paths": ["b.py"]}]})],
            [ToolCall("p2", "run_tests", {})], [ToolCall("p3", "finish", {})]])
        outcome = run_agent(self.original, "Set A and B to 2", model, self.verify, lambda kind, data: events.append((kind, data)),
                            self.cancel, parallel_writer=self.child)
        self.assertEqual(outcome.state, "completed", outcome.error)
        self.assertTrue(outcome.tests_verified)
        self.assertEqual(outcome.files, {**self.original, "a.py": "A = 2\n", "b.py": "B = 2\n"})
        self.assertEqual(len(self.verified), 1)  # tests ran once, on the merged files
        end = next(data for kind, data in events if kind == "parallel_edit" and data["phase"] == "end")
        self.assertEqual([u["status"] for u in end["units"]], ["merged", "merged"])
        self.assertEqual(end["changed_files"], ["a.py", "b.py"])
        for tools in self.child_tools:  # writers cannot run commands or start writers of their own
            self.assertFalse({"run_command", "run_tests_shell", "parallel_edit"} & set(tools))

    def test_parallel_edit_is_only_offered_with_a_writer(self):
        model = ScriptedModel([[ToolCall("x", "finish", {})]])
        run_agent(self.original, "Look", model, self.verify, lambda kind, data: None, self.cancel,
                  AgentBudget(max_model_calls=1))
        self.assertNotIn("parallel_edit", model.tools)


class EngineTests(unittest.TestCase):
    """A local-mode turn on a real folder: two writer agents fix two files, the coordinator runs the real tests."""

    def test_two_writers_fix_two_files_and_the_real_tests_pass(self):
        import os
        import re
        import tempfile
        import time
        from pathlib import Path
        from unittest.mock import patch

        from dikdik.engine import TERMINAL, Engine

        test = ("import unittest\nfrom calc import add\nfrom mul import mul\n\nclass T(unittest.TestCase):\n"
                "    def test_both(self):\n        self.assertEqual((add(2, 3), mul(2, 3)), (5, 6))\n")
        fixes = {"calc.py": ("a - b", "a + b"), "mul.py": ("a + b", "a * b")}

        class Model:
            """Coordinator or writer, told apart by the writer's system prompt."""
            seen_tools = []

            def __init__(self):
                self.step = 0

            def respond(self, messages, tools):
                self.step += 1
                owned = re.search(r"You own only these files and folders: ([^.]+\.py)", messages[0]["content"])
                if owned:  # a writer
                    path = owned.group(1)
                    if self.step == 1:
                        return ModelResponse("", [ToolCall(f"{path}-1", "edit_file", {"path": path, "before": fixes[path][0], "after": fixes[path][1]})], 5)
                    return ModelResponse("", [ToolCall(f"{path}-{self.step}", "run_tests" if self.step == 2 else "finish", {})], 5)
                Model.seen_tools = [tool["function"]["name"] for tool in tools]
                script = [ToolCall("c1", "parallel_edit", {"units": [{"task": "Fix add", "paths": ["calc.py"]}, {"task": "Fix mul", "paths": ["mul.py"]}]}),
                          ToolCall("c2", "run_tests", {}), ToolCall("c3", "finish", {})]
                return ModelResponse("", [script[self.step - 1]], 5)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            (root / "mul.py").write_text("def mul(a, b):\n    return a + b\n", encoding="utf-8")
            (root / "test_calc.py").write_text(test, encoding="utf-8")
            engine = Engine(root, Path(temp) / "data")
            try:
                env = {"DIKDIK_MODEL_URL": "https://provider.example/v1/chat/completions", "DIKDIK_MODEL": "m", "DIKDIK_API_KEY": "k"}
                with patch.dict(os.environ, env), patch("dikdik.engine.CompatibleAgentModel", side_effect=lambda *a, **k: Model()):
                    task = engine.submit({"objective": "Fix add and mul", "mode": "agent", "verification": "local",
                                          "approve_external": True, "approve_execution": True})
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        record = engine.store.get(task)
                        if record["state"] in TERMINAL and not engine.busy(task):
                            break
                        time.sleep(0.05)
                self.assertEqual(record["state"], "completed", record["result"])
                self.assertTrue(record["result"]["tests_verified"])  # the real unittest ran on the merged files
                self.assertIn("a + b", (root / "calc.py").read_text(encoding="utf-8"))
                self.assertIn("a * b", (root / "mul.py").read_text(encoding="utf-8"))
                writers = [e["data"] for e in record["events"] if e["kind"] == "writer"]
                self.assertEqual(sorted((w["phase"], w["unit"]) for w in writers), [("end", 1), ("end", 2), ("start", 1), ("start", 2)])
                self.assertTrue(all(w["state"] == "completed" for w in writers if w["phase"] == "end"), writers)
                self.assertTrue({"parallel_edit", "dast_scan", "fault_test", "load_test"} <= set(Model.seen_tools))
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
