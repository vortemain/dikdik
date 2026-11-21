"""run_python: a Python script on this computer that can call Dikdik's read-only tools.

The script runs like any approved command (local_exec: the user's approval first, a time limit,
the process tree stopped on cancel, resource limits). It imports ``dikdik_tools``, a helper
written next to it, which reaches a loopback bridge for this one run: a random token, an
allow-list of read-only tools and a call cap. Tool calls go through the same code (and folder
boundaries) as the model's own calls. Only what the script prints returns, capped head and tail.
This is not a sandbox: like run_command, the script can do what the user can.
"""

from __future__ import annotations

import ast
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from . import local_exec
from .shell_tools import head_tail, relative_workdir, resolve_workdir

OUTPUT_CAP = 12000
MAX_CALLS = 200
REQUEST_LIMIT = 1024 * 1024
# Read-only tools a script may call, when the conversation itself offers them.
CALLABLE = ("read_document", "statistics", "web_fetch", "web_search", "literature_search")
ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|/(?!/)|~[\\/])\S")


def run_python_tool(available) -> dict:
    names = [name for name in CALLABLE if name in set(available)]
    helpers = (" In the script, `from dikdik_tools import " + ", ".join(names) + "` calls those Dikdik tools with "
               "the same arguments as the tools themselves and returns their results as dicts (call(name, **arguments) "
               "for any of them); a refused call raises dikdik_tools.ToolError.") if names else ""
    return {"type": "function", "function": {
        "name": "run_python",
        "description": "Run a Python 3 script on this computer, in the working folder, with the user's approval. Use it "
                       "for loops, bulk filtering, parsing and real computation instead of working numbers out in your "
                       "head. Only what the script prints comes back (long output keeps its start and end), so print "
                       "concise results." + helpers + " The standard library always works; other packages only if "
                       "installed. Use paths relative to the working folder; workdir runs it in a subfolder. Files it "
                       "writes stay in the folder.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "maxLength": 100000},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
            "workdir": {"type": "string"}},
            "required": ["code"], "additionalProperties": False}}}


HELPER = '''"""Dikdik's read-only tools for this script. Results are untrusted data."""
import json
import os
import socket

__all__ = ["ToolError", "call", "read_document", "statistics", "web_fetch", "web_search", "literature_search"]


class ToolError(RuntimeError):
    pass


def call(tool, **arguments):
    host, port, token = os.environ.get("DIKDIK_TOOLS", "::").rsplit(":", 2)
    if not port:
        raise ToolError("Dikdik tools are not available in this script")
    with socket.create_connection((host, int(port)), timeout=600) as conn:
        conn.sendall(json.dumps({"token": token, "tool": tool, "arguments": arguments}).encode("utf-8") + b"\\n")
        reply = conn.makefile("rb").readline()
    data = json.loads(reply.decode("utf-8") or "{}")
    if data.get("refused"):
        raise ToolError(data.get("error", "refused"))
    return data.get("result")


def read_document(path, **arguments):
    return call("read_document", path=path, **arguments)


def statistics(path, analysis, **arguments):
    return call("statistics", path=path, analysis=analysis, **arguments)


def web_fetch(url):
    return call("web_fetch", url=url)


def web_search(query, **arguments):
    return call("web_search", query=query, **arguments)


def literature_search(query, **arguments):
    return call("literature_search", query=query, **arguments)
'''


def check_code(code, root: Path) -> None:
    """Refuse code that does not parse, or names an existing absolute path outside the folder."""
    if not isinstance(code, str) or not code.strip() or len(code) > 100000:
        raise ValueError("code must contain 1-100000 characters")
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        raise ValueError(f"The script does not parse: {error.msg} (line {error.lineno}). Nothing ran.") from None
    base = Path(root).resolve()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value.strip()
        if len(text) > 500 or "\n" in text or not ABSOLUTE.match(text):
            continue
        try:
            target = Path(os.path.expanduser(text)).resolve()
        except (OSError, ValueError):
            continue
        if target.exists() and target != base and base not in target.parents:
            raise ValueError(f"Python runs in the working folder ({base.as_posix()}); {text} is outside it. "
                             "Use paths relative to the working folder. Nothing ran.")


def interpreter(root: Path) -> str:
    """The project's virtual environment, else the user's Python on PATH, else Dikdik's own."""
    for venv in (".venv", "venv"):
        for candidate in (Path(root) / venv / "Scripts" / "python.exe", Path(root) / venv / "bin" / "python"):
            if candidate.is_file():
                return str(candidate)
    search = local_exec.child_env().get("PATH")
    for name in ("python3", "python"):
        found = shutil.which(name, path=search)
        if found and "windowsapps" not in found.lower():
            return found
    own = Path(sys.executable)
    console = own.with_name("python.exe")
    return str(console if own.name.lower() == "pythonw.exe" and console.is_file() else own)


class ToolBridge:
    """A loopback endpoint for one script run; refuses unknown tokens, tools outside the allow-list
    and calls past the cap. Calls are handled one at a time, in the order they arrive."""

    def __init__(self, call_tool: Callable[[str, dict], dict] | None, allowed, max_calls: int = MAX_CALLS):
        self.call_tool, self.allowed, self.max_calls = call_tool, frozenset(allowed or ()), max_calls
        self.token = secrets.token_hex(24)
        self.calls = 0
        self.refused = 0
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self._server.settimeout(0.2)
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self._server.getsockname()[1]}:{self.token}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._closing.set()
        self._thread.join(timeout=5)
        self._server.close()

    def _serve(self):
        while not self._closing.is_set():
            try:
                conn, _ = self._server.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                try:
                    conn.settimeout(10)
                    reply = self._answer(self._request(conn))
                    conn.settimeout(None)
                    conn.sendall(json.dumps(reply, default=str).encode("utf-8") + b"\n")
                except (OSError, ValueError):
                    continue

    @staticmethod
    def _request(conn) -> dict:
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > REQUEST_LIMIT:
                raise ValueError("request too large")
        value = json.loads(data.decode("utf-8") or "{}")
        return value if isinstance(value, dict) else {}

    def _answer(self, request: dict) -> dict:
        if not hmac.compare_digest(str(request.get("token", "")), self.token):
            return {"refused": True, "error": "Unknown caller"}
        tool, arguments = request.get("tool"), request.get("arguments")
        if not isinstance(tool, str) or tool not in self.allowed or self.call_tool is None:
            self.refused += 1
            return {"refused": True, "error": f"{tool} is not available to scripts here; available: {', '.join(sorted(self.allowed)) or 'none'}"}
        if not isinstance(arguments, dict):
            return {"refused": True, "error": "arguments must be keyword arguments"}
        if self.calls >= self.max_calls:
            return {"refused": True, "error": f"A script may make at most {self.max_calls} tool calls"}
        self.calls += 1
        try:
            return {"result": self.call_tool(tool, arguments)}
        except PermissionError as error:
            return {"refused": True, "error": str(error) or "Not permitted"}
        except Exception as error:
            return {"result": {"error": str(error)[:1500] or error.__class__.__name__}}


def run(root: Path, code: str, cancel: threading.Event, *, timeout: float = 120, workdir=None,
        call_tool: Callable[[str, dict], dict] | None = None, allowed=(), on_chunk=None,
        env: dict[str, str] | None = None, limits: dict | None = None) -> dict:
    """Run ``code`` in ``root`` (or its ``workdir``); ``call_tool(name, arguments)`` serves the allowed tools."""
    check_code(code, root)
    folder_path = resolve_workdir(root, workdir)
    scratch = Path(tempfile.mkdtemp(prefix="dikdik-python-"))
    try:
        (scratch / "dikdik_tools.py").write_text(HELPER, encoding="utf-8", newline="\n")
        script = scratch / "script.py"
        script.write_text(code, encoding="utf-8", newline="\n")
        with ToolBridge(call_tool, set(allowed) & set(CALLABLE)) as bridge:
            result = local_exec.run(folder_path, "python script.py", cancel, timeout=timeout, on_chunk=on_chunk,
                                    env={**(env or {}), "DIKDIK_TOOLS": bridge.address}, limits=limits,
                                    argv=[interpreter(root), "-X", "utf8", str(script)])
        result["tool_calls"] = bridge.calls
        if bridge.refused:
            result["refused_tool_calls"] = bridge.refused
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    result["output"] = head_tail(str(result.get("output", "")).replace(str(script), "script.py"), OUTPUT_CAP)
    result["workdir"] = relative_workdir(root, folder_path)
    return result
