"""A local stand-in for an OpenAI-compatible model provider, for browser checks.

It is NOT a model: it returns scripted replies so the whole Dikdik flow (HTTP,
SQLite, SSE, the agent loop, approvals) can be exercised without a real key.
Anything verified with it must be reported as "checked against a stand-in",
never as live-provider verified.

Usage:
    python scripts/stub_model.py --port 8899 --key sk-local-test --log stub.jsonl

Then add a model profile in Settings → Models: provider "local",
endpoint http://127.0.0.1:8899/v1, model "stub-model", and an API key holding
the same value as --key.

Behaviour (keyed on words in the latest user message):
- side-question / summary / goal-judge system prompts get canned answers
  (the judge says "not yet" once, then "achieved");
- a request without tools (single proposal) returns one exact edit;
- "EDIT": edit_file on calculator.py, then run_tests, then an answer;
- "SLOW": slow read_file calls, so Pause/Stop can be clicked mid-run
  (alternating two files, which Dikdik's stuck detector stops after 6 calls);
- "MARKDOWN": a reply with a table, an alert, a quote and a file path, plus
  provider "reasoning_content" (shown as a Thinking row);
- an attached image: says it sees an image;
- otherwise: "Done with: <message>".
Streaming is ignored on purpose (Dikdik falls back to a plain response).
"""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MARKDOWN = ("Here is what I found in `calculator.py:2`.\n\n| File | Lines |\n|:--|--:|\n| calculator.py | 3 |\n"
            "| test_calculator.py | 7 |\n\n> [!WARNING]\n> The tests do not cover negative numbers.\n\n> A plain quote.")


def reply(content, calls=None, reasoning=None):
    message = {"content": content}
    if calls:
        message["tool_calls"] = calls
    if reasoning:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}}


def call(name, args, n):
    return {"id": f"call-{name}-{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def text_of(content):
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if part.get("type") == "text")
    return content or ""


def make_handler(expected_key: str, log_path: str | None):
    judged = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.headers.get("Authorization") != f"Bearer {expected_key}":
                self.send_response(401); self.end_headers(); return
            messages, tools = body["messages"], {t["function"]["name"] for t in body.get("tools", [])}
            system = text_of(messages[0]["content"])
            users = [m for m in messages if m["role"] == "user"]
            objective = text_of(users[-1]["content"]) if users else ""
            done = [m["tool_call_id"].split("-")[1] for m in messages if m["role"] == "tool"]
            last = users[-1]["content"] if users else ""
            has_image = isinstance(last, list) and any(p.get("type") == "image_url" for p in last)
            if log_path:
                with open(log_path, "a", encoding="utf-8") as log:
                    log.write(json.dumps({"system": system[:40], "objective": objective[:80], "tools": sorted(tools), "done": done, "image": has_image}) + "\n")
            n = len(messages)
            if system.startswith("Answer the user's side question"):
                data = reply("Side answer: calculator.py defines add(a, b).")
            elif system.startswith("Summarize this coding conversation"):
                data = reply("Summary: the user asked about calculator.py; add returns a + b.")
            elif system.startswith("You judge whether a coding goal"):
                judged["n"] += 1
                data = reply(json.dumps({"achieved": judged["n"] >= 2, "reason": "Tests pass now." if judged["n"] >= 2 else "Not checked yet.", "next": "Read the test file."}))
            elif not tools:
                data = reply(json.dumps({"edits": [{"path": "calculator.py", "before": "return a + b", "after": "return b + a"}]}))
            elif "SLOW" in objective and done.count("read_file") < 6:
                time.sleep(1.5)
                data = reply("Reading again.", [call("read_file", {"path": "calculator.py" if len(done) % 2 == 0 else "test_calculator.py"}, n)])
            elif "EDIT" in objective and "edit_file" not in done:
                data = reply("I'll make the change.", [call("edit_file", {"path": "calculator.py", "before": "return a + b", "after": "return b + a"}, n)])
            elif "EDIT" in objective and "run_tests" not in done:
                data = reply("Checking it.", [call("run_tests", {}, n)])
            elif "MARKDOWN" in objective:
                data = reply(MARKDOWN, reasoning="The user wants a summary. I will list the files with their sizes.")
            elif has_image:
                data = reply("I see the attached image.")
            else:
                data = reply(f"Done with: {objective[:60]}")
            raw = json.dumps(data).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--key", default="sk-local-test")
    parser.add_argument("--log", default=None, help="append one JSON line per request (no keys are logged)")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.key, args.log))
    print(f"Stand-in model on http://127.0.0.1:{args.port}/v1 (key {args.key[:6]}…)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
