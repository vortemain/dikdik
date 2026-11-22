"""A local stand-in for an OpenAI-compatible model provider, for browser checks.

It is NOT a model: it returns scripted replies so the whole Dikdik flow (HTTP,
SQLite, SSE, the agent loop, approvals) can be exercised without a real key.
Anything verified with it must be reported as "checked against a stand-in",
never as live-provider verified.

Usage:
    python scripts/stub_model.py --port 8899 --key sk-local-test --delay 5 --log stub.jsonl

Then add a model profile in Settings → Models: provider "local",
endpoint http://127.0.0.1:8899/v1, model "stub-model", and an API key holding
the same value as --key.

Behaviour (keyed on words in the latest user message):
- side-question / title / summary / goal-judge system prompts get canned answers
  (the judge says "not yet" once, then "achieved");
- a request without tools (single proposal) returns one exact edit;
- "EDIT": edit_file on calculator.py, then run_tests, then an answer;
- "FIX": fixes calculator.py (a - b to a + b), runs `python -m unittest` with run_command,
  then run_tests, then answers (for local mode: Ask asks before the command);
- "SLOW": slow read_file calls, so Pause/Stop can be clicked mid-run
  (alternating two files, which Dikdik's stuck detector stops after 6 calls);
- "SECRET": asks for a DEMO_TOKEN secret with request_secret (needs command approval in the task);
- "QUESTION": asks a clarifying question with choices, then a sensitive one (REPORT_DB_PASSWORD), and replies
  with the tool results it received, so sealed answers can be checked (works in chat and agent tasks);
- "FILES": creates three files and edits one, a delay apart (the Explorer follows live);
- "PREVIEW": previews a Next.js project twice with `npx next dev` (needs command approval);
- "MARKDOWN": a reply with a table, an alert, a quote and a file path, plus
  provider "reasoning_content" (shown as a Thinking row);
- "CHATSPACE": thinks, keeps a task_tracker plan, reads one file in four parts (slowly) and answers
  with cited sources and cached-token usage, for checking the chat space;
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
# "FILES": new files one at a time, then an edit, so the Explorer can be watched following the agent.
FILES = [("src/app/page.tsx", "export default function Page() {\n  return <main>Tasks</main>;\n}\n"),
         ("src/lib/todos.ts", "export type Todo = { id: string; title: string; done: boolean };\n"),
         ("src/app/globals.css", "main { padding: 2rem; }\n")]


CITED = ("The calculator adds two numbers, and the tests cover the positive cases [1]. Python's own guidance on "
         "[unit testing](https://docs.python.org/3/library/unittest.html) suggests adding edge cases [2].\n\n"
         "1. [calculator.py on the project site](https://example.org/calculator)\n"
         "2. [unittest documentation](https://docs.python.org/3/library/unittest.html)")
PLAN = [("read", "Read calculator.py"), ("check", "Check the tests"), ("answer", "Write the answer")]


def tracker(done_steps):
    return {"tasks": [{"id": key, "title": title, "status": "done" if i < done_steps else "in_progress" if i == done_steps else "todo"}
                      for i, (key, title) in enumerate(PLAN)]}


def reply(content, calls=None, reasoning=None):
    message = {"content": content}
    if calls:
        message["tool_calls"] = calls
    if reasoning:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70, "prompt_tokens_details": {"cached_tokens": 32}}}


def call(name, args, n):
    return {"id": f"call-{name}-{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def text_of(content):
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if part.get("type") == "text")
    return content or ""


def make_handler(expected_key: str, log_path: str | None, delay: float):
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
            scenario = " ".join(text_of(m["content"]) for m in users)
            done = [m["tool_call_id"].split("-")[1] for m in messages if m["role"] == "tool"]
            last = users[-1]["content"] if users else ""
            has_image = isinstance(last, list) and any(p.get("type") == "image_url" for p in last)
            if log_path:
                with open(log_path, "a", encoding="utf-8") as log:
                    log.write(json.dumps({"system": system[:40], "objective": objective[:80], "tools": sorted(tools), "done": done, "image": has_image}) + "\n")
            n = len(messages)
            if system.startswith("Answer the user's side question"):
                data = reply("Side answer: calculator.py defines add(a, b).")
            elif system.startswith("Write a short title"):
                data = reply("Stand-in conversation title")
            elif system.startswith("Summarize this coding conversation"):
                data = reply("Summary: the user asked about calculator.py; add returns a + b.")
            elif system.startswith("You judge whether a coding goal"):
                judged["n"] += 1
                data = reply(json.dumps({"achieved": judged["n"] >= 2, "reason": "Tests pass now." if judged["n"] >= 2 else "Not checked yet.", "next": "Read the test file."}))
            elif not tools:
                data = reply(json.dumps({"edits": [{"path": "calculator.py", "before": "return a + b", "after": "return b + a"}]}))
            elif "SLOW" in objective and done.count("read_file") < 6:
                time.sleep(delay)
                data = reply("Reading again.", [call("read_file", {"path": "calculator.py" if len(done) % 2 == 0 else "test_calculator.py"}, n)])
            elif "FIX" in objective and "edit_file" not in done:
                data = reply("The add function subtracts. Fixing it.", [call("edit_file", {"path": "calculator.py", "before": "return a - b", "after": "return a + b"}, n)])
            elif "FIX" in objective and "run_command" in tools and "run_command" not in done:
                data = reply("Running the tests myself first.", [call("run_command", {"command": "python -m unittest"}, n)])
            elif "FIX" in objective and "run_tests" not in done:
                data = reply("Checking with the project's tests.", [call("run_tests", {}, n)])
            elif "FIX" in objective:
                data = reply("Fixed: add now returns a + b, and the tests pass on this computer.")
            elif "EDIT" in objective and "edit_file" not in done:
                data = reply("I'll make the change.", [call("edit_file", {"path": "calculator.py", "before": "return a + b", "after": "return b + a"}, n)])
            elif "EDIT" in objective and "run_tests" not in done:
                data = reply("Checking it.", [call("run_tests", {}, n)])
            elif "SECRET" in objective and "request_secret" in tools and "request_secret" not in done:
                data = reply("I need a token for this.", [call("request_secret", {"name": "DEMO_TOKEN", "reason": "To call the demo API"}, n)])
            elif "QUESTION" in scenario and "ask_question" in tools and done.count("ask_question") < 1:
                data = reply("", [call("ask_question", {"question": "Which API key should the report script use? Paste it, or pick one.",
                                                        "choices": ["The staging key", "The production key"]}, n)])
            elif "QUESTION" in scenario and "ask_question" in tools and done.count("ask_question") < 2:
                data = reply("", [call("ask_question", {"question": "What is the database password?", "sensitive": True,
                                                        "name": "REPORT_DB_PASSWORD"}, n)])
            elif "QUESTION" in scenario:
                # Echo exactly what came back, so a check can confirm sealed values never reach the model.
                answers = [text_of(m["content"]) for m in messages if m["role"] == "tool"][-2:]
                data = reply("The answers I received:\n\n" + "\n\n".join(f"- `{a[:300]}`" for a in answers))
            elif "FILES" in objective and done.count("create_file") < len(FILES):
                time.sleep(delay)
                path, text = FILES[done.count("create_file")]
                data = reply(f"Writing {path}.", [call("create_file", {"path": path, "content": text}, n)])
            elif "FILES" in objective and "edit_file" not in done:
                time.sleep(delay)
                data = reply("Adding the done state.", [call("edit_file", {"path": "src/app/page.tsx", "before": "  return <main>",
                                                                          "after": "  const done = 0;\n  return <main>"}, n)])
            elif "FILES" in objective:
                data = reply("Wrote the page, the store and the styles.")
            elif "PREVIEW" in objective and "preview_app" in tools and done.count("preview_app") < 2:
                # Twice: the second preview reuses the running dev server.
                data = reply("Previewing the app.", [call("preview_app", {"command": "npx next dev --port {port}", "path": "/"}, n)])
            elif "PREVIEW" in objective:
                data = reply("Previewed the app; it is running in the Browser panel.")
            elif "CHATSPACE" in scenario and "task_tracker" in tools and done.count("task_tracker") < 1:
                time.sleep(delay)
                data = reply("", [call("task_tracker", tracker(0), n)], reasoning="The user wants a review of the calculator. "
                             "I will plan three steps, read the file in parts and then answer with sources.")
            elif "CHATSPACE" in scenario and done.count("read_file") < 4:
                time.sleep(delay)
                data = reply("", [call("read_file", {"path": "calculator.py", "offset": done.count("read_file") * 10}, n)])
            elif "CHATSPACE" in scenario and "task_tracker" in tools and done.count("task_tracker") < 2:
                time.sleep(delay)
                data = reply("", [call("task_tracker", tracker(2), n)], reasoning="I have read the whole file. Now I check the tests and write it up.")
            elif "CHATSPACE" in scenario:
                time.sleep(delay)
                data = reply(CITED, reasoning="Everything is read. The answer should cite the file and the unittest docs.")
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
    parser.add_argument("--delay", type=float, default=1.5, help="seconds before each SLOW reply")
    parser.add_argument("--log", default=None, help="append one JSON line per request (no keys are logged)")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.key, args.log, args.delay))
    print(f"Stand-in model on http://127.0.0.1:{args.port}/v1 (key {args.key[:6]}…)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
