"""Phase 3: effort in proportion to the request, checked answers, richer stuck detection, lint reflection."""

import threading
import unittest

from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.reasoning import (DEEP, SIMPLE, StuckDetector, answer_problems, classify_effort, effort_options,
                              lint_findings, observe_evidence, resolve_effort)

DOC = {"type": "function", "function": {"name": "read_document", "parameters": {"type": "object"}}}
FETCH = {"type": "function", "function": {"name": "web_fetch", "parameters": {"type": "object"}}}
STATS = {"type": "function", "function": {"name": "statistics", "parameters": {"type": "object"}}}
TRACKER = {"type": "function", "function": {"name": "task_tracker", "parameters": {"type": "object"}}}


class Replies:
    def __init__(self, *replies):
        self.replies, self.seen, self.tools = list(replies), [], []

    def respond(self, messages, tools):
        self.seen.append(messages)
        self.tools.append([tool["function"]["name"] for tool in tools])
        return self.replies.pop(0)


def chat(model, objective, call=None, effort=None, tools=(DOC, FETCH, STATS, TRACKER)):
    events = []
    result = run_agent({}, objective, model, None, lambda k, d: events.append((k, d)), threading.Event(),
                       extra_tools=list(tools), extra_call=call or (lambda name, args: {"text": "nothing"}),
                       read_only=True, conversation=True, effort=effort)
    return result, events


class EffortTests(unittest.TestCase):
    def test_short_factual_questions_are_simple_and_work_is_deep(self):
        for text in ("What is the capital of Kenya?", "Who wrote Things Fall Apart?", "define osmosis", "is 91 prime?"):
            self.assertEqual(classify_effort(text), SIMPLE, text)
        for text in ("Analyse my survey data against the objectives", "Write chapter four of my thesis",
                     "Fix the login bug", "Compare these two papers and draft a review?", "Explain photosynthesis",
                     "What is " + "very " * 80 + "long?"):
            self.assertEqual(classify_effort(text), DEEP, text)
        self.assertEqual(classify_effort("What is this?", attachments=True), DEEP)

    def test_a_request_field_overrides_the_heuristic(self):
        self.assertEqual(resolve_effort("What is 2+2?", None), (SIMPLE, "auto"))
        self.assertEqual(resolve_effort("What is 2+2?", "deep"), (DEEP, "user"))
        self.assertEqual(resolve_effort("Write a report", "simple"), (SIMPLE, "user"))
        self.assertEqual(resolve_effort("Write a report", "bogus"), (DEEP, "auto"))

    def test_simple_requests_ask_for_low_reasoning_only_when_the_profile_set_none(self):
        self.assertEqual(effort_options({}, SIMPLE, "o4-mini")["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", effort_options({}, SIMPLE, "llama3.1"))  # the provider might reject it
        self.assertEqual(effort_options({}, SIMPLE, "llama3.1")["adaptive_effort"], "low")
        self.assertEqual(effort_options({"reasoning_effort": "high"}, SIMPLE, "o4-mini")["reasoning_effort"], "high")
        self.assertNotIn("reasoning_effort", effort_options({}, DEEP, "o4-mini"))

    def test_simple_question_has_no_plan_tool_and_one_model_call(self):
        model = Replies(ModelResponse("Nairobi.", []))
        result, events = chat(model, "What is the capital of Kenya?")
        self.assertEqual(result.state, "completed")
        self.assertEqual(result.model_calls, 1)
        self.assertNotIn("task_tracker", model.tools[0])
        self.assertIn(("effort", {"level": "simple", "source": "auto"}), events)
        self.assertIn("short factual question", model.seen[0][-1]["content"])
        self.assertNotIn("short factual question", model.seen[0][0]["content"])  # the system prompt stays cache-stable

    def test_user_override_makes_a_short_question_deep(self):
        model = Replies(ModelResponse("Nairobi.", []))
        result, events = chat(model, "What is the capital of Kenya?", effort="deep")
        self.assertIn("task_tracker", model.tools[0])
        self.assertIn(("effort", {"level": "deep", "source": "user"}), events)


class AnswerCheckTests(unittest.TestCase):
    def test_deterministic_problems_name_each_unsupported_claim(self):
        evidence = {"sources": set(), "numbers": set(), "names": set()}
        observe_evidence(evidence, '{"content": "WHO reports 118.5 million donations", "url": "https://www.who.int/blood/"}')
        observe_evidence(evidence, '{"n": 91, "cronbach_alpha": 0.8123, "saved": "report.docx"}')
        answer = ("Alpha was 0.81 (n = 91) and the mean score was 3.47. See https://who.int/blood and "
                  "https://doi.org/10.1000/fake. Your report is in report.docx and the tables are in tables.xlsx.")
        problems = answer_problems(answer, evidence, used_tools=True)
        self.assertEqual({(p["kind"], p["item"]) for p in problems},
                         {("number", "3.47"), ("source", "https://doi.org/10.1000/fake"), ("deliverable", "tables.xlsx")})

    def test_numbers_are_only_checked_when_the_answer_claims_analysis_results(self):
        evidence = {"sources": set(), "numbers": set(), "names": set()}
        self.assertEqual(answer_problems("Water boils at 100.0 degrees at sea level.", evidence, used_tools=True), [])
        self.assertEqual(answer_problems("The mean was 3.47.", evidence, used_tools=False), [])

    def test_a_cited_source_that_was_never_fetched_gets_one_correction_turn(self):
        model = Replies(
            ModelResponse("", [ToolCall("f", "web_fetch", {"url": "https://who.int/blood"})]),
            ModelResponse("Donations reached 118.5 million (https://who.int/blood, https://made.up/source).", []),
            ModelResponse("Donations reached 118.5 million (https://who.int/blood).", []))
        result, events = chat(model, "Research global blood donation figures and summarise the evidence",
                              call=lambda name, args: {"content": "WHO: 118.5 million donations", "url": args.get("url")})
        self.assertEqual(result.state, "completed")
        self.assertEqual(result.model_calls, 3)
        correction = model.seen[2][-1]["content"]
        self.assertIn("https://made.up/source", correction)
        checks = [d for k, d in events if k == "answer_check"]
        self.assertEqual(checks[0]["round"], 1)
        messages = [d["content"] for k, d in events if k == "message"]
        self.assertEqual(messages[-1], "Donations reached 118.5 million (https://who.int/blood).")

    def test_after_two_rounds_the_answer_is_delivered_with_an_honest_caveat(self):
        bad = ModelResponse("Per my analysis the mean was 4.52 (see summary.docx).", [])
        model = Replies(ModelResponse("", [ToolCall("s", "statistics", {"path": "s.xlsx"})]), bad, bad, bad)
        result, events = chat(model, "Analyse the survey and report the mean score", call=lambda name, args: {"n": 12, "mean": 3.1})
        self.assertEqual(result.state, "completed")
        self.assertEqual(result.model_calls, 4)
        final = [d["content"] for k, d in events if k == "message"][-1]
        self.assertTrue(final.startswith("Per my analysis the mean was 4.52"))
        self.assertIn("could not verify", final)
        self.assertIn("4.52", final.split("could not verify")[1])
        self.assertEqual([d["round"] for k, d in events if k == "answer_check"], [1, 2, 3])

    def test_passing_answers_and_simple_questions_cost_no_extra_call(self):
        model = Replies(ModelResponse("", [ToolCall("s", "statistics", {"path": "s.xlsx"})]),
                        ModelResponse("The mean was 3.10 across 12 respondents.", []))
        result, _ = chat(model, "Analyse the survey and report the mean score", call=lambda name, args: {"n": 12, "mean": 3.1})
        self.assertEqual(result.model_calls, 2)
        model = Replies(ModelResponse("It is 4.52 per https://made.up/x.", []))
        result, events = chat(model, "What is the mean?")
        self.assertEqual(result.model_calls, 1)
        self.assertFalse([d for k, d in events if k == "answer_check"])


class StuckTests(unittest.TestCase):
    def test_signatures_ignore_key_order(self):
        detector = StuckDetector()
        self.assertIsNone(detector.action("read", {"a": 1, "b": 2}, {"x": 1}, False))
        self.assertIsNone(detector.action("read", {"b": 2, "a": 1}, {"x": 2}, False))
        self.assertEqual(detector.action("read", {"a": 1, "b": 2}, {"x": 3}, False), ("warn", "repeat"))

    def test_same_action_same_result_warns_at_three_nudges_at_four_and_stops_at_five(self):
        detector = StuckDetector()
        levels = [detector.action("search", {"q": "x"}, {"hits": []}, False) for _ in range(5)]
        self.assertEqual(levels, [None, None, ("warn", "repeat"), ("nudge", "same_result"), ("stop", "repeat")])

    def test_same_failing_action_is_nudged_at_three_then_stopped(self):
        detector = StuckDetector()
        levels = [detector.action("edit", {"p": 1}, {"error": "no match"}, True) for _ in range(5)]
        self.assertEqual(levels[2], ("nudge", "same_error"))
        self.assertEqual(levels[4][0], "stop")

    def test_alternation_and_monologue(self):
        detector = StuckDetector()
        levels = [detector.action(name, {}, {"r": name}, False) for name in "ababab"]
        self.assertEqual(levels[-1], ("nudge", "alternation"))
        levels = [detector.action(name, {}, {"r": name}, False) for name in "ab"]
        self.assertEqual(levels[-1][0], "stop")
        detector = StuckDetector()
        self.assertEqual([detector.monologue("thinking") for _ in range(3)][-1], ("nudge", "monologue"))

    def test_different_work_is_never_stuck(self):
        detector = StuckDetector()
        self.assertFalse([level for i in range(20) if (level := detector.action("read", {"path": f"f{i}"}, {"t": i}, False))])

    def test_a_run_that_keeps_repeating_pauses_and_asks(self):
        model = Replies(*[ModelResponse("", [ToolCall(f"s{i}", "search_text", {"query": "needle"})]) for i in range(6)],
                        ModelResponse("Done", []))
        events = []
        result = run_agent({"a.py": "x = 1\n"}, "Find where the needle is configured", model, None,
                           lambda k, d: events.append((k, d)), threading.Event(), read_only=True)
        self.assertEqual(result.state, "paused")
        self.assertIn("How should I proceed", result.error)
        levels = [d["level"] for k, d in events if k == "stuck"]
        self.assertEqual(levels, ["warning", "nudge", "stop"])


class LintReflectionTests(unittest.TestCase):
    def test_lint_findings_keep_only_errors_in_changed_files(self):
        output = "src/a.ts(3,1): error TS2304: Cannot find name 'x'.\nsrc/other.ts(1,1): error TS1: old\nFound 2 errors."
        found = lint_findings({"status": "failed", "output": output, "label": "npm run typecheck"}, ["src/a.ts"])
        self.assertEqual(found["status"], "failed")
        self.assertEqual(found["errors"], ["src/a.ts(3,1): error TS2304: Cannot find name 'x'."])
        self.assertEqual(lint_findings({"status": "failed", "output": output}, ["src/b.ts"])["status"], "passed")
        self.assertEqual(lint_findings({"status": "not_run"}, ["src/a.ts"])["status"], "not_run")

    def test_errors_in_edited_files_are_fed_back_at_most_three_times(self):
        original = {"app.ts": "export const a = 1;\n", "tests/test_app.py": "assert True\n"}
        edits = [ModelResponse("", [ToolCall(f"e{i}", "edit_file", {"path": "app.ts", "before": f"a = {i + 1}", "after": f"a = {i + 2}"})])
                 for i in range(5)]
        model = Replies(*edits, ModelResponse("", [ToolCall("t", "run_tests", {})]), ModelResponse("", [ToolCall("f", "finish", {})]))
        linted, events = [], []

        def lint(files, paths):
            linted.append(paths)
            return {"status": "failed", "errors": ["app.ts(1,14): error TS2322: Type 'number' is not assignable"], "label": "tsc"}
        result = run_agent(original, "Bump the constant in app.ts five times", model,
                           lambda files: {"kind": "unittest", "status": "passed"}, lambda k, d: events.append((k, d)),
                           threading.Event(), lint=lint)
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(linted, [["app.ts"]] * 3)
        reflections = [d for k, d in events if k == "lint_reflection"]
        self.assertEqual([d["reflection"] for d in reflections], [1, 2, 3])
        fed = [m["content"] for m in model.seen[1] if m.get("role") == "user" and "TS2322" in str(m.get("content"))]
        self.assertTrue(fed)


if __name__ == "__main__":
    unittest.main()
