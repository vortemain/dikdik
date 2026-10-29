"""Run commands on this computer, in the project folder, like Claude Code.

Local mode trades Docker's isolation for the user's real toolchain (their Python,
node_modules, virtual environments, compilers). Safety comes from the permission
mode the user picked (commands ask first unless remembered), a time limit, an
output cap and stopping the whole process tree on cancel. Nothing here decides
whether a command may run: the engine asks the user before calling run().
"""

from __future__ import annotations

import os
import codecs
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

OUTPUT_LIMIT = 256 * 1024     # kept for the model and the Terminal panel
CHUNK = 4096


def check_project_search(command: str) -> None:
    """Catch accidental whole-drive discovery commands, not a general shell sandbox."""
    root_find = r'''(?:^|[;&|]\s*)(?:command\s+)?(?:/[^\s;&|]*/)?find(?:\.exe)?\s+(?:-[HLP]\s+)*(['"]?)(?:/|[A-Za-z]:[/\\]|/(?:[A-Za-z]|mnt/[A-Za-z])/?)\1(?=\s|$|[;&|])'''
    if re.search(root_find, command.strip()):
        raise ValueError("Do not search the entire computer for a project file. Use list_files with query set to the filename "
                         "or run find . -name 'filename' inside the project. "
                         "If it is not found, ask the user to open the correct project instead of scanning filesystem roots.")


class _ProcessTree:
    """Keep descendants controllable even when their original shell exits."""
    def __init__(self):
        self.pid, self.job = None, None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            self.api = ctypes.WinDLL("kernel32", use_last_error=True)
            self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self.api.CreateJobObjectW.restype = wintypes.HANDLE
            self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.api.AssignProcessToJobObject.restype = wintypes.BOOL
            self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.api.TerminateJobObject.restype = wintypes.BOOL
            self.api.CloseHandle.argtypes = [wintypes.HANDLE]
            self.api.CloseHandle.restype = wintypes.BOOL
            job = self.api.CreateJobObjectW(None, None)
            if not job:
                raise ctypes.WinError(ctypes.get_last_error())
            self.job = job

    def attach(self, process):
        self.pid = process.pid
        if self.job is not None and not self.api.AssignProcessToJobObject(self.job, int(process._handle)):
            import ctypes
            raise ctypes.WinError(ctypes.get_last_error())

    def stop(self):
        if self.job is not None:
            self.api.TerminateJobObject(self.job, 1)
        elif os.name != "nt" and self.pid is not None:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except OSError:
                pass

    def close(self):
        self.stop()
        if self.job is not None:
            self.api.CloseHandle(self.job)
            self.job = None


def shell_argv(command: str) -> tuple[list[str], str]:
    """The argv that runs `command` and the shell's name. On Windows, Git Bash when it
    is installed (the commands agents write are POSIX), else PowerShell."""
    if os.name != "nt":
        return ["/bin/bash" if Path("/bin/bash").exists() else "/bin/sh", "-c", command], "bash"
    git = shutil.which("git")
    if git:  # git.exe sits in Git\cmd, Git\bin or Git\mingw64\bin; bash in Git\bin
        for folder in list(Path(git).parents)[:3]:
            candidate = folder / "bin" / "bash.exe"
            if candidate.is_file():
                return [str(candidate), "-c", command], "bash"
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command], "powershell"


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False, timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def run(root: Path, command: str, cancel: threading.Event, *, timeout: float = 120,
        on_chunk: Callable[[str], None] | None = None, env: dict[str, str] | None = None,
        output_limit: int = OUTPUT_LIMIT) -> dict:
    """Run one command in `root`. Status: passed, failed, timed_out, cancelled or output_limit."""
    if not isinstance(command, str) or not command.strip() or len(command) > 4000:
        raise ValueError("Command must contain 1-4000 characters")
    argv, shell = shell_argv(command)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    started = time.monotonic()
    try:
        tree = _ProcessTree()
    except OSError:
        return {"status": "failed", "exit_code": None, "output": "Could not create a supervised command process tree.",
                "shell": shell, "runs_on": "this computer"}
    try:
        process = subprocess.Popen(argv, cwd=root, env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
                                   start_new_session=os.name != "nt")
    except OSError as exc:
        tree.close()
        return {"status": "failed", "exit_code": None, "output": f"Could not start {shell}: {exc.strerror or exc}",
                "shell": shell, "runs_on": "this computer"}
    try:
        tree.attach(process)
    except OSError:
        _stop(process)
        tree.close()
        process.stdout.close()
        return {"status": "failed", "exit_code": process.poll(), "output": "Could not supervise the command's process tree safely.",
                "shell": shell, "runs_on": "this computer"}
    output, size, over = [], 0, threading.Event()
    closing_output = threading.Event()

    def read():
        nonlocal size
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        def read_chunk():
            try:
                return process.stdout.read1(CHUNK)
            except (OSError, ValueError):
                if closing_output.is_set():
                    return b""
                raise
        for raw in iter(read_chunk, b""):
            text = decoder.decode(raw)
            if size < output_limit:
                kept = text[: output_limit - size]
                output.append(kept)
                if on_chunk is not None:
                    on_chunk(kept)
            size += len(text)
            if size >= output_limit:
                over.set()
        tail = decoder.decode(b"", final=True)
        if tail and size < output_limit:
            output.append(tail)
            if on_chunk is not None:
                on_chunk(tail)

    def drain():
        try:
            read()
        finally:
            process.stdout.close()  # only the reader closes its pipe; close() on another thread can deadlock

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    status = None
    while process.poll() is None or reader.is_alive():
        if cancel.is_set():
            status = "cancelled"
        elif time.monotonic() - started > timeout:
            status = "timed_out"
        elif over.is_set():
            status = "output_limit"
        if status:
            tree.stop()
            _stop(process)
            break
        time.sleep(0.05)
    reader.join(timeout=5)
    closing_output.set()
    tree.close()
    code = process.returncode
    if status is None:
        status = "passed" if code == 0 else "failed"
    return {"status": status, "exit_code": code, "output": "".join(output), "shell": shell,
            "seconds": round(time.monotonic() - started, 3), "runs_on": "this computer"}


# What `run_tests` runs on this computer for each detected suite.
TEST_COMMANDS = {"unittest": "python -m unittest discover", "bun_test": "bun test", "npm_test": "npm test",
                 "cargo_test": "cargo test"}


def run_tests(root: Path, runners: list[dict], cancel: threading.Event, on_chunk=None) -> dict:
    """Every detected suite, run locally. A suite passes when its command exits 0
    (Python's unittest exits 5 when it found no tests, so an empty suite never passes)."""
    if not runners:
        return {"kind": "tests", "status": "not_run", "reason": "no_automated_checks",
                "error": "No automated tests or declared typecheck, lint or build checks were found"}
    runs = []
    missing = []
    for runner in runners:
        for requirement in runner.get("requires", []):
            tool, cwd = requirement["tool"], (root / requirement.get("cwd", "")).resolve()
            if cwd != root.resolve() and root.resolve() not in cwd.parents:
                return {"kind": "checks", "status": "blocked", "error": "Validation directory must stay inside the workspace"}
            found = shutil.which(tool) is not None
            for directory in [cwd, *cwd.parents]:
                if directory != root.resolve() and root.resolve() not in directory.parents:
                    break
                if any((directory / "node_modules" / ".bin" / (tool + suffix)).is_file() for suffix in ("", ".cmd", ".exe")):
                    found = True
                    break
            if not found:
                missing.append(tool + (f" ({requirement['cwd']})" if requirement.get("cwd") else ""))
    if missing:
        return {"kind": "checks", "status": "not_run", "reason": "missing_check_tool",
                "missing_tools": sorted(set(missing)),
                "note": "Validation could not run because required project tools are unavailable: " + ", ".join(sorted(set(missing)))}
    for runner in runners:
        command = runner.get("command") or TEST_COMMANDS.get(runner["kind"])
        if command is None or cancel.is_set():
            continue
        cwd = (root / runner.get("cwd", "")).resolve()
        if cwd != root.resolve() and root.resolve() not in cwd.parents:
            return {"kind": "tests", "status": "blocked", "error": "Validation directory must stay inside the workspace"}
        result = run(cwd, command, cancel, timeout=runner.get("timeout", 300), on_chunk=on_chunk)
        runs.append({"kind": runner["kind"], "label": runner["label"], "command": command, **result})
    if len(runs) == 1:
        return runs[0]
    order = ["cancelled", "timed_out", "output_limit", "failed", "passed"]
    worst = min((r["status"] for r in runs), key=lambda s: order.index(s) if s in order else 0) if runs else "cancelled"
    return {"kind": "tests" if all(r["kind"] in TEST_COMMANDS for r in runs) else "checks", "status": worst, "runs": runs, "runs_on": "this computer",
            "output": "\n\n".join(f"== {r['label']}: {r['status']} ==\n{r.get('output', '')}" for r in runs)[:OUTPUT_LIMIT]}
