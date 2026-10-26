"""Model Context Protocol servers: settings, a stdlib client, and the agent's tool bridge.

Servers are added by the user in Settings and are off until enabled. HTTP servers use
the Streamable HTTP transport (HTTPS, or plain HTTP on this machine only; no
redirects). Stdio servers are commands the user typed: they run on this machine with
a minimal environment plus the one API key the user assigned, never inherited secrets.
"""

import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

from .model_config import check_url

PROTOCOL = "2025-06-18"
CLIENT = {"name": "dikdik", "version": "0.1.0"}
TIMEOUT = 30
MAX_SERVERS = 20
MAX_TOOLS = 64
RESULT_LIMIT = 16384
SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, transport TEXT NOT NULL,
    url TEXT, command TEXT, secret_ref TEXT, env_var TEXT,
    enabled INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
);
"""
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


class McpError(ValueError):
    pass


def _check(url: str) -> None:
    try:
        check_url(url)
    except ValueError as exc:
        raise McpError(str(exc).replace("Model endpoints", "Server URLs").replace("Model endpoint", "The server URL")) from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise McpError("MCP server redirects are not followed")


# --- settings ---------------------------------------------------------------
class McpStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute(SCHEMA)

    @staticmethod
    def _row(row) -> dict:
        return {"id": row["id"], "name": row["name"], "transport": row["transport"], "url": row["url"] or "",
                "command": row["command"] or "", "secret_ref": row["secret_ref"] or "", "env_var": row["env_var"] or "",
                "enabled": bool(row["enabled"])}

    def list(self) -> list[dict]:
        with self.store.connect() as db:
            return [self._row(r) for r in db.execute("SELECT * FROM mcp_servers ORDER BY created")]

    def get(self, server_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
        if row is None:
            raise KeyError(server_id)
        return self._row(row)

    def create(self, body) -> dict:
        if not isinstance(body, dict):
            raise McpError("Body must be a JSON object")
        name, transport = body.get("name"), body.get("transport")
        if not isinstance(name, str) or not NAME.match(name):
            raise McpError("Name must be 1–32 letters, digits, '_' or '-', starting with a letter or digit")
        if transport not in {"http", "stdio"}:
            raise McpError("Transport must be http or stdio")
        url = command = None
        if transport == "http":
            url = body.get("url")
            if not isinstance(url, str) or len(url) > 400:
                raise McpError("Enter the server URL")
            _check(url)
        else:
            command = body.get("command")
            if not isinstance(command, str) or not command.strip() or len(command) > 1000:
                raise McpError("Enter the command that starts the server")
            argv(command)  # validates quoting
        secret_ref, env_var = body.get("secret_ref") or None, body.get("env_var") or None
        if secret_ref is not None and (not isinstance(secret_ref, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", secret_ref)):
            raise McpError("API key name must be a stored key name from API keys")
        if transport == "stdio" and secret_ref and (not isinstance(env_var, str) or not ENV_NAME.match(env_var)):
            raise McpError("Name the environment variable that receives the key, e.g. GITHUB_TOKEN")
        with self.store.connect() as db:
            if db.execute("SELECT COUNT(*) FROM mcp_servers").fetchone()[0] >= MAX_SERVERS:
                raise McpError(f"At most {MAX_SERVERS} MCP servers")
            if db.execute("SELECT 1 FROM mcp_servers WHERE name=?", (name,)).fetchone():
                raise McpError("A server with this name already exists")
            server_id = uuid.uuid4().hex
            db.execute("INSERT INTO mcp_servers(id,name,transport,url,command,secret_ref,env_var,enabled,created) VALUES(?,?,?,?,?,?,?,0,?)",
                       (server_id, name, transport, url, command, secret_ref, env_var if transport == "stdio" else None, time.time()))
        return {"id": server_id}

    def set_enabled(self, server_id: str, enabled) -> dict:
        if not isinstance(enabled, bool):
            raise McpError("Enabled must be true or false")
        with self.store.connect() as db:
            if db.execute("UPDATE mcp_servers SET enabled=? WHERE id=?", (int(enabled), server_id)).rowcount == 0:
                raise KeyError(server_id)
        return {"id": server_id, "enabled": enabled}

    def delete(self, server_id: str) -> dict:
        with self.store.connect() as db:
            if db.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,)).rowcount == 0:
                raise KeyError(server_id)
        return {"deleted": server_id}


def argv(command: str) -> list[str]:
    windows = os.name == "nt"
    try:
        # POSIX rules would eat the backslashes in Windows paths.
        parts = shlex.split(command, posix=not windows)
    except ValueError:
        raise McpError("The command has unbalanced quotes") from None
    if windows:
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    if not parts:
        raise McpError("Enter the command that starts the server")
    # `npx` and friends are .cmd files on Windows; start them by their full path.
    found = shutil.which(parts[0])
    return [found or parts[0], *parts[1:]]


# --- transports ---------------------------------------------------------------
class _Client:
    def __init__(self):
        self.next_id = 0

    def request(self, method: str, params: dict | None = None, timeout: float = TIMEOUT) -> dict:
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method, **({"params": params} if params is not None else {})}
        reply = self._exchange(message, timeout)
        if not isinstance(reply, dict):
            raise McpError("The server sent an invalid reply")
        if "error" in reply:
            error = reply["error"] if isinstance(reply["error"], dict) else {}
            raise McpError(f"Server error: {str(error.get('message', 'unknown'))[:300]}")
        result = reply.get("result")
        if not isinstance(result, dict):
            raise McpError("The server reply has no result")
        return result

    def notify(self, method: str) -> None:
        self._send_notification({"jsonrpc": "2.0", "method": method})

    def initialize(self) -> dict:
        info = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT}, timeout=20)
        self.notify("notifications/initialized")
        return info

    def tools(self) -> list[dict]:
        found, cursor = [], None
        for _ in range(10):
            page = self.request("tools/list", {"cursor": cursor} if cursor else {})
            found += [t for t in page.get("tools", []) if isinstance(t, dict) and isinstance(t.get("name"), str)]
            cursor = page.get("nextCursor")
            if not cursor or len(found) >= MAX_TOOLS:
                break
        return found[:MAX_TOOLS]

    def call(self, tool: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": tool, "arguments": arguments}, timeout=60)

    def close(self) -> None:
        pass


class HttpClient(_Client):
    def __init__(self, url: str, key: str | None = None):
        super().__init__()
        _check(url)
        self.url, self.key, self.session = url, key, None
        self.opener = urllib.request.build_opener(_NoRedirect)

    def _post(self, message: dict, timeout: float) -> tuple[str, bytes]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if message.get("method") != "initialize":
            headers["MCP-Protocol-Version"] = PROTOCOL
        request = urllib.request.Request(self.url, data=json.dumps(message).encode(), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=timeout) as response:
                self.session = response.headers.get("Mcp-Session-Id") or self.session
                return response.headers.get("Content-Type", ""), response.read(2_000_001)
        except urllib.error.HTTPError as exc:
            raise McpError(f"The server answered HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise McpError("Could not reach the MCP server") from None

    def _exchange(self, message: dict, timeout: float) -> dict:
        kind, body = self._post(message, timeout)
        if len(body) > 2_000_000:
            raise McpError("The server reply is too large")
        text = body.decode("utf-8", "replace")
        if "text/event-stream" in kind:
            for block in text.replace("\r\n", "\n").split("\n\n"):
                data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
                if not data:
                    continue
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("id") == message["id"]:
                    return event
            raise McpError("The server's event stream ended without a reply")
        try:
            return json.loads(text)
        except ValueError:
            raise McpError("The server did not return JSON") from None

    def _send_notification(self, message: dict) -> None:
        self._post(message, 10)


def _minimal_env(extra: dict[str, str]) -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG")
    return {**{k: os.environ[k] for k in keep if k in os.environ}, **extra}


class StdioClient(_Client):
    def __init__(self, command: str, env: dict[str, str] | None = None):
        super().__init__()
        try:
            self.proc = subprocess.Popen(argv(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         env=_minimal_env(env or {}), shell=False)
        except OSError:
            raise McpError("The server command could not be started") from None
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _write(self, message: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(message).encode() + b"\n")
            self.proc.stdin.flush()
        except OSError:
            raise McpError("The server process stopped") from None

    def _exchange(self, message: dict, timeout: float) -> dict:
        self._write(message)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError("The MCP server did not answer in time")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                raise McpError("The MCP server did not answer in time") from None
            if line is None:
                raise McpError("The server process stopped")
            try:
                reply = json.loads(line)
            except ValueError:
                continue  # servers sometimes log to stdout; skip non-JSON lines
            if isinstance(reply, dict) and reply.get("id") == message["id"]:
                return reply

    def _send_notification(self, message: dict) -> None:
        self._write(message)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def connect(server: dict, reveal) -> _Client:
    """Open one server. ``reveal(ref) -> bytes`` decrypts its API key."""
    key = reveal(server["secret_ref"]).decode("utf-8") if server.get("secret_ref") else None
    if server["transport"] == "http":
        return HttpClient(server["url"], key)
    return StdioClient(server["command"], {server["env_var"]: key} if key and server.get("env_var") else {})


# --- the agent's view -----------------------------------------------------------
def tool_name(server: str, tool: str) -> str:
    return ("mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", server) + "__" + re.sub(r"[^A-Za-z0-9_-]", "_", tool))[:64]


def _text(result: dict) -> str:
    parts = []
    for item in result.get("content", []) if isinstance(result.get("content"), list) else []:
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
        elif isinstance(item, dict):
            parts.append(f"[{item.get('type', 'content')} omitted]")
    if not parts and "structuredContent" in result:
        parts.append(json.dumps(result["structuredContent"])[:RESULT_LIMIT])
    text = "\n".join(parts)
    return text if len(text) <= RESULT_LIMIT else text[:RESULT_LIMIT] + "\n…(truncated)"


class McpSession:
    """Connections to every enabled server for one task; closed when the task ends."""

    def __init__(self, servers: list[dict], reveal):
        self.clients, self.routes, self.tools, self.errors = [], {}, [], []
        for server in servers:
            try:
                client = connect(server, reveal)
                self.clients.append(client)
                client.initialize()
                for tool in client.tools():
                    name = tool_name(server["name"], tool["name"])
                    if name in self.routes or len(self.tools) >= MAX_TOOLS:
                        continue
                    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object", "properties": {}}
                    self.routes[name] = (client, tool["name"])
                    self.tools.append({"type": "function", "function": {
                        "name": name, "description": f"[MCP server {server['name']}] {str(tool.get('description', ''))[:900]}",
                        "parameters": {"type": "object", "properties": schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {},
                                       "required": [r for r in schema.get("required", []) if isinstance(r, str)] if isinstance(schema.get("required"), list) else []}}})
            except (McpError, KeyError, OSError, UnicodeDecodeError) as exc:
                self.errors.append({"server": server["name"], "error": str(exc) if isinstance(exc, McpError) else "Could not connect"})

    def call(self, name: str, arguments: dict) -> dict:
        client, tool = self.routes[name]
        try:
            result = client.call(tool, arguments)
        except McpError as exc:
            return {"error": str(exc)}
        return {"is_error": bool(result.get("isError")), "content": _text(result)}

    def close(self) -> None:
        for client in self.clients:
            client.close()


def test_server(server: dict, reveal) -> dict:
    """Connect once and list the server's tools, for the Settings Test button."""
    client = connect(server, reveal)
    try:
        info = client.initialize()
        tools = client.tools()
    finally:
        client.close()
    server_info = info.get("serverInfo") if isinstance(info.get("serverInfo"), dict) else {}
    return {"server": str(server_info.get("name", ""))[:100], "version": str(server_info.get("version", ""))[:40],
            "tools": [{"name": t["name"], "description": str(t.get("description", ""))[:300]} for t in tools]}
