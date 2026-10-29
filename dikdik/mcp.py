"""Model Context Protocol servers: settings, a stdlib client, and the agent's tool bridge.

Servers are added by the user in Settings and are off until enabled. HTTP servers use
the Streamable HTTP transport (HTTPS, or plain HTTP on this machine only; no
redirects) with an API key or an OAuth sign-in (see mcp_oauth). Stdio servers are
commands the user typed or approved: they run on this machine with a minimal
environment plus the API keys the user assigned, never inherited secrets.
"""

import atexit
import json
import os
import queue
import re
import shlex
import shutil
import signal
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

# Global registry of all living stdio MCP clients, so an unexpected process exit
# (atexit, SIGTERM, SIGINT) kills their child processes instead of orphaning them.
_stdio_clients: list = []
_cleanup_registered = False


def _cleanup_stdio_clients(*_args):
    """Kill all stdio MCP child processes on process exit (atexit + signal)."""
    for client in list(_stdio_clients):
        try:
            client.close()
        except Exception:
            pass
    _stdio_clients.clear()


def _register_cleanup():
    global _cleanup_registered
    if _cleanup_registered:
        return
    _cleanup_registered = True
    atexit.register(_cleanup_stdio_clients)
    # SIGTERM (e.g. docker stop, systemd) should not orphan MCP children.
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_cleanup_stdio_clients(), os._exit(143)))
    except (OSError, ValueError):
        pass  # not main thread or not supported


_register_cleanup()
MAX_TOOLS = 64
RESULT_LIMIT = 16384
SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, transport TEXT NOT NULL,
    url TEXT, command TEXT, secret_ref TEXT, env_var TEXT,
    enabled INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
);
"""
MIGRATIONS = (("env_refs", "TEXT"), ("auth", "TEXT"), ("catalog_id", "TEXT"))
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_ENV = 10
OAUTH_PREFIX = "mcpauth."  # sealed OAuth tokens, one per server; never listed under API keys
MANAGED = ("conn.", OAUTH_PREFIX)  # keys other settings own; a server cannot borrow them
EDITABLE = {"name", "transport", "url", "command", "secret_ref", "env_var", "env", "auth", "enabled"}


def oauth_ref(server_id: str) -> str:
    return OAUTH_PREFIX + server_id


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
            have = {r["name"] for r in db.execute("PRAGMA table_info(mcp_servers)")}
            for column, kind in MIGRATIONS:
                if column not in have:
                    db.execute(f"ALTER TABLE mcp_servers ADD COLUMN {column} {kind}")

    @staticmethod
    def _row(row) -> dict:
        return {"id": row["id"], "name": row["name"], "transport": row["transport"], "url": row["url"] or "",
                "command": row["command"] or "", "secret_ref": row["secret_ref"] or "", "env_var": row["env_var"] or "",
                "env": json.loads(row["env_refs"]) if row["env_refs"] else {}, "auth": row["auth"] or "",
                "catalog_id": row["catalog_id"] or "", "enabled": bool(row["enabled"])}

    def list(self) -> list[dict]:
        with self.store.connect() as db:
            return [self._row(r) for r in db.execute("SELECT * FROM mcp_servers ORDER BY created")]

    def get(self, server_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
        if row is None:
            raise KeyError(server_id)
        return self._row(row)

    @staticmethod
    def _clean(body: dict) -> dict:
        """Validate one server definition; returns the stored fields."""
        name, transport = body.get("name"), body.get("transport")
        if not isinstance(name, str) or not NAME.match(name):
            raise McpError("Name must be 1–32 letters, digits, '_' or '-', starting with a letter or digit")
        if transport not in {"http", "stdio"}:
            raise McpError("Transport must be http or stdio")
        url = command = None
        if transport == "http":
            url = body.get("url")
            if not isinstance(url, str) or not url.strip() or len(url) > 400:
                raise McpError("Enter the server URL")
            _check(url)
        else:
            command = body.get("command")
            if not isinstance(command, str) or not command.strip() or len(command) > 1000:
                raise McpError("Enter the command that starts the server")
            argv(command)  # validates quoting
        secret_ref, env_var = body.get("secret_ref") or None, body.get("env_var") or None
        if secret_ref is not None and (not isinstance(secret_ref, str) or not REF.match(secret_ref) or secret_ref.startswith(MANAGED)):
            raise McpError("API key name must be a stored key name from API keys")
        if transport == "stdio" and secret_ref and (not isinstance(env_var, str) or not ENV_NAME.match(env_var)):
            raise McpError("Name the environment variable that receives the key, e.g. GITHUB_TOKEN")
        env = body.get("env") or {}
        if not isinstance(env, dict) or len(env) > MAX_ENV:
            raise McpError(f"Environment keys must be at most {MAX_ENV} VARIABLE: key-name pairs")
        if env and transport != "stdio":
            raise McpError("Only command servers take environment variables")
        for var, ref in env.items():
            if not ENV_NAME.match(var):
                raise McpError(f"“{var[:40]}” is not an environment variable name (A–Z, 0–9, _)")
            if not isinstance(ref, str) or not REF.match(ref) or ref.startswith(MANAGED):
                raise McpError(f"{var}: name a stored key from API keys")
        if transport == "stdio" and env_var and secret_ref and env_var in env:
            raise McpError(f"{env_var} is set twice")
        auth = body.get("auth") or ""
        if auth not in {"", "oauth"}:
            raise McpError("Sign-in must be oauth or none")
        if auth and transport != "http":
            raise McpError("Only remote servers can use OAuth sign-in")
        if auth and secret_ref:
            raise McpError("Use either an API key or OAuth sign-in, not both")
        catalog_id = body.get("catalog_id") or None
        if catalog_id is not None and (not isinstance(catalog_id, str) or not NAME.match(catalog_id)):
            raise McpError("Unknown library entry")
        return {"name": name, "transport": transport, "url": url, "command": command, "secret_ref": secret_ref,
                "env_var": env_var if transport == "stdio" and secret_ref else None,
                "env_refs": json.dumps(env, sort_keys=True) if env else None, "auth": auth or None, "catalog_id": catalog_id}

    def create(self, body) -> dict:
        if not isinstance(body, dict):
            raise McpError("Body must be a JSON object")
        fields = self._clean(body)
        with self.store.connect() as db:
            if db.execute("SELECT COUNT(*) FROM mcp_servers").fetchone()[0] >= MAX_SERVERS:
                raise McpError(f"At most {MAX_SERVERS} MCP servers")
            if db.execute("SELECT 1 FROM mcp_servers WHERE name=?", (fields["name"],)).fetchone():
                raise McpError("A server with this name already exists")
            server_id = uuid.uuid4().hex
            db.execute("INSERT INTO mcp_servers(id,name,transport,url,command,secret_ref,env_var,env_refs,auth,catalog_id,enabled,created) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (server_id, *[fields[k] for k in ("name", "transport", "url", "command", "secret_ref", "env_var", "env_refs", "auth", "catalog_id")],
                        int(body.get("enabled") is True), time.time()))
        return {"id": server_id}

    def update(self, server_id: str, body) -> dict:
        """Sparse edit: fields left out keep their value; null or "" clears an optional one."""
        if not isinstance(body, dict) or not body:
            raise McpError("Send the fields to change")
        unknown = set(body) - EDITABLE
        if unknown:
            raise McpError(f"Unknown field: {sorted(unknown)[0][:40]}")
        current = self.get(server_id)
        merged = {**current, **{k: v for k, v in body.items() if k != "enabled"}}
        if "transport" in body and body["transport"] != current["transport"]:
            # Switching type drops the other type's fields unless they were sent.
            for key in ("url",) if body["transport"] == "stdio" else ("command", "env_var", "env"):
                if key not in body:
                    merged[key] = {} if key == "env" else ""
            if body["transport"] == "stdio" and "auth" not in body:
                merged["auth"] = ""
        fields = self._clean(merged)
        enabled = body.get("enabled", current["enabled"])
        if not isinstance(enabled, bool):
            raise McpError("Enabled must be true or false")
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM mcp_servers WHERE name=? AND id<>?", (fields["name"], server_id)).fetchone():
                raise McpError("A server with this name already exists")
            db.execute("UPDATE mcp_servers SET name=?,transport=?,url=?,command=?,secret_ref=?,env_var=?,env_refs=?,auth=?,enabled=? WHERE id=?",
                       (*[fields[k] for k in ("name", "transport", "url", "command", "secret_ref", "env_var", "env_refs", "auth")], int(enabled), server_id))
        return self.get(server_id)

    def rename_ref(self, old: str, new: str) -> None:
        """Keep env keys pointing at a renamed API key."""
        with self.store.connect() as db:
            for row in db.execute("SELECT id, env_refs FROM mcp_servers WHERE env_refs IS NOT NULL").fetchall():
                env = json.loads(row["env_refs"])
                if old in env.values():
                    db.execute("UPDATE mcp_servers SET env_refs=? WHERE id=?",
                               (json.dumps({k: new if v == old else v for k, v in env.items()}, sort_keys=True), row["id"]))

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
        _stdio_clients.append(self)
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
            self.proc.wait(timeout=3)
        self.proc.stdout.close()
        # Remove from the global registry so the exit handler doesn't retry a dead process.
        try:
            _stdio_clients.remove(self)
        except ValueError:
            pass


def _key(reveal, ref: str) -> str:
    try:
        return reveal(ref).decode("utf-8")
    except KeyError:
        raise McpError(f"The API key “{ref}” is not stored; add it under API keys") from None


def connect(server: dict, reveal, save=None) -> _Client:
    """Open one server. ``reveal(ref) -> bytes`` decrypts its keys; ``save(ref, bytes)``
    stores a refreshed OAuth token."""
    if server["transport"] == "http":
        if server.get("auth") == "oauth":
            from .mcp_oauth import access_token
            return HttpClient(server["url"], access_token(server, reveal, save))
        return HttpClient(server["url"], _key(reveal, server["secret_ref"]) if server.get("secret_ref") else None)
    env = {var: _key(reveal, ref) for var, ref in (server.get("env") or {}).items()}
    if server.get("secret_ref") and server.get("env_var"):
        env[server["env_var"]] = _key(reveal, server["secret_ref"])
    return StdioClient(server["command"], env)


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

    def __init__(self, servers: list[dict], reveal, save=None):
        self.clients, self.routes, self.tools, self.errors = [], {}, [], []
        for server in servers:
            try:
                client = connect(server, reveal, save)
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


def test_server(server: dict, reveal, save=None, tool_call=None) -> dict:
    """Connect once and list the server's tools, for the Settings Test button.
    ``tool_call`` ({name, arguments}) also runs one tool and reports its text."""
    if tool_call is not None and (not isinstance(tool_call, dict) or not isinstance(tool_call.get("name"), str)
                                  or not isinstance(tool_call.get("arguments", {}), dict)):
        raise McpError("tool_call must be {name, arguments}")
    client = connect(server, reveal, save)
    try:
        info = client.initialize()
        tools = client.tools()
        result = None
        if tool_call is not None:
            if tool_call["name"] not in {t["name"] for t in tools}:
                result = {"is_error": True, "content": f"The server does not offer a tool named {tool_call['name'][:64]}"}
            else:
                try:
                    reply = client.call(tool_call["name"], tool_call.get("arguments", {}))
                    result = {"is_error": bool(reply.get("isError")), "content": _text(reply)}
                except McpError as exc:
                    result = {"is_error": True, "content": str(exc)}
    finally:
        client.close()
    server_info = info.get("serverInfo") if isinstance(info.get("serverInfo"), dict) else {}
    found = {"server": str(server_info.get("name", ""))[:100], "version": str(server_info.get("version", ""))[:40],
             "tools": [{"name": t["name"], "description": str(t.get("description", ""))[:300]} for t in tools]}
    return {**found, "tool_result": result} if tool_call is not None else found
