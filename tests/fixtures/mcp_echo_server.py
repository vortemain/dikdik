"""A minimal stdio MCP server for tests: tools `echo` and `add`, plus env reporting."""
import json
import os
import sys

TOOLS = [
    {"name": "echo", "description": "Echo text back", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "add", "description": "Add two numbers", "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}},
    {"name": "whoami", "description": "Report the API key variable", "inputSchema": {"type": "object", "properties": {}}},
]


def reply(message_id, result=None, error=None):
    body = {"jsonrpc": "2.0", "id": message_id, **({"error": error} if error else {"result": result})}
    sys.stdout.write(json.dumps(body) + "\n")
    sys.stdout.flush()


print("starting echo server (log line, not JSON)", flush=True)
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue  # notifications
    method, params = message["method"], message.get("params", {})
    if method == "initialize":
        reply(message["id"], {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "echo", "version": "1.0"}})
    elif method == "tools/list":
        reply(message["id"], {"tools": TOOLS})
    elif method == "tools/call":
        name, args = params["name"], params.get("arguments", {})
        if name == "echo":
            reply(message["id"], {"content": [{"type": "text", "text": args["text"]}]})
        elif name == "add":
            reply(message["id"], {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]})
        elif name == "whoami":
            leaked = [k for k in os.environ if k.startswith("DIKDIK")]
            reply(message["id"], {"content": [{"type": "text", "text": json.dumps({"key": os.environ.get("ECHO_KEY"), "dikdik_vars": leaked})}]})
        else:
            reply(message["id"], {"content": [{"type": "text", "text": "no such tool"}], "isError": True})
    else:
        reply(message["id"], error={"code": -32601, "message": "Method not found"})
