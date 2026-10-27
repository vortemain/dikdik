"""A stand-in ACP agent for tests: speaks JSON-RPC over stdio like a real one.

Modes (first argument): `work` (default) edits files through the protocol and
directly, `hang` waits for session/cancel, `crash` exits during the prompt.
"""

import json
import os
import sys
from pathlib import Path

mode = sys.argv[1] if len(sys.argv) > 1 else "work"
next_id = 1000
pending = []  # messages that arrived while waiting for a specific reply
cwd = None


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def update(session, body):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "update": body}})


def read():
    if pending:
        return pending.pop(0)
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)


def ask(method, params):
    """Send a request to the client and wait for its reply."""
    global next_id
    next_id += 1
    send({"jsonrpc": "2.0", "id": next_id, "method": method, "params": params})
    while True:
        line = sys.stdin.readline()
        if not line:
            sys.exit(0)
        message = json.loads(line)
        if message.get("id") == next_id and "method" not in message:
            return message
        pending.append(message)


print("fake agent banner, not JSON", flush=True)  # real agents sometimes print one
while True:
    message = read()
    method, params, ident = message.get("method"), message.get("params") or {}, message.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []}})
    elif method == "session/new":
        cwd = Path(params["cwd"])
        send({"jsonrpc": "2.0", "id": ident, "result": {"sessionId": "s1"}})
    elif method == "session/prompt" and mode == "crash":
        sys.stderr.write("boom: the fake agent crashed\n")
        sys.stderr.flush()
        sys.exit(3)
    elif method == "session/prompt" and mode == "hang":
        update("s1", {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "working..."}})
        while True:
            later = read()
            if later.get("method") == "session/cancel":
                send({"jsonrpc": "2.0", "id": ident, "result": {"stopReason": "cancelled"}})
                break
    elif method == "session/prompt":
        update("s1", {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "Reading app.py first."}})
        update("s1", {"sessionUpdate": "plan", "entries": [{"content": "Change value to 2", "status": "pending", "priority": "high"}]})
        text = ask("fs/read_text_file", {"sessionId": "s1", "path": str(cwd / "app.py")})["result"]["content"]
        update("s1", {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Edit app.py", "kind": "edit",
                      "status": "pending", "rawInput": {"path": "app.py"}})
        choice = ask("session/request_permission", {"sessionId": "s1", "toolCall": {"toolCallId": "t1", "title": "Edit app.py"},
                                                    "options": [{"optionId": "always", "name": "Always", "kind": "allow_always"},
                                                                {"optionId": "once", "name": "Allow", "kind": "allow_once"},
                                                                {"optionId": "no", "name": "Reject", "kind": "reject_once"}]})
        picked = choice["result"]["outcome"].get("optionId")
        ask("fs/write_text_file", {"sessionId": "s1", "path": str(cwd / "app.py"), "content": text.replace("1", "2")})
        (cwd / "extra.py").write_text("added = True\n", encoding="utf-8")  # an edit made straight on disk
        outside = ask("fs/read_text_file", {"sessionId": "s1", "path": str(cwd.parent.parent / "outside.txt")})
        update("s1", {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
                      "content": [{"type": "content", "content": {"type": "text", "text": "edited"}}]})
        leaked = "yes" if "DIKDIK_SERVER_ONLY" in os.environ else "no"
        update("s1", {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text":
                      f"Done. permission={picked}; outside read refused={'yes' if 'error' in outside else 'no'}; "}})
        update("s1", {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text":
                      f"server env leaked={leaked}; key={os.environ.get('FAKE_AGENT_KEY', 'missing')}"}})
        send({"jsonrpc": "2.0", "id": ident, "result": {"stopReason": "end_turn"}})
    elif ident is not None:
        send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "not supported"}})
