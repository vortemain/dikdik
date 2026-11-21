"""Phase 2: compaction keeps a typed rolling checkpoint, a sources ledger and every identifier."""

import json
import threading
import unittest

from dikdik.agent import ModelResponse, ToolCall, context_size, run_agent
from dikdik.context_memory import MARKER, compact, compact_prompt, identifiers
from tests.test_context_continuity import assert_protocol, group, memory


def history(objective, extra=()):
    return [{"role": "system", "content": "Policy"}, objective, *extra]


def checkpoint(messages):
    return memory(messages)["checkpoint"]


def text_of(messages):
    return "\n".join(m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content")) for m in messages) + \
        "\n".join(json.dumps(m.get("tool_calls", [])) for m in messages)


class CheckpointTests(unittest.TestCase):
    def test_checkpoint_has_typed_sections_and_a_deterministic_sources_ledger(self):
        objective = {"role": "user", "content": "Analyse my survey against the proposal objectives"}
        requirement = {"role": "user", "content": "Use the corrected sample of 91 respondents"}
        messages = history(objective, [requirement,
            *group("d", "read_document", {"path": "attachments/proposal.docx", "find": "Objectives"}, {"text": "Objectives: assess policy"}),
            *group("s", "statistics", {"path": "attachments/survey.xlsx", "analysis": "reliability", "columns": ["#11"]},
                   {"n": 91, "cronbach_alpha": 0.812, "note": "ok"}),
            *group("w", "web_fetch", {"url": "https://who.int/blood"}, {"content": "WHO: 118.5 million donations"}),
            *group("c", "create_document", {"path": "report.docx", "content": "# Report"}, {"saved": "report.docx"}),
            *group("e", "web_fetch", {"url": "https://fails.example"}, {"error": "unreachable"})])
        for i in range(10):
            messages += group(f"r{i}", "read_document", {"path": "attachments/proposal.docx", "offset": i * 9000}, {"text": "z" * 9000})
        self.assertTrue(compact(messages, objective, {"changed_files": []}, 40000, context_size))
        saved = checkpoint(messages)
        for key in ("objective", "requirements", "decisions", "work_state", "next_move", "sources", "key_numbers"):
            self.assertIn(key, saved)
        self.assertIn("survey", saved["objective"])
        self.assertTrue(any("91 respondents" in item for item in saved["requirements"]))
        sources = {(item["action"], item["target"]) for item in saved["sources"]}
        self.assertIn(("read", "attachments/proposal.docx"), sources)
        self.assertIn(("read", "attachments/survey.xlsx"), sources)
        self.assertIn(("fetched", "https://who.int/blood"), sources)
        self.assertIn(("changed", "report.docx"), sources)
        self.assertNotIn(("fetched", "https://fails.example"), sources)
        self.assertTrue(any("cronbach_alpha=0.812" in item for item in saved["key_numbers"]))
        assert_protocol(self, messages)

    def test_checkpoint_rolls_forward_in_place_across_compactions(self):
        objective = {"role": "user", "content": "Write the results chapter"}
        calls = []

        def summarize(older, previous, missing):
            calls.append(previous)
            if len(calls) == 1:
                return {"decisions": ["Use Spearman correlation"], "next_move": "Draft section 4.2"}
            return {"decisions": ["Report medians, not means"], "next_move": "Draft section 4.3"}
        messages = history(objective, group("a", "read_document", {"path": "data.xlsx"}, {"text": "x" * 60000}))
        messages += group("b", "read_document", {"path": "notes.docx"}, {"text": "small"})
        self.assertTrue(compact(messages, objective, {}, 40000, context_size, summarize=summarize))
        messages += group("c", "web_fetch", {"url": "https://doi.org/10.1000/xyz"}, {"content": "y" * 60000})
        messages += group("d", "read_document", {"path": "more.docx"}, {"text": "small"})
        self.assertTrue(compact(messages, objective, {}, 40000, context_size, summarize=summarize))
        saved = checkpoint(messages)
        self.assertEqual(saved["decisions"], ["Use Spearman correlation", "Report medians, not means"])
        self.assertEqual(saved["next_move"], "Draft section 4.3")
        self.assertIn("Use Spearman correlation", calls[1]["decisions"])  # the model updates the previous checkpoint
        sources = {(item["action"], item["target"]) for item in saved["sources"]}
        self.assertTrue({("read", "data.xlsx"), ("fetched", "https://doi.org/10.1000/xyz")} <= sources)
        self.assertEqual(len([m for m in messages if isinstance(m.get("content"), str) and m["content"].startswith(MARKER)]), 1)

    def test_identifier_audit_retries_once_then_keeps_what_is_still_missing(self):
        objective = {"role": "user", "content": "Summarise the evidence"}
        said = ("The key source is https://doi.org/10.1234/abc.5678 and the pilot had N = 137 with 42.5% uptake; "
                "see results/table2.csv for the breakdown. " + "Background discussion follows. " * 200)
        attempts = []

        def summarize(older, previous, missing):
            attempts.append(list(missing))
            return {"decisions": ["Cite 10.1234/abc.5678"]} if missing else {"decisions": ["Use the pilot data"]}
        messages = history(objective, [{"role": "assistant", "content": said}, {"role": "assistant", "content": "Reading the rest now."}])
        for i in range(8):
            messages += group(f"r{i}", "read_document", {"path": "big.pdf", "offset": i * 6000}, {"text": "q" * 6000})
        self.assertTrue(compact(messages, objective, {}, 40000, context_size, summarize=summarize, tail=20000))
        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[0])
        self.assertIn("results/table2.csv", attempts[1])
        survived = text_of(messages)
        for identifier in ("https://doi.org/10.1234/abc.5678", "137", "42.5%", "results/table2.csv"):
            self.assertIn(identifier, survived)

    def test_identifiers_finds_urls_dois_paths_and_meaningful_numbers(self):
        found = identifiers("See https://ex.org/a?b=1, doi 10.5555/q-1. File src/app.py, e.g. 3 items, 2026 total, 0.05 and 12%.")
        for item in ("https://ex.org/a?b=1", "10.5555/q-1", "src/app.py", "2026", "0.05", "12%"):
            self.assertIn(item, found)
        self.assertNotIn("3", found)
        self.assertNotIn("e.g", found)

    def test_compaction_that_frees_less_than_ten_percent_is_refused(self):
        objective = {"role": "user", "content": "R" * 30000}
        messages = history(objective, group("a", "read_file", {"path": "a.py"}, {"content": "tiny"}))
        before = list(messages)
        self.assertFalse(compact(messages, objective, {}, 40000, context_size))
        self.assertEqual(messages, before)

    def test_recent_turns_stay_verbatim_and_batches_stay_whole(self):
        objective = {"role": "user", "content": "Investigate"}
        messages = history(objective)
        for i in range(30):
            messages.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"a{i}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": f"a{i}.py"})}},
                {"id": f"b{i}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": f"b{i}.py"})}}]})
            messages.append({"role": "tool", "tool_call_id": f"a{i}", "content": "a" * 6000})
            messages.append({"role": "tool", "tool_call_id": f"b{i}", "content": "b" * 6000})
        limit = 320 * 1024
        self.assertTrue(compact(messages, objective, {}, limit // 2, context_size))
        self.assertLessEqual(context_size(messages), limit // 2)
        recent = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual(recent[-1], {"role": "tool", "tool_call_id": "b29", "content": "b" * 6000})
        self.assertGreaterEqual(sum(len(m["content"]) for m in recent), 60000)  # about 15-20k tokens kept verbatim
        assert_protocol(self, messages)

    def test_compact_prompt_matches_the_task_type(self):
        chat, code = compact_prompt("chat"), compact_prompt("code")
        self.assertNotIn("coding", chat)
        self.assertIn("conversation", chat)
        self.assertIn("sources", chat)
        self.assertIn("code", code)
        for key in ("objective", "requirements", "decisions", "work_state", "next_move", "key_numbers"):
            self.assertIn(key, chat)
            self.assertIn(key, code)


class MemoryFlushTests(unittest.TestCase):
    def test_model_is_asked_once_to_save_notes_shortly_before_compaction(self):
        notes_tool = {"type": "function", "function": {"name": "save_notes", "parameters": {"type": "object"}}}
        read_tool = {"type": "function", "function": {"name": "read_document", "parameters": {"type": "object"}}}

        class Model:
            def __init__(self):
                self.count, self.seen = 0, []

            def respond(self, messages, tools):
                if not tools:
                    return ModelResponse('{"next_move": "keep reading"}', [])
                self.seen.append(messages)
                self.count += 1
                if self.count > 12:
                    return ModelResponse("Done.", [])
                return ModelResponse("", [ToolCall(f"r{self.count}", "read_document", {"path": "p.docx", "offset": self.count * 9000})])
        events = []
        model = Model()
        result = run_agent({}, "Analyse the proposal and write findings", model, None, lambda k, d: events.append((k, d)),
                           threading.Event(), extra_tools=[read_tool, notes_tool],
                           extra_call=lambda name, args: {"text": "w" * 9000, "offset": args.get("offset", 0)},
                           read_only=True, conversation=True, context_limit=96 * 1024)
        self.assertEqual(result.state, "completed")
        flushes = [d for k, d in events if k == "memory_flush"]
        self.assertEqual(len(flushes), len([d for k, d in events if k == "context_compacted"]))
        self.assertTrue(flushes)
        kinds = [k for k, _ in events if k in {"memory_flush", "context_compacted"}]
        self.assertEqual(kinds[0], "memory_flush")
        asked = [m for messages in model.seen for m in messages if m.get("role") == "user"
                 and isinstance(m.get("content"), str) and m["content"].startswith("Context will be condensed soon")]
        self.assertTrue(asked)


class FailedThesisReplayTests(unittest.TestCase):
    """The 2.49M-token run: a long proposal paged and re-paged, a statistics result, a final answer."""

    def test_replayed_pattern_keeps_notes_numbers_and_sources_and_ends_with_a_checked_answer(self):
        tools = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
                 for name in ("read_document", "statistics", "save_notes")]
        pages = [0, 24000, 48000, 72000, 96000, 120000, 144000, 168000, 192000, 216000, 240000, 264000,
                 12000, 30000, 60000, 90000, 130000, 170000, 200000, 250000]

        class Model:
            def __init__(self):
                self.step, self.seen = 0, []

            def respond(self, messages, tools):
                if not tools:
                    return ModelResponse('{"decisions": ["Use N = 91 from the workbook"], "next_move": "write findings"}', [])
                self.seen.append(messages)
                last = messages[-1].get("content")
                if isinstance(last, str) and last.startswith("Context will be condensed soon"):
                    return ModelResponse("", [ToolCall(f"n{self.step}-{len(self.seen)}", "save_notes",
                                                       {"notes": "N = 91 (workbook); alpha 0.812; proposal objectives 1.4.2"})])
                self.step += 1
                if self.step == 1:
                    return ModelResponse("", [ToolCall("s", "statistics", {"path": "survey.xlsx", "analysis": "reliability"})])
                if self.step - 2 < len(pages):
                    return ModelResponse("", [ToolCall(f"r{self.step}", "read_document",
                                                       {"path": "attachments/proposal.docx", "offset": pages[self.step - 2]})])
                return ModelResponse("The workbook has N = 91 and Cronbach's alpha was 0.812 for the scale.", [])

        def call(name, args):
            if name == "statistics":
                return {"n": 91, "cronbach_alpha": 0.812}
            if name == "save_notes":
                return {"saved": True}
            start = args["offset"]
            return {"path": args["path"], "offset": start, "next_offset": start + 24000, "text": "p" * 24000}
        events, model = [], Model()
        result = run_agent({}, "Analyse my survey against the objectives in my proposal", model, None,
                           lambda k, d: events.append((k, d)), threading.Event(), extra_tools=tools, extra_call=call,
                           read_only=True, conversation=True, context_limit=320 * 1024)
        self.assertEqual(result.state, "completed", result.error)
        compactions = [d for k, d in events if k == "context_compacted"]
        self.assertTrue(compactions)
        self.assertLessEqual(len(compactions), 3)  # the failed run compacted 33 times
        self.assertEqual(len([d for k, d in events if k == "memory_flush"]), len(compactions))
        final = model.seen[-1]
        saved = checkpoint(final)
        self.assertIn({"action": "read", "target": "attachments/proposal.docx"}, saved["sources"])
        self.assertTrue(any("cronbach_alpha=0.812" in item for item in saved["key_numbers"]))
        self.assertIn("N = 91", memory(final)["working_notes"])
        self.assertFalse([d for k, d in events if k == "answer_check"])  # its numbers came from a tool
        self.assertTrue(any(k == "recovery" and d.get("action") == "record_notes_and_continue" for k, d in events))
        for messages in model.seen:
            assert_protocol(self, messages)


if __name__ == "__main__":
    unittest.main()
