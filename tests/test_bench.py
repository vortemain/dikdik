"""The benchmark harness: metrics, scoring, invariants and isolated task runs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik import bench
from dikdik.agent import ModelResponse, ToolCall
from dikdik.model_config import ModelConfig

SUITE = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures.json"
CONFIG = ModelConfig("https://bench.invalid/v1/chat/completions", "scripted", "key", "bearer", 60, {"model": "scripted"})


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)

    def respond(self, messages, tools):
        return self.replies.pop(0)


def tool(identity, name, **arguments):
    return ModelResponse("", [ToolCall(identity, name, arguments)])


def event(kind, time, **data):
    return {"kind": kind, "time": time, "data": data}


class BenchTests(unittest.TestCase):
    def test_metrics_split_time_and_count_calls_across_resumes(self):
        events = [event("queued", 0.0), event("agent_model", 1.0, phase="start"),
                  event("agent_model", 4.0, phase="end", usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "reported_cost": 0.01}),
                  event("agent_tool", 4.0, phase="start", id="a"), event("agent_tool", 6.0, phase="end", id="a", failed=True),
                  event("agent_context_compacted", 6.5), event("agent_recovery", 6.6),
                  event("resumed", 7.0), event("agent_model", 7.0, phase="start"),
                  event("agent_model", 9.0, phase="end", usage={"input_tokens": 50, "total_tokens": 50}), event("completed", 10.0)]
        measured = bench.metrics("completed", {"changed_files": ["a.py"], "agent": {"model_calls": 1}}, events)
        self.assertEqual((measured["model_calls"], measured["tool_calls"], measured["failed_tool_calls"]), (2, 1, 1))
        self.assertEqual((measured["seconds"], measured["model_seconds"], measured["tool_seconds"], measured["harness_seconds"]), (10.0, 5.0, 2.0, 3.0))
        self.assertEqual((measured["total_tokens"], measured["cost"], measured["compactions"], measured["recoveries"]), (170, 0.01, 1, 1))

    def test_invariants_catch_encoding_damage_and_mixed_line_endings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.tsx").write_bytes(b"one\r\ntwo\nthree\r\n")
            (root / "b.tsx").write_bytes("price — quote".encode("cp1252"))
            (root / "c.ts").write_bytes(b"x\r\ny\r\n")
            source = {"a.tsx": "one\r\nthree\r\n", "b.tsx": "price", "c.ts": "x\r\n"}
            problems = bench.invariants(source, root, ["a.tsx", "b.tsx", "c.ts"])
        self.assertEqual(problems, ["a.tsx: 1 LF line endings mixed into a CRLF file", "b.tsx: not UTF-8"])

    def test_fixture_task_runs_isolated_and_is_scored(self):
        spec = next(task for task in json.loads(SUITE.read_text(encoding="utf-8"))["tasks"] if task["name"] == "fix-discount")
        fixture = SUITE.parent / spec["project"] / "pricing.py"
        untouched = fixture.read_bytes()
        script = Scripted(tool("e", "edit_file", path="pricing.py", before="price * percent / 100", after="price - price * percent / 100"),
                          ModelResponse("Fixed the discount.", []))
        with patch("dikdik.engine.CompatibleAgentModel", return_value=script):
            row = bench.run_task(spec, CONFIG, SUITE.parent)
        self.assertTrue(row["passed"], row)
        self.assertEqual(row["changed_files"], ["pricing.py"])
        self.assertEqual(row["model_calls"], 2)
        self.assertEqual(row["invariant_problems"], [])
        self.assertTrue(all(check["passed"] for check in row["checks"]))
        self.assertEqual(fixture.read_bytes(), untouched)  # the fixture itself is never edited

    def test_questions_are_answered_so_runs_never_wait_for_a_person(self):
        spec = next(task for task in json.loads(SUITE.read_text(encoding="utf-8"))["tasks"] if task["name"] == "fix-discount")
        script = Scripted(tool("q", "ask_question", question="Should the discount round down?"),
                          tool("e", "edit_file", path="pricing.py", before="price * percent / 100", after="price - price * percent / 100"),
                          ModelResponse("Fixed the discount.", []))
        with patch("dikdik.engine.CompatibleAgentModel", return_value=script):
            row = bench.run_task({**spec, "deadline_seconds": 60}, CONFIG, SUITE.parent)
        self.assertTrue(row["passed"], row)
        self.assertLess(row["seconds"], 60)

    def test_unfinished_work_fails_its_checks(self):
        spec = next(task for task in json.loads(SUITE.read_text(encoding="utf-8"))["tasks"] if task["name"] == "fix-discount")
        with patch("dikdik.engine.CompatibleAgentModel", return_value=Scripted(ModelResponse("I could not find it.", []))):
            row = bench.run_task(spec, CONFIG, SUITE.parent)
        self.assertFalse(row["passed"])
        self.assertFalse(row["checks"][0]["passed"])

    def test_compare_reports_changes_per_task_and_overall(self):
        row = {"name": "t", "state": "completed", "passed": False, "seconds": 100.0, "model_calls": 40, "tool_calls": 1,
               "failed_tool_calls": 0, "compactions": 0, "total_tokens": 900, "cost": None}
        before = {"results": [row], "summary": bench.summarize([row])}
        better = {**row, "passed": True, "seconds": 20.0, "model_calls": 6, "total_tokens": 200}
        after = {"results": [better], "summary": bench.summarize([better])}
        text = bench.compare(before, after)
        self.assertIn("40->6", text)
        self.assertIn("pass_rate: 0.0 -> 1.0", text)

    def test_overhead_needs_no_model(self):
        measured = bench.overhead(files=12, steps=3)
        self.assertEqual(measured["state"], "completed")
        self.assertEqual(measured["model_calls"], 7)
        self.assertGreater(measured["checkpoint_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
