"""The Task List stays current while the agent works, including on follow-up turns."""

import threading
import unittest

from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.context_memory import PLAN_MARKER
from dikdik.planning import TASK_TRACKER

ORIGINAL = {"app.py": "a = 1\nb = 1\nc = 1\n", "tests/test_app.py": "assert True\n"}
STEPS = [{"title": "Fix a", "status": "in_progress"}, {"title": "Fix b", "status": "todo"}, {"title": "Fix c", "status": "todo"}]


class Replies:
    def __init__(self, *replies):
        self.replies, self.seen, self.tools = list(replies), [], []

    def respond(self, messages, tools):
        self.seen.append(messages)
        self.tools.append([tool["function"]["name"] for tool in tools])
        return self.replies.pop(0)


class Tracker:
    """The planning service's checklist semantics, without a store."""

    def __init__(self, items=None):
        self.items = [dict(item, id=str(i + 1)) for i, item in enumerate(items or [])]

    def __call__(self, name, args):
        if args.get("tasks") is not None:
            self.items = [{"id": str(i + 1), "title": t["title"], "status": t["status"]} for i, t in enumerate(args["tasks"])]
        elif args.get("advance") is True:
            current = next((t for t in self.items if t["status"] == "in_progress"), None)
            following = next((t for t in self.items if t["status"] == "todo"), None)
            if current:
                current["status"] = "done"
            if following:
                following["status"] = "in_progress"
        return {"tasks": [dict(t) for t in self.items], "revision": 1, "reported_only": True}


def edit(i, letter, value=2):
    return ToolCall(f"e{i}", "edit_file", {"path": "app.py", "before": f"{letter} = 1", "after": f"{letter} = {value}"})


def run(model, tracker, objective="Fix a, b and c in app.py", **kwargs):
    events = []
    result = run_agent(dict(ORIGINAL), objective, model, lambda files: {"kind": "unittest", "status": "passed"},
                       lambda k, d: events.append((k, d)), threading.Event(), extra_tools=[TASK_TRACKER], extra_call=tracker,
                       effort=kwargs.pop("effort", "deep"), **kwargs)
    return result, events


def user_text(messages):
    return [m["content"] for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]


class TaskListTests(unittest.TestCase):
    def test_quiet_step_work_gets_one_reminder_to_advance(self):
        tracker = Tracker()
        model = Replies(ModelResponse("", [ToolCall("p", "task_tracker", {"tasks": STEPS})]),
                        ModelResponse("", [edit(1, "a")]), ModelResponse("", [edit(2, "b")]),
                        ModelResponse("", [edit(3, "c")]), ModelResponse("", [edit(4, "a", 3)]),
                        ModelResponse("", [ToolCall("t", "run_tests", {})]),
                        ModelResponse("", [ToolCall("d", "task_tracker", {"tasks": [dict(s, status="done") for s in STEPS]})]),
                        ModelResponse("", [ToolCall("f", "finish", {})]))
        result, events = run(model, tracker)
        self.assertEqual(result.state, "completed", result.error)
        reminders = [d for k, d in events if k == "recovery" and d.get("action") == "update_task_list"]
        self.assertEqual(len(reminders), 1)  # once per quiet stretch, not after every batch
        self.assertTrue(any('"Fix a" as in progress' in text for text in user_text(model.seen[3])))
        self.assertFalse(any("Task List check" in text for text in user_text(model.seen[2])))

    def test_finish_with_open_steps_is_returned_once_for_a_list_update(self):
        tracker = Tracker()
        model = Replies(ModelResponse("", [ToolCall("p", "task_tracker", {"tasks": STEPS}), edit(1, "a")]),
                        ModelResponse("", [ToolCall("t", "run_tests", {})]),
                        ModelResponse("", [ToolCall("f", "finish", {})]),
                        ModelResponse("", [ToolCall("d", "task_tracker", {"advance": True})]),
                        ModelResponse("", [ToolCall("f2", "finish", {})]))
        result, events = run(model, tracker)
        self.assertEqual(result.state, "completed", result.error)
        refused = [d for k, d in events if k == "tool" and d.get("name") == "finish" and d.get("phase") == "end"][0]
        self.assertTrue(refused["failed"])
        self.assertIn("Fix a; Fix b; Fix c", refused["observation"]["error"])
        self.assertEqual([t["status"] for t in tracker.items], ["done", "in_progress", "todo"])

    def test_a_second_finish_completes_whatever_the_list_says(self):
        model = Replies(ModelResponse("", [ToolCall("p", "task_tracker", {"tasks": STEPS}), edit(1, "a")]),
                        ModelResponse("", [ToolCall("t", "run_tests", {})]),
                        ModelResponse("", [ToolCall("f", "finish", {})]),
                        ModelResponse("", [ToolCall("f2", "finish", {})]))
        result, _ = run(model, Tracker())
        self.assertEqual(result.state, "completed", result.error)

    def test_a_follow_up_turn_sees_and_closes_the_open_list(self):
        tracker = Tracker(STEPS)
        model = Replies(ModelResponse("", [edit(1, "b"), edit(2, "c")]),
                        ModelResponse("", [ToolCall("t", "run_tests", {})]),
                        ModelResponse("All fixed.", []),
                        ModelResponse("", [ToolCall("d", "task_tracker", {"tasks": [dict(s, status="done") for s in STEPS]})]),
                        ModelResponse("All fixed.", []))
        result, events = run(model, tracker, objective="ok fix the rest", reported_checklist=tracker.items)
        self.assertEqual(result.state, "completed", result.error)
        first = model.seen[0]
        marker = next(i for i, m in enumerate(first) if PLAN_MARKER in str(m.get("content")))
        self.assertEqual(first[-1]["content"], "ok fix the rest")
        self.assertLess(marker, len(first) - 1)
        self.assertTrue(any("still shows open steps" in text for text in user_text(model.seen[3])))
        self.assertEqual({t["status"] for t in tracker.items}, {"done"})
        self.assertEqual([d["content"] for k, d in events if k == "message"][-1], "All fixed.")

    def test_a_question_about_finished_work_is_not_held_up_by_the_list(self):
        model = Replies(ModelResponse("Three files were fixed.", []))
        result, _ = run(model, Tracker(STEPS), objective="which files did you fix?", effort="simple",
                        reported_checklist=Tracker(STEPS).items, read_only=True, conversation=True)
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(result.model_calls, 1)
        self.assertIn("task_tracker", model.tools[0])  # a short follow-up may still close steps

    def test_simple_runs_without_an_open_list_have_no_tracker(self):
        model = Replies(ModelResponse("Nairobi.", []))
        run(model, Tracker(), objective="What is the capital of Kenya?", effort="simple", read_only=True, conversation=True,
            reported_checklist=[dict(s, status="done") for s in STEPS])
        self.assertNotIn("task_tracker", model.tools[0])


if __name__ == "__main__":
    unittest.main()
