"""Performance measurement: budgets, baselines and regressions from real timings."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik import engineering
from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.control import assess, needs_approval
from dikdik.store import Store


class FakeClock:
    """Each run takes the next duration (seconds) from the script."""

    def __init__(self, durations):
        self.durations, self.now, self.started = list(durations), 0.0, False

    def __call__(self):
        if self.started:
            self.now += self.durations.pop(0)
        self.started = not self.started
        return self.now


class MemoryBaselines(dict):
    def put(self, name, record):
        self[name] = record


def passing(files, command, timeout):
    return {"status": "passed", "exit_code": 0, "output": "", "changed": {}}


class MeasureTests(unittest.TestCase):
    def measure(self, durations, baselines=None, **arguments):
        return engineering.measure(passing, {}, {"name": "startup", "command": "app --check", **arguments},
                                   baselines, clock=FakeClock(durations))

    def test_statistics_and_budget(self):
        result = self.measure([0.100, 0.120, 0.110, 0.300, 0.105], runs=5, budget_ms=250)
        self.assertEqual((result["min_ms"], result["median_ms"], result["p95_ms"], result["max_ms"]), (100.0, 110.0, 300.0, 300.0))
        self.assertEqual(result["results"][0]["status"], "OVER_BUDGET")  # p95 is the budgeted figure
        self.assertEqual(result["status"], "RISK")
        self.assertEqual(self.measure([0.1] * 5, runs=5, budget_ms=250)["status"], "PASS")

    def test_baseline_is_saved_only_on_request_and_only_when_passing(self):
        baselines = MemoryBaselines()
        self.measure([0.1] * 3, baselines, runs=3)
        self.assertEqual(baselines, {})
        self.measure([0.5] * 3, baselines, runs=3, budget_ms=200, save_baseline=True)
        self.assertEqual(baselines, {})  # over budget: not a baseline
        saved = self.measure([0.1] * 3, baselines, runs=3, save_baseline=True)
        self.assertTrue(saved["baseline_saved"])
        self.assertEqual(baselines["startup"]["median_ms"], 100.0)

    def test_regression_needs_both_the_ratio_and_the_noise_floor(self):
        baselines = MemoryBaselines(startup={"command": "app --check", "median_ms": 100.0})
        self.assertEqual(self.measure([0.118] * 3, baselines, runs=3)["status"], "PASS")    # within 20%
        self.assertEqual(self.measure([0.124] * 3, baselines, runs=3)["status"], "PASS")    # +24%, but within the 25 ms floor
        slow = self.measure([0.160] * 3, baselines, runs=3)
        self.assertEqual(slow["results"][0]["status"], "REGRESSION")
        tiny = MemoryBaselines(startup={"command": "app --check", "median_ms": 10.0})
        self.assertEqual(self.measure([0.020] * 3, tiny, runs=3)["status"], "PASS")       # 2x, but only 10 ms slower
        other = MemoryBaselines(startup={"command": "something else", "median_ms": 1.0})
        self.assertIn("different command", self.measure([0.5] * 3, other, runs=3)["results"][0]["detail"])

    def test_failed_or_writing_runs_are_not_measurements(self):
        failing = lambda *a: {"status": "failed", "exit_code": 2, "output": "boom"}
        result = engineering.measure(failing, {}, {"name": "x", "command": "c", "runs": 3}, clock=FakeClock([0.1]))
        self.assertEqual((result["status"], result["runs_completed"]), ("FAILED", 0))
        writing = lambda *a: {"status": "passed", "exit_code": 0, "changed": {"cache.json": "{}"}}
        with self.assertRaises(ValueError):
            engineering.measure(writing, {}, {"name": "x", "command": "c", "runs": 1}, clock=FakeClock([0.1]))
        with self.assertRaises(ValueError):
            engineering.measure(passing, {}, {"name": "bad/name", "command": "c"})

    def test_a_real_process_is_timed(self):
        from dikdik.local_exec import run
        with tempfile.TemporaryDirectory() as temp:
            def shell(files, command, timeout):
                return {**run(Path(temp), command, threading.Event(), timeout=timeout), "changed": {}}
            command = f'"{Path(sys.executable).as_posix()}" -c "import time; time.sleep(0.2)"'
            result = engineering.measure(shell, {}, {"name": "sleep", "command": command, "runs": 2, "budget_ms": 60000})
        self.assertEqual(result["status"], "PASS", result)
        self.assertGreaterEqual(result["min_ms"], 200)  # the sleep is really inside the measurement


class BaselineStoreTests(unittest.TestCase):
    def test_baselines_persist_per_project(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "state.sqlite3")
            first, second = engineering.Baselines(store, "C:/a"), engineering.Baselines(store, "C:/b")
            first.put("build", {"command": "make", "median_ms": 5.0})
            self.assertEqual(engineering.Baselines(store, "C:/a").get("build")["median_ms"], 5.0)
            self.assertIsNone(second.get("build"))


class ApprovalAndAgentTests(unittest.TestCase):
    def test_measurements_are_commands_for_approval_and_risk(self):
        call = ToolCall("1", "measure_performance", {"name": "n", "command": "curl https://example.com"})
        self.assertEqual(assess(call)[0], "high")
        self.assertTrue(needs_approval("ask", "medium", "measure_performance"))

    def test_the_agent_measures_and_the_gate_reports_it(self):
        original = {"calc.py": "def add(a, b):\n    return a - b\n", "test_calc.py": "import calc\n"}
        batches = iter([[ToolCall("1", "edit_file", {"path": "calc.py", "before": "a - b", "after": "a + b"})],
                        [ToolCall("2", "measure_performance", {"name": "add", "command": "python bench.py", "runs": 2, "budget_ms": 1})],
                        [ToolCall("3", "run_tests", {})], [ToolCall("4", "finish", {})]])
        class Model:
            def respond(self, messages, tools):
                return ModelResponse("", next(batches), 1)
        slow_shell = lambda files, command, timeout: (__import__("time").sleep(0.01), passing(files, command, timeout))[1]
        outcome = run_agent(original, "Fix add", Model(),
                            lambda files: {"kind": "unittest", "status": "passed", "output": "test_add (t.T.test_add) ... ok"},
                            lambda *_: None, threading.Event(), shell=slow_shell)
        self.assertEqual(outcome.state, "completed")
        performance = next(g for g in outcome.release_gate["gates"] if g["gate"] == "performance")
        self.assertEqual(performance["status"], "RISK")  # 10 ms runs against a 1 ms budget
        self.assertIn("budget 1 ms", performance["detail"])


if __name__ == "__main__":
    unittest.main()
