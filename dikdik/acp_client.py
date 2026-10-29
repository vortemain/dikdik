"""Client side of the Agent Client Protocol (ACP): drive an external coding agent.

ACP is JSON-RPC 2.0, one message per line, over the agent's stdin/stdout. Dikdik
is the client: it sends `initialize`, `session/new` and `session/prompt`, receives
`session/update` notifications (message chunks, thoughts, tool calls, plans) and
answers the agent's own requests for files and permission.

The agent is a program on the host, outside Docker, so it never sees the real
workspace. It runs in a throwaway copy with a minimal environment: file requests
are confined to that copy, and the engine turns whatever the copy holds at the end
into an ordinary candidate diff that the user reviews and applies.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

PROTOCOL_VERSION = 1
MAX_LINE = 8_000_000      # one JSON-RPC message
MAX_FILE = 2_000_000      # one file read or written through fs/*
STDERR_KEEP = 4000        # tail of the agent's stderr kept for a plain error message
# What a CLI needs to start and find its own config and login; nothing else from the
# server's environment (other keys, tokens) is passed on.
HOST_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "HOME", "USERPROFILE",
            "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "TEMP", "TMP", "TMPDIR",
            "LANG", "LC_ALL", "TERM", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "NODE_OPTIONS")


class AcpError(ValueError):
    pass


def host_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: os.environ[key] for key in HOST_ENV if key in os.environ}
    env["NO_COLOR"] = "1"
    env.update(extra or {})
    return env


class AcpSession:
    """One agent process and its JSON-RPC traffic. Not reusable after close()."""

    def __init__(self, command: list[str], cwd: Path, env: dict[str, str], on_update, on_permission=None):
        self.command, self.cwd, self.env = command, Path(cwd).resolve(), env
        self.on_update, self.on_permission = on_update, on_permission
        self.process: subprocess.Popen | None = None
        self.next_id = 0
        self.waiting: dict[int, queue.Queue] = {}
        self.write_lock = threading.Lock()
        self.stderr_tail = ""
        self.closed = threading.Event()

    # --- process -----------------------------------------------------------------
    def start(self) -> None:
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0
        try:
            self.process = subprocess.Popen(
                self.command, cwd=self.cwd, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=flags, start_new_session=sys.platform != "win32")
        except OSError as exc:
            raise AcpError(f"Could not start {Path(self.command[0]).name}: {exc.strerror or exc}") from None
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def close(self) -> None:
        """Stop the agent and everything it started, then release its pipes."""
        self.closed.set()
        process = self.process
        if process is None:
            return
        try:
            self._stop(process)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            process.stdin.close()  # a well-behaved agent exits when its input ends
        except OSError:
            pass
        try:
            process.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    # --- JSON-RPC ----------------------------------------------------------------
    def _send(self, message: dict) -> None:
        line = json.dumps(message, separators=(",", ":")) + "\n"
        with self.write_lock:
            try:
                self.process.stdin.write(line.encode("utf-8"))
                self.process.stdin.flush()
            except (OSError, ValueError):
                raise AcpError(self._exit_message()) from None

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict, timeout: float, cancel=None):
        """Send a request and wait for its answer. When `cancel` is set the agent is
        told to stop (`session/cancel`) and given a moment to answer before AcpError."""
        self.next_id += 1
        ident, box = self.next_id, queue.Queue()
        self.waiting[ident] = box
        self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        deadline, cancelled_at = time.monotonic() + timeout, None
        try:
            while True:
                try:
                    reply = box.get(timeout=0.2)
                    break
                except queue.Empty:
                    pass
                if cancel is not None and cancel.is_set() and cancelled_at is None and method == "session/prompt":
                    cancelled_at = time.monotonic()
                    try:
                        self.notify("session/cancel", {"sessionId": params.get("sessionId")})
                    except AcpError:
                        raise AcpError("Cancelled") from None
                if cancelled_at is not None and time.monotonic() - cancelled_at > 10:
                    raise AcpError("Cancelled")
                if self.process.poll() is not None and box.empty():
                    raise AcpError(self._exit_message())
                if time.monotonic() > deadline:
                    raise AcpError(f"The agent did not answer {method} within {int(timeout)} seconds")
        finally:
            self.waiting.pop(ident, None)
        if "error" in reply:
            error = reply["error"] if isinstance(reply["error"], dict) else {}
            raise AcpError(f"The agent refused {method}: {str(error.get('message') or 'unknown error')[:300]}")
        return reply.get("result")

    def _exit_message(self) -> str:
        code = self.process.poll() if self.process else None
        tail = self.stderr_tail.strip().splitlines()[-1:] if self.stderr_tail.strip() else []
        detail = f": {tail[0][:300]}" if tail else ""
        return f"The agent stopped (exit code {code}){detail}" if code is not None else f"The agent closed its output{detail}"

    def _read_stderr(self) -> None:
        try:
            for raw in iter(lambda: self.process.stderr.read1(4096), b""):
                self.stderr_tail = (self.stderr_tail + raw.decode("utf-8", "replace"))[-STDERR_KEEP:]
        except (OSError, ValueError):
            return  # close() released the pipe

    def _read_stdout(self) -> None:
        stream = self.process.stdout
        while not self.closed.is_set():
            try:
                raw = stream.readline(MAX_LINE + 1)
            except (OSError, ValueError):
                return  # close() released the pipe
            if not raw:
                return
            if len(raw) > MAX_LINE:
                continue  # an oversized message is dropped rather than buffered without bound
            try:
                message = json.loads(raw)
            except ValueError:
                continue  # agents sometimes print a banner; only JSON-RPC lines count
            if not isinstance(message, dict):
                continue
            if "method" not in message and "id" in message:
                box = self.waiting.get(message["id"])
                if box is not None:
                    box.put(message)
            elif "method" in message and "id" in message:
                threading.Thread(target=self._answer, args=(message,), daemon=True).start()
            elif message.get("method") == "session/update":
                update = (message.get("params") or {}).get("update")
                if isinstance(update, dict):
                    try:
                        self.on_update(update)
                    except Exception:
                        pass  # a display problem must never break the protocol loop

    # --- requests from the agent ------------------------------------------------
    def _answer(self, message: dict) -> None:
        method, params = message["method"], message.get("params") or {}
        try:
            if method == "fs/read_text_file":
                result = {"content": self._read_file(params)}
            elif method == "fs/write_text_file":
                self._write_file(params)
                result = None
            elif method == "session/request_permission":
                result = {"outcome": self._permission(params)}
            else:
                self._send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": f"{method} is not offered"}})
                return
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        except AcpError:
            return  # the agent is gone
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            try:
                self._send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": str(exc)[:300]}})
            except AcpError:
                pass

    def inside(self, path) -> Path:
        """ACP paths are absolute; only paths inside the throwaway copy are served."""
        if not isinstance(path, str) or not path:
            raise ValueError("path must be an absolute path inside the session folder")
        target = Path(path) if Path(path).is_absolute() else self.cwd / path
        target = target.resolve()
        if target != self.cwd and self.cwd not in target.parents:
            raise ValueError("Only files inside the session folder are available")
        return target

    def _read_file(self, params: dict) -> str:
        target = self.inside(params.get("path"))
        if target.stat().st_size > MAX_FILE:
            raise ValueError("File is larger than 2 MB")
        text = target.read_bytes().decode("utf-8")
        line, limit = params.get("line"), params.get("limit")
        if isinstance(line, int) or isinstance(limit, int):
            lines = text.splitlines(keepends=True)
            start = max((line or 1) - 1, 0)
            text = "".join(lines[start:start + limit] if isinstance(limit, int) and limit >= 0 else lines[start:])
        return text

    def _write_file(self, params: dict) -> None:
        target, content = self.inside(params.get("path")), params.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE:
            raise ValueError("content must be text under 2 MB")
        if target.relative_to(self.cwd).parts[:1] == (".git",):
            raise ValueError("The session folder's .git is not writable")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))

    def _permission(self, params: dict) -> dict:
        """The agent works on a throwaway copy the user approved for this task, so a
        one-time allow is chosen; never an "always", which the agent could remember."""
        options = [o for o in params.get("options") or [] if isinstance(o, dict) and isinstance(o.get("optionId"), str)]
        chosen = next((o for o in options if o.get("kind") == "allow_once"), None)
        if chosen is None:
            chosen = next((o for o in options if o.get("kind") == "reject_once"), None)
        if self.on_permission is not None:
            try:
                self.on_permission(params.get("toolCall") or {}, chosen)
            except Exception:
                pass
        if chosen is None:
            return {"outcome": "cancelled"}
        return {"outcome": "selected", "optionId": chosen["optionId"]}
