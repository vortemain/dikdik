"""Requirements traceability and the release gate are computed from evidence, not model claims."""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik import engineering
from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.store import Store

AWS = "AKIA" + "Q3EGJ7RZ5XN2LW4P"
PASSING = {"kind": "unittest", "status": "passed", "output": "test_add (test_calc.Calc.test_add) ... ok\nRan 1 test\nOK"}


def requirement(ident="REQ-1", priority="must", kind="explicit", tests=("test_add",)):
    return {"id": ident, "description": "Addition returns the sum", "kind": kind, "priority": priority,
            "acceptance": ["add(2, 3) == 5"], "tests": list(tests)}


class RequirementTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(engineering.parse_requirements([requirement()])[0]["tests"], ["test_add"])
        for bad, reason in [([requirement(), requirement()], "unique"), ([requirement(ident="R1")], "REQ-1"),
                            ([requirement(kind="optional", priority="must")], "optional"),
                            ([{**requirement(), "status": "verified"}], "description"),  # status is never accepted
                            ([requirement(tests=["x" * 400])], "tests")]:
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                engineering.parse_requirements(bad)

    def test_trace_needs_named_tests_in_a_current_passing_run(self):
        reqs = engineering.parse_requirements([requirement(), requirement("REQ-2", tests=["test_subtract"]),
                                               requirement("REQ-3", tests=[])])
        traced = {r["id"]: r["status"] for r in engineering.trace(reqs, [PASSING], tests_current=True)}
        self.assertEqual(traced, {"REQ-1": "VERIFIED", "REQ-2": "INCONCLUSIVE", "REQ-3": "UNVERIFIED"})
        stale = {r["id"]: r["status"] for r in engineering.trace(reqs, [PASSING], tests_current=False)}
        self.assertEqual(stale["REQ-1"], "NOT_RUN")
        # A name only matches as a whole test name, not as a substring of another.
        self.assertEqual(engineering.trace(engineering.parse_requirements([requirement(tests=["test_ad"])]),
                                           [PASSING], True)[0]["status"], "INCONCLUSIVE")


FIXTURES = Path(__file__).parent / "fixtures" / "test_output"


class TestResultParsingTests(unittest.TestCase):
    """Real runner output captured from unittest -v, cargo test and bun's JUnit reporter."""

    def test_unittest_verbose_including_a_docstring_line(self):
        results = engineering.test_results((FIXTURES / "unittest_verbose.txt").read_text(encoding="utf-8"))
        self.assertTrue({"test_add", "Calc.test_add", "test_calc.Calc.test_add"} <= results["passed"])
        self.assertTrue({"test_sub", "test_mul"} <= results["failed"])  # FAIL and skipped are never passes
        self.assertNotIn("test_sub", results["passed"])

    def test_cargo(self):
        results = engineering.test_results((FIXTURES / "cargo.txt").read_text(encoding="utf-8"))
        self.assertEqual(results["passed"], {"tests::adds", "adds"})
        self.assertTrue({"tests::subtracts", "subtracts"} <= results["failed"])

    def test_bun_junit(self):
        results = engineering.junit_results((FIXTURES / "bun_junit.xml").read_text(encoding="utf-8"))
        self.assertEqual(results["passed"], {"adds numbers", "calc.adds numbers", "calc > adds numbers"})
        self.assertIn("subtracts", results["failed"])
        self.assertEqual(engineering.junit_results("<not xml"), {"passed": set(), "failed": set()})

    def test_pytest_go_and_js_text(self):
        output = "\n".join(["tests/test_calc.py::TestCalc::test_add PASSED                [ 50%]",
                            "tests/test_calc.py::test_div FAILED                          [100%]",
                            "--- PASS: TestParse (0.00s)", "    --- FAIL: TestParse/empty (0.00s)",
                            "  ✓ renders the header (3 ms)", "(fail) form > rejects blank [0.20ms]"])
        results = engineering.test_results(output)
        self.assertTrue({"test_add", "TestCalc::test_add", "TestParse", "renders the header"} <= results["passed"])
        self.assertTrue({"test_div", "empty", "rejects blank", "form > rejects blank"} <= results["failed"])

    def test_structured_runner_results_count_and_text_mentions_do_not(self):
        reqs = engineering.parse_requirements([requirement(tests=["adds numbers"]), requirement("REQ-2", tests=["test_add"])])
        mention_only = {"kind": "tests", "status": "passed", "output": "Ran 1 test. The name test_add appears here.\n"}
        bun = {"kind": "tests", "status": "passed", "output": " 1 pass\n",
               "runs": [{"kind": "bun_test", "status": "passed", "tests": {"passed": ["adds numbers"], "failed": []}}]}
        statuses = {r["id"]: r["status"] for r in engineering.trace(reqs, [mention_only, bun], True)}
        self.assertEqual(statuses, {"REQ-1": "VERIFIED", "REQ-2": "INCONCLUSIVE"})
        statuses = {r["id"]: (r["status"], r["evidence"]) for r in engineering.trace(reqs, [mention_only], True)}
        self.assertEqual(statuses["REQ-2"][0], "INCONCLUSIVE")  # a bare mention is not a reported pass
        self.assertIn("no per-test results", statuses["REQ-2"][1])

    @unittest.skipUnless(__import__("shutil").which("bun"), "bun is not installed")
    def test_local_bun_run_reports_structured_results_and_leaves_no_file(self):
        from dikdik.local_exec import run_tests
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "calc.test.ts").write_text('import { test, expect } from "bun:test";\n'
                                               'test("adds numbers", () => expect(1 + 1).toBe(2));\n', encoding="utf-8")
            result = run_tests(root, [{"kind": "bun_test", "label": "bun test"}], threading.Event())
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["tests"]["passed"], ["adds numbers"])
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["calc.test.ts"])


class GateTests(unittest.TestCase):
    base = {"calc.py": "def add(a, b):\n    return a - b\n"}
    fixed = {"calc.py": "def add(a, b):\n    return a + b\n"}

    def gate(self, files, checks, current, reqs=(), available=True, base=None):
        return engineering.release_gate(engineering.parse_requirements(list(reqs)), checks, current, available,
                                        files, base or self.base)

    def test_decisions(self):
        self.assertEqual(self.gate(self.fixed, [PASSING], True, [requirement()])["decision"], "APPROVED")
        self.assertEqual(self.gate(self.fixed, [], False)["decision"], "BLOCKED")
        self.assertEqual(self.gate(self.fixed, [{"kind": "unittest", "status": "failed"}], False)["decision"], "REJECTED")
        self.assertEqual(self.gate(self.fixed, [PASSING], True, [requirement(tests=["test_missing"])])["decision"],
                         "APPROVED_WITH_DOCUMENTED_RISK")
        self.assertEqual(self.gate(self.fixed, [], False, available=False)["decision"], "APPROVED_WITH_DOCUMENTED_RISK")

    def test_an_introduced_credential_rejects_but_an_existing_one_is_not_this_turns_fault(self):
        leaked = {"calc.py": self.fixed["calc.py"] + f'KEY = "{AWS}"\n'}
        report = self.gate(leaked, [PASSING], True)
        self.assertEqual(report["decision"], "REJECTED")
        self.assertEqual(report["security_findings"][0]["rule"], "SEC001")
        self.assertNotIn(AWS, json.dumps(report))
        already = {"calc.py": self.base["calc.py"] + f'KEY = "{AWS}"\n'}
        edited = {"calc.py": already["calc.py"].replace("a - b", "a + b")}
        self.assertEqual(self.gate(edited, [PASSING], True, base=already)["decision"], "APPROVED")


class QualityAndAccessibilityGateTests(unittest.TestCase):
    def gate_for(self, files, base, **evidence):
        report = engineering.release_gate([], [PASSING], True, True, files, base, **evidence)
        return {g["gate"]: g["status"] for g in report["gates"]}, report

    def test_introduced_quality_findings_are_a_documented_risk(self):
        base = {"svc.py": "import requests\n"}
        gates, report = self.gate_for({"svc.py": "import requests\nrequests.get(url)\n"}, base)
        self.assertEqual(gates["quality"], "RISK")
        self.assertEqual(report["decision"], "APPROVED_WITH_DOCUMENTED_RISK")
        self.assertEqual(report["quality_findings"][0]["rule"], "REL001")
        gates, _ = self.gate_for({"svc.py": "import requests\nrequests.get(url, timeout=5)\n"}, base)
        self.assertEqual(gates["quality"], "PASS")

    def test_accessibility_gate_uses_static_html_and_rendered_audits(self):
        base = {"index.html": '<html lang="en"><title>t</title><body><p>hi</p></body></html>'}
        broken = {"index.html": base["index.html"].replace("<p>hi</p>", '<p>hi</p><img src="a.png">')}
        self.assertEqual(self.gate_for(broken, base)[0]["accessibility"], "RISK")
        fine = {"index.html": base["index.html"].replace("hi", "hello")}
        self.assertEqual(self.gate_for(fine, base)[0]["accessibility"], "RISK")  # UI changed, nothing rendered
        audited = {"accessibility": [{"label": "desktop", "serious": 0, "moderate": 2}]}
        self.assertEqual(self.gate_for(fine, base, preview=audited)[0]["accessibility"], "PASS")
        failing = {"accessibility": [{"label": "mobile", "serious": 1, "moderate": 0}]}
        self.assertEqual(self.gate_for(fine, base, preview=failing)[0]["accessibility"], "RISK")
        self.assertEqual(self.gate_for({"a.py": "x = 2\n"}, {"a.py": "x = 1\n"})[0]["accessibility"], "NOT_APPLICABLE")

    def test_static_accessibility_counts_only_what_the_turn_introduced(self):
        base = {"page.html": '<html lang="en"><title>t</title><img src="old.png"></html>'}
        report = engineering.quality({"page.html": base["page.html"].replace("t</title>", "T</title>")}, base)
        self.assertEqual(report["accessibility"]["serious"], 0)  # the missing alt predates this turn

    def test_performance_gate(self):
        files, base = {"a.py": "x = 2\n"}, {"a.py": "x = 1\n"}
        ok = {"results": [{"name": "startup", "status": "PASS", "detail": "120 ms <= 500 ms"}]}
        slow = {"results": [{"name": "startup", "status": "OVER_BUDGET", "detail": "startup p95 900 ms > 500 ms"}]}
        self.assertEqual(self.gate_for(files, base, performance=ok)[0]["performance"], "PASS")
        gates, report = self.gate_for(files, base, performance=slow)
        self.assertEqual(gates["performance"], "RISK")
        self.assertIn("900 ms", next(g["detail"] for g in report["gates"] if g["gate"] == "performance"))
        self.assertNotIn("performance", self.gate_for(files, base)[0])  # nothing measured, no claim


class AgentIntegrationTests(unittest.TestCase):
    original = {"calc.py": "def add(a, b):\n    return a - b\n", "test_calc.py": "import calc\n"}

    def drive(self, *batches):
        sequence = iter(range(1, 100))
        script = iter([[ToolCall(str(next(sequence)), name, args) for name, args in batch] for batch in batches])
        class Model:
            def respond(self, messages, tools):
                return ModelResponse("", next(script), 1)
        events = []
        outcome = run_agent(self.original, "Fix add", Model(), lambda files: dict(PASSING),
                            lambda kind, data: events.append((kind, data)), threading.Event())
        return outcome, events

    def test_traced_change_completes_with_an_approved_gate(self):
        outcome, events = self.drive(
            [("record_requirements", {"requirements": [requirement()]})],
            [("edit_file", {"path": "calc.py", "before": "a - b", "after": "a + b"})],
            [("run_tests", {})], [("release_gate", {})], [("finish", {})])
        self.assertEqual(outcome.state, "completed")
        self.assertEqual(outcome.release_gate["decision"], "APPROVED")
        self.assertEqual(outcome.release_gate["requirements"][0]["status"], "VERIFIED")
        self.assertEqual(outcome.transcript["requirements"][0]["id"], "REQ-1")
        gate_result = next(d for k, d in events if k == "tool" and d.get("phase") == "end" and d["name"] == "release_gate")
        self.assertEqual(gate_result["observation"]["decision"], "APPROVED")

    def test_quality_check_reports_introduced_reliability_findings(self):
        outcome, events = self.drive(
            [("edit_file", {"path": "calc.py", "before": "return a - b", "after": "return float(a.amount) + b"})],
            [("quality_check", {})], [("run_tests", {})], [("finish", {})])
        report = next(d for k, d in events if k == "tool" and d.get("phase") == "end" and d["name"] == "quality_check")
        self.assertEqual([f["rule"] for f in report["observation"]["findings"]], ["I18N003"])
        self.assertEqual(outcome.state, "completed")  # a quality risk is documented, not blocking
        self.assertEqual(outcome.release_gate["decision"], "APPROVED_WITH_DOCUMENTED_RISK")

    def test_finish_is_refused_when_the_change_adds_a_credential(self):
        outcome, events = self.drive(
            [("edit_file", {"path": "calc.py", "before": "a - b", "after": f'a + b\nKEY = "{AWS}"'})],
            [("run_tests", {})], [("finish", {})], [("security_scan", {})])
        self.assertNotEqual(outcome.state, "completed")
        refusal = next(d for k, d in events if k == "tool" and d.get("phase") == "end" and d["name"] == "finish")
        self.assertIn("release gate rejects", refusal["observation"]["error"])
        scan = next(d for k, d in events if k == "tool" and d.get("phase") == "end" and d["name"] == "security_scan")
        self.assertEqual(scan["observation"]["summary"]["critical"], 1)


class MutationHardeningTests(unittest.TestCase):
    """Assertions added for mutants scripts/mutation_test.py showed the suite did not kill."""

    def test_kind_and_priority_are_checked_independently_and_limits_are_exact(self):
        for bad in ({"kind": "bogus"}, {"priority": "bogus"}):
            with self.assertRaises(ValueError):
                engineering.parse_requirements([{**requirement(), **bad}])
        engineering.parse_requirements([requirement(tests=[f"t{i}" for i in range(20)])])
        engineering.parse_requirements([{**requirement(), "acceptance": [f"a{i}" for i in range(10)]}])
        engineering.parse_requirements([requirement(tests=["x" * 300])])
        for bad in ({"tests": [f"t{i}" for i in range(21)]}, {"acceptance": [f"a{i}" for i in range(11)]}, {"tests": ["x" * 301]}):
            with self.assertRaises(ValueError):
                engineering.parse_requirements([{**requirement(), **bad}])

    def test_scan_results_are_bounded(self):
        many = {f"m{i}.py": f'KEY = "AKIA{i:016d}"\n'.replace("0", "Q") for i in range(60)}
        report = engineering.scan(many, {})
        self.assertEqual((len(report["findings"]), report["truncated"]), (50, True))
        skipped = engineering.scan({f"b{i}.bin": "\0" for i in range(25)}, {})
        self.assertEqual(len(skipped["skipped"]), 20)

    def test_pytest_dotted_name_and_missing_test_message(self):
        results = engineering.test_results("tests/test_calc.py::TestCalc::test_add PASSED")
        self.assertIn("TestCalc.test_add", results["passed"])
        traced = engineering.trace(engineering.parse_requirements([requirement(tests=["test_add", "test_gone"])]), [PASSING], True)
        self.assertEqual(traced[0]["evidence"], "Not reported as passing: test_gone")

    def test_a_later_syntax_check_is_never_the_evidence(self):
        syntax = {"kind": "python_syntax", "status": "passed", "output": ""}
        traced = engineering.trace(engineering.parse_requirements([requirement()]), [PASSING, syntax], True)
        self.assertEqual(traced[0]["status"], "VERIFIED")

    def test_measurement_defaults_and_failure_details(self):
        from tests.test_performance import FakeClock, passing
        result = engineering.measure(passing, {}, {"name": "n", "command": "c"}, clock=FakeClock([0.001] * 5))
        self.assertEqual(result["runs"], 5)
        result = engineering.measure(passing, {}, {"name": "n", "command": "c", "runs": 2}, clock=FakeClock([0.0001234, 0.0001244]))
        self.assertEqual(result["median_ms"], round((0.123 + 0.124) / 2, 3))  # three decimals, not 0.1235
        failing = lambda *a: {"status": "failed", "exit_code": 7, "output": "x" * 5000}
        failed = engineering.measure(failing, {}, {"name": "n", "command": "c"}, clock=FakeClock([0.1]))
        self.assertEqual((failed["error"], len(failed["output"])), ("Run 1 exited 7", 4000))

    def test_tool_schemas_reject_unexpected_arguments(self):
        outcome, events = AgentIntegrationTests().drive(
            [("record_requirements", {"requirements": [requirement()], "status": "verified"})], [("finish", {})])
        first = next(d for k, d in events if k == "tool" and d.get("phase") == "end")
        self.assertTrue(first["failed"])
        self.assertEqual(outcome.transcript["requirements"], [])


class PersistenceTests(unittest.TestCase):
    def test_requirements_event_is_stored_and_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "state.sqlite3")
            with store.connect() as db:
                db.execute("INSERT INTO tasks(id, created, state, request) VALUES('t', 0, 'running', '{}')")
            store.event("t", "requirements", {"requirements": engineering.parse_requirements(
                [requirement("REQ-2"), requirement("REQ-10"), requirement("REQ-1")])})
            self.assertEqual([r["id"] for r in store.requirements("t")["requirements"]], ["REQ-1", "REQ-2", "REQ-10"])
            store.event("t", "requirements", {"requirements": engineering.parse_requirements([requirement()])})
            stored = store.requirements("t")
            self.assertEqual((stored["revision"], len(stored["requirements"])), (2, 1))
            with self.assertRaises(KeyError):
                store.requirements("missing")
            store.delete("t")
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM requirements").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
