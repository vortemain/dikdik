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
        process = subprocess.Popen(argv, cwd=root, env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
                                   start_new_session=os.name != "nt")
    except OSError as exc:
        return {"status": "failed", "exit_code": None, "output": f"Could not start {shell}: {exc.strerror or exc}",
                "shell": shell, "runs_on": "this computer"}
    output, size, over = [], 0, threading.Event()

    def read():
        nonlocal size
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        for raw in iter(lambda: process.stdout.read1(CHUNK), b""):
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

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    status = None
    while process.poll() is None:
        if cancel.is_set():
            status = "cancelled"
        elif time.monotonic() - started > timeout:
            status = "timed_out"
        elif over.is_set():
            status = "output_limit"
        if status:
            _stop(process)
            break
        time.sleep(0.05)
    reader.join(timeout=5)
    process.stdout.close()
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
        return {"kind": "tests", "status": "not_run", "error": "No test suite found (Python test_*.py, package.json test script, or Cargo.toml)"}
    runs = []
    for runner in runners:
        command = TEST_COMMANDS.get(runner["kind"])
        if command is None or cancel.is_set():
            continue
        result = run(root, command, cancel, timeout=runner.get("timeout", 300), on_chunk=on_chunk)
        runs.append({"kind": runner["kind"], "label": runner["label"], "command": command, **result})
    if len(runs) == 1:
        return runs[0]
    order = ["cancelled", "timed_out", "output_limit", "failed", "passed"]
    worst = min((r["status"] for r in runs), key=lambda s: order.index(s) if s in order else 0) if runs else "cancelled"
    return {"kind": "tests", "status": worst, "runs": runs, "runs_on": "this computer",
            "output": "\n\n".join(f"== {r['label']}: {r['status']} ==\n{r.get('output', '')}" for r in runs)[:OUTPUT_LIMIT]}
