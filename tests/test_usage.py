import json
import threading
import unittest

from dikdik.agent import ModelResponse, run_agent
from dikdik.providers import usage_breakdown


class UsageTests(unittest.TestCase):
    def test_breakdown_keeps_only_bounded_numbers(self):
        openai_style = {"usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
                                  "prompt_tokens_details": {"cached_tokens": 40}, "note": "ignored"}}
        self.assertEqual(usage_breakdown(openai_style),
                         {"input_tokens": 120, "output_tokens": 30, "cache_read_tokens": 40, "total_tokens": 150})
        anthropic_style = {"usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3,
                                     "cache_creation_input_tokens": 7, "cost_usd": 0.0012}}
        self.assertEqual(usage_breakdown(anthropic_style),
                         {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 3, "cache_write_tokens": 7,
                          "total_tokens": 15, "reported_cost": 0.0012})
        for bad in ({}, {"usage": "x"}, {"usage": {"input_tokens": -1, "total_tokens": True, "cost": float("inf")}}):
            self.assertEqual(usage_breakdown(bad), {})

    def test_agent_totals_usage_and_records_it_per_call(self):
        class Model:
            def __init__(self):
                self.replies = [ModelResponse("", [], usage_tokens=15, usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "bogus": "x"}),
                                ModelResponse("Done.", [], usage_tokens=8, usage={"total_tokens": 8, "reported_cost": 0.5})]

            def respond(self, messages, tools):
                return self.replies.pop(0)
        # The first reply has no text and no tools, so the agent asks again; the second ends the turn.
        events = []
        outcome = run_agent({"a.py": "x = 1\n"}, "Look", Model(), None, lambda kind, data: events.append((kind, data)), threading.Event())
        reports = [data["usage"] for kind, data in events if kind == "model" and data.get("phase") == "end"]
        self.assertTrue(reports)
        self.assertNotIn("bogus", json.dumps(reports))
        self.assertEqual(outcome.usage.get("total_tokens"), sum(r.get("total_tokens", 0) for r in reports))


if __name__ == "__main__":
    unittest.main()
