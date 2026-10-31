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


CD_TARGET = re.compile(r'''(?:^|[;&|(]\s*)(?:cd|pushd|Set-Location)\s+(['"]?)([^\s;&|'"]+)\1''', re.I)


def check_command_scope(command: str, root: Path) -> None:
    """Commands run in the project folder; refuse one that moves to an absolute path outside it
    (another project of the user's, or the real project behind a copy). Not a sandbox: relative
    paths and paths inside the project stay allowed."""
    base = Path(root).resolve()
    for match in CD_TARGET.finditer(command.strip()):
        target = match.group(2)
        posix = re.match(r"^/([A-Za-z])(/.*)?$", target)  # Git Bash /c/... spelling
        if posix and os.name == "nt":
            target = f"{posix.group(1)}:{posix.group(2) or '/'}"
        if not (target.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:[\\/]", target)):
            continue
        try:
            resolved = Path(os.path.expanduser(target)).resolve()
        except OSError:
            continue
        if not resolved.exists():
            continue  # cd fails and the command stays in the project (a /workspace habit)
        if resolved != base and base not in resolved.parents:
            raise ValueError(f"Commands run in the project folder ({base.as_posix()}); do not cd to {match.group(2)} outside it. "
                             "Use paths relative to the project root.")


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


# Commands write project files as UTF-8 unless the user's environment says otherwise:
# Python on Windows otherwise writes in the ANSI code page and corrupts source files.
UTF8_ENV = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def command_env(env: dict[str, str] | None = None) -> dict[str, str]:
    return {**UTF8_ENV, **os.environ, **(env or {})}


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


def _creation_flags(new_group: bool = True) -> int:
    """Hide child console windows when Dikdik runs from its GUI launcher (pythonw).

    A new process group is still created on Windows so the Stop button can
    interrupt a running command; pipe handles are unaffected.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    if new_group and os.name == "nt":
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    return flags


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
        output_limit: int = OUTPUT_LIMIT, keep_running: bool = False) -> dict:
    """Run one command in `root`. Status: passed, failed, timed_out, cancelled or output_limit.

    With ``keep_running`` (checks), a noisy command is not stopped at the output limit: it runs to
    its exit code, keeping the start and the end of its output (failures are usually summarised last).
    """
    if not isinstance(command, str) or not command.strip() or len(command) > 4000:
        raise ValueError("Command must contain 1-4000 characters")
    argv, shell = shell_argv(command)
    flags = _creation_flags()
    started = time.monotonic()
    try:
        tree = _ProcessTree()
    except OSError:
        return {"status": "failed", "exit_code": None, "output": "Could not create a supervised command process tree.",
                "shell": shell, "runs_on": "this computer"}
    try:
        process = subprocess.Popen(argv, cwd=root, env=command_env(env), stdin=subprocess.DEVNULL,
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
    tail_text = []  # with keep_running: the newest output past the limit
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
            elif keep_running:
                tail_text.append(text)
                while sum(map(len, tail_text)) > output_limit // 2 and len(tail_text) > 1:
                    tail_text.pop(0)
            size += len(text)
            if size >= output_limit and not keep_running:
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
    text = "".join(output)
    if tail_text:
        text = text[: output_limit // 2] + f"\n[... {size - output_limit // 2 - len(''.join(tail_text))} characters omitted ...]\n" + "".join(tail_text)[-(output_limit // 2):]
    return {"status": status, "exit_code": code, "output": text, "shell": shell,
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
        result = run(cwd, command, cancel, timeout=runner.get("timeout", 300), on_chunk=on_chunk, keep_running=True)
        runs.append({"kind": runner["kind"], "label": runner["label"], "command": command, **result})
    if len(runs) == 1:
        return runs[0]
    order = ["cancelled", "timed_out", "output_limit", "failed", "passed"]
    worst = min((r["status"] for r in runs), key=lambda s: order.index(s) if s in order else 0) if runs else "cancelled"
    return {"kind": "tests" if all(r["kind"] in TEST_COMMANDS for r in runs) else "checks", "status": worst, "runs": runs, "runs_on": "this computer",
            "output": "\n\n".join(f"== {r['label']}: {r['status']} ==\n{r.get('output', '')}" for r in runs)[:OUTPUT_LIMIT]}


class BackgroundShells:
    """Long-running commands (dev servers, watchers) started in the project folder.

    Each shell runs in its own process group; output is buffered for later polling.
    A shell stops when the user stops it, the engine closes, or its process exits."""

    def __init__(self):
        self._shells: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, root: Path, command: str, *, env: dict[str, str] | None = None) -> dict:
        import uuid
        if not isinstance(command, str) or not command.strip() or len(command) > 4000:
            raise ValueError("Command must contain 1-4000 characters")
        shell_id = uuid.uuid4().hex[:12]
        argv, shell_name = shell_argv(command)
        flags = _creation_flags()
        process = subprocess.Popen(argv, cwd=root, env=command_env(env), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
                                   start_new_session=os.name != "nt")
        entry = {"id": shell_id, "command": command, "shell": shell_name, "pid": process.pid,
                  "process": process, "output": [], "size": 0, "status": "running", "exit_code": None,
                  "started": time.time()}
        with self._lock:
            self._shells[shell_id] = entry
        drain = threading.Thread(target=self._drain, args=(shell_id,), daemon=True)
        entry["thread"] = drain
        drain.start()
        return {"id": shell_id, "command": command, "shell": shell_name, "pid": process.pid, "status": "running"}

    def _drain(self, shell_id: str):
        entry = self._shells.get(shell_id)
        if not entry:
            return
        process = entry["process"]
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            for raw in iter(lambda: process.stdout.read1(CHUNK), b""):
                text = decoder.decode(raw)
                with self._lock:
                    if entry["size"] < OUTPUT_LIMIT:
                        kept = text[: OUTPUT_LIMIT - entry["size"]]
                        entry["output"].append(kept)
                        entry["size"] += len(kept)
            tail = decoder.decode(b"", final=True)
            if tail:
                with self._lock:
                    if entry["size"] < OUTPUT_LIMIT:
                        entry["output"].append(tail[: OUTPUT_LIMIT - entry["size"]])
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdout.close()
            except OSError:
                pass
            entry["exit_code"] = process.wait()
            with self._lock:
                entry["status"] = "exited" if entry["process"].poll() is not None else "running"

    def poll(self, shell_id: str) -> dict | None:
        with self._lock:
            entry = self._shells.get(shell_id)
            if not entry:
                return None
            process = entry["process"]
            status = entry["status"]
            if status == "running" and process.poll() is not None:
                status = "exited"
                entry["status"] = status
                entry["exit_code"] = process.returncode
            return {"id": shell_id, "command": entry["command"], "shell": entry["shell"],
                    "status": status, "exit_code": entry.get("exit_code"), "pid": entry["pid"],
                    "output": "".join(entry["output"]), "started": entry["started"]}

    def stop(self, shell_id: str) -> dict | None:
        with self._lock:
            entry = self._shells.get(shell_id)
            if not entry:
                return None
            process = entry["process"]
        if process.poll() is None:
            _stop(process)
        with self._lock:
            entry["status"] = "stopped"
            entry["exit_code"] = process.returncode
            return {"id": shell_id, "command": entry["command"], "status": "stopped",
                    "exit_code": entry["exit_code"], "output": "".join(entry["output"])}

    def list_shells(self) -> list[dict]:
        with self._lock:
            return [{"id": sid, "command": e["command"], "shell": e["shell"], "status": e["status"],
                     "pid": e["pid"], "exit_code": e.get("exit_code"), "started": e["started"]}
                    for sid, e in self._shells.items()]

    def close(self):
        with self._lock:
            shells = list(self._shells.values())
        for entry in shells:
            if entry["process"].poll() is None:
                _stop(entry["process"])
            try:
                entry["process"].wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        # Join the drain threads so process.stdout is closed and process.wait()
        # has returned before the caller cleans up the working directory. On
        # Windows the OS releases the killed process's cwd handle slightly after
        # wait() returns; joining the drain thread narrows that race with
        # directory cleanup (test temp dirs, project snapshots).
        for entry in shells:
            thread = entry.get("thread")
            if thread and thread.is_alive():
                thread.join(timeout=3)


# --- pre-existing failures --------------------------------------------------------------
# A project whose suite already fails must not block every change, nor make the agent
# "repair" tests it never touched. When a check fails, the same checks run once on a copy
# of the project as it was before this turn; failures present there too are pre-existing.

LINKED_DIRS = {"node_modules", ".venv", "venv", "target"}  # shared, not copied: installs and build caches
SKIPPED_DIRS = {".git", ".next", ".nuxt", ".turbo", ".svelte-kit", "dist", "build", "__pycache__", ".pytest_cache", ".mypy_cache"}
COPY_LIMIT = 300 * 1024 * 1024
FAILURE_PATTERNS = [
    re.compile(r"^(?:FAIL|ERROR): (\S+ \([\w.]+\))", re.M),                       # unittest
    re.compile(r"^(?:FAILED|ERROR) (\S+::\S+)", re.M),                            # pytest
    re.compile(r"^\s*(?:×|✕|✗|❯ ×)\s+(.+?)(?:\s+\d+(?:\.\d+)?\s*m?s)?\s*$", re.M),   # vitest / jest
    re.compile(r"^\s*FAIL\s+(\S+\.(?:[cm]?[jt]sx?))", re.M),                       # jest / vitest file summary
    re.compile(r"^\(fail\) (.+?)(?: \[[\d.]+m?s\])?\s*$", re.M),                    # bun test
    re.compile(r"^test (\S+) \.\.\. FAILED\s*$", re.M),                             # cargo test
    re.compile(r"^(\S+?)\(\d+,\d+\): (error TS\d+: .+?)\s*$", re.M),                # tsc; position dropped below
]
ESLINT_ROW = re.compile(r"^\s+\d+:\d+\s+error\s+(.+?)\s{2,}(\S+)\s*$")  # message, rule; warnings never fail a run
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def failure_ids(output: str) -> set[str]:
    """Names of failing tests or errors in runner output. Positions are dropped so an
    edit that shifts lines does not make an old error look new."""
    text = ANSI.sub("", output or "")
    found = set()
    for pattern in FAILURE_PATTERNS:
        for match in pattern.finditer(text):
            found.add(" ".join(group.strip() for group in match.groups() if group))
    # ESLint's stylish format: a file line, then "  12:5  error  message  rule" rows.
    current = None
    for line in text.splitlines():
        if line and not line[0].isspace() and re.search(r"\.(?:[cm]?[jt]sx?|vue|svelte|astro)$", line.strip()):
            current = line.strip().replace("\\", "/")
            continue
        problem = ESLINT_ROW.match(line)
        if problem and current:
            found.add(f"{current} {problem.group(2)} {problem.group(1)}")
    return found


def link_dir(source: Path, target: Path) -> None:
    """Share a heavy folder such as node_modules instead of copying it."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(source), str(target))
    else:
        target.symlink_to(source, target_is_directory=True)


def unlink_dir(target: Path) -> None:
    # Remove the link itself first so cleanup can never descend into the shared folder.
    if target.is_symlink() or (os.name == "nt" and target.is_junction()):
        os.rmdir(target) if os.name == "nt" else target.unlink()


def project_copy(root: Path, target: Path) -> list[Path]:
    """Copy a project for a side run, linking installs and build caches and skipping build
    output. Returns the links, which the caller removes with unlink_dir before deleting."""
    links, size = [], 0

    def ignore(directory, names):
        nonlocal size
        skipped = []
        for name in names:
            path = Path(directory) / name
            if name in SKIPPED_DIRS or name in LINKED_DIRS and path.is_dir():
                skipped.append(name)
                if name in LINKED_DIRS:
                    links.append((path, target / path.relative_to(root)))
            elif path.is_file():
                size += path.stat().st_size
                if size > COPY_LIMIT:
                    raise ValueError("Project too large to copy for a baseline check")
        return skipped

    shutil.copytree(root, target, ignore=ignore, symlinks=True)
    made = []
    for source, link in links:
        link_dir(source, link)
        made.append(link)
    return made


def run_before_change(root: Path, before: dict[str, str | None], runners: list[dict], cancel: threading.Event) -> dict | None:
    """Run ``runners`` on a temporary copy of ``root`` with ``before`` restored (None = the
    file did not exist). The project folder itself is never changed. None if it cannot run."""
    import tempfile
    temp = Path(tempfile.mkdtemp(prefix="dikdik-baseline-"))
    copy, links = temp / "project", []
    try:
        links = project_copy(root, copy)
        for path, text in before.items():
            target = (copy / path).resolve()
            if copy.resolve() not in target.parents:
                continue
            if text is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(text.encode("utf-8"))
        result = run_tests(copy, runners, cancel)
        # Report paths as the project's own, so the same error matches before and after.
        swap = lambda text: text.replace(str(copy), str(root)).replace(copy.as_posix(), Path(root).as_posix()) if isinstance(text, str) else text
        result = {**result, "output": swap(result.get("output"))}
        if isinstance(result.get("runs"), list):
            result["runs"] = [{**run, "output": swap(run.get("output"))} for run in result["runs"]]
        return result
    except (OSError, ValueError, shutil.Error):
        return None
    finally:
        for link in links:
            try:
                unlink_dir(link)
            except OSError:
                pass
        if not any(link.exists() for link in links):
            shutil.rmtree(temp, ignore_errors=True)


def preexisting_failures(after: dict, before: dict | None) -> list[str] | None:
    """The failures in ``after`` when every one of them also failed in ``before``; None when
    something new fails or a failure cannot be identified (then the change stays failed)."""
    if before is None or after.get("status") != "failed":
        return None
    runs = lambda result: result["runs"] if isinstance(result.get("runs"), list) else [result]
    previous = {run.get("label"): run for run in runs(before)}
    found = set()
    for run in runs(after):
        if run.get("status") == "passed":
            continue
        prior = previous.get(run.get("label"))
        if run.get("status") != "failed" or prior is None or prior.get("status") != "failed":
            return None
        now, then = failure_ids(run.get("output", "")), failure_ids(prior.get("output", ""))
        if not now or now - then:
            return None
        found |= now
    return sorted(found) or None
