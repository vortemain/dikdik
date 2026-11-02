"""Run commands on this computer, in the project folder, like Claude Code.

Commands use the user's real toolchain (their Python, node_modules, virtual
environments, compilers); there is no container. Safety comes from the permission
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
import tempfile
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .windows_shell import powershell_command

OUTPUT_LIMIT = 256 * 1024     # kept for the model and the Terminal panel
# Resource limits for commands on this computer (0 turns one off). On Windows they are Job Object
# limits on committed memory and live processes for the whole command tree, so they stop runaway
# memory and fork bombs without touching reserved address space. On macOS/Linux only an explicitly
# configured memory_mb applies (RLIMIT_AS per process): address-space limits break runtimes that
# reserve large ranges (JVM, V8), so it cannot be a safe default there.
DEFAULT_LIMITS = {"memory_mb": 8192, "processes": 1024}
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
    def __init__(self, limits: dict | None = None):
        self.pid, self.job, self.limits = None, None, dict(limits or {})
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
            self._limit(ctypes, wintypes)

    def _limit(self, ctypes, wintypes):
        memory, processes = int(self.limits.get("memory_mb") or 0), int(self.limits.get("processes") or 0)
        if not memory and not processes:
            return
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        self._extended = Extended
        info = Extended()
        if memory:
            info.BasicLimitInformation.LimitFlags |= 0x200  # JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory * 1024 * 1024
        if processes:
            info.BasicLimitInformation.LimitFlags |= 0x8  # JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = processes
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        if not self.api.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation
            raise ctypes.WinError(ctypes.get_last_error())

    def peak_memory(self) -> int | None:
        """Peak committed memory of the whole command tree (Windows), in bytes. Windows includes a
        request the job limit refused, so a peak at or above the limit means the limit was hit."""
        if self.job is None or not hasattr(self, "_extended"):
            return None
        import ctypes
        from ctypes import wintypes
        info = self._extended()
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.QueryInformationJobObject.restype = wintypes.BOOL
        if not self.api.QueryInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info), None):
            return None
        return int(info.PeakJobMemoryUsed)

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


def _git_bash() -> str | None:
    git = shutil.which("git")
    if git:  # git.exe sits in Git\cmd, Git\bin or Git\mingw64\bin; bash in Git\bin
        for folder in list(Path(git).parents)[:3]:
            candidate = folder / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def shell_kind() -> str:
    """Which shell commands run in: bash, pwsh (PowerShell 7) or powershell (Windows PowerShell 5.1)."""
    if os.name != "nt" or _git_bash():
        return "bash"
    return "pwsh" if shutil.which("pwsh") else "powershell"


def describe_shell() -> str:
    """One sentence for the model, so it writes commands for the shell it really has."""
    kind = shell_kind()
    if kind == "bash":
        return "commands run in bash" + (" (Git Bash on Windows)" if os.name == "nt" else "")
    return (("commands run in PowerShell 7" if kind == "pwsh" else "commands run in Windows PowerShell 5.1")
            + " on Windows (no Git Bash): prefer PowerShell syntax and $env:NAME for environment variables; "
              "&&, ||, /dev/null, rm -rf, mkdir -p, ls -la, touch, export, which, head and tail are translated")


def shell_argv(command: str, env_names=()) -> tuple[list[str], str]:
    """The argv that runs `command` and the shell's name. On Windows, Git Bash when it is
    installed (the commands agents write are POSIX), else PowerShell with translation."""
    if os.name != "nt":
        return ["/bin/bash" if Path("/bin/bash").exists() else "/bin/sh", "-c", command], "bash"
    bash = _git_bash()
    if bash:
        return [bash, "-c", command], "bash"
    kind = shell_kind()
    program = shutil.which("pwsh") if kind == "pwsh" else "powershell"
    # Bypass applies to this one process only: Windows' default policy blocks npm.ps1, npx.ps1 and the like.
    return [program, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
            powershell_command(command, legacy=kind == "powershell", env_names=env_names)], kind


def child_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for a command: UTF-8 Python I/O unless the user set otherwise, this
    process's environment, the approved secrets, and on Windows a Python when PATH has none."""
    merged = {**UTF8_ENV, **os.environ, **(env or {})}
    if os.name == "nt":
        found = shutil.which("python", path=merged.get("PATH"))
        bundled = Path(sys.executable).with_name("python.exe")
        if (found is None or "windowsapps" in found.lower()) and bundled.is_file():
            merged["PATH"] = str(bundled.parent) + os.pathsep + merged.get("PATH", "")
    return merged


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


def _posix_limits(limits: dict):
    memory = int(limits.get("memory_mb") or 0) if limits.get("explicit") else 0
    if os.name == "nt" or not memory:
        return None
    def apply():
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory * 1024 * 1024, memory * 1024 * 1024))
    return apply


class Redactor:
    """Replaces secret values in command output, including values split across streamed chunks."""

    def __init__(self, values: dict[str, str]):
        self.pairs = sorted(((value, f"[redacted {name}]") for name, value in values.items() if len(value) >= 3),
                            key=lambda pair: -len(pair[0]))
        self.hold = max((len(value) for value, _ in self.pairs), default=1) - 1
        self.buffer = ""

    def clean(self, text: str) -> str:
        for value, mark in self.pairs:
            text = text.replace(value, mark)
        return text

    def feed(self, chunk: str) -> str:
        """Cleaned text that is safe to show now; a short tail waits for the next chunk."""
        text = self.clean(self.buffer + chunk)
        if self.hold <= 0:
            self.buffer = ""
            return text
        self.buffer = text[-self.hold:]
        return text[:-self.hold]

    def flush(self) -> str:
        text, self.buffer = self.clean(self.buffer), ""
        return text


def run(root: Path, command: str, cancel: threading.Event, *, timeout: float = 120,
        on_chunk: Callable[[str], None] | None = None, env: dict[str, str] | None = None,
        output_limit: int = OUTPUT_LIMIT, limits: dict | None = None, keep_running: bool = False) -> dict:
    """Run one command in `root`. Status: passed, failed, timed_out, cancelled or output_limit.

    With ``keep_running`` (checks), a noisy command is not stopped at the output limit: it runs to
    its exit code, keeping the start and the end of its output (failures are usually summarised last).
    """
    if not isinstance(command, str) or not command.strip() or len(command) > 4000:
        raise ValueError("Command must contain 1-4000 characters")
    argv, shell = shell_argv(command, tuple(env or ()))
    flags = _creation_flags()
    started = time.monotonic()
    limits = DEFAULT_LIMITS if limits is None else limits
    try:
        tree = _ProcessTree(limits)
    except OSError:
        return {"status": "failed", "exit_code": None, "output": "Could not create a supervised command process tree.",
                "shell": shell, "runs_on": "this computer"}
    try:
        process = subprocess.Popen(argv, cwd=root, env=child_env(env), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
                                   start_new_session=os.name != "nt", preexec_fn=_posix_limits(limits))
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
    # Approved secrets reach the command as environment variables; their values never reach the output.
    redactor = Redactor(env) if env else None

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
            if redactor is not None:
                text = redactor.feed(text)
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
        if redactor is not None:
            tail = redactor.feed(tail) + redactor.flush()
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
    peak = tree.peak_memory()
    tree.close()
    code = process.returncode
    if status is None:
        status = "passed" if code == 0 else "failed"
    memory = int(limits.get("memory_mb") or 0) * 1024 * 1024
    usage = {"peak_memory_bytes": peak, "limits": {k: limits.get(k) for k in ("memory_mb", "processes")}} if peak is not None else {}
    if peak is not None and memory and peak >= memory * 0.98:
        usage["note"] = f"The command reached its {limits['memory_mb']} MB memory limit (Settings: local_limits)."
    text = "".join(output)
    if tail_text:
        text = text[: output_limit // 2] + f"\n[... {size - output_limit // 2 - len(''.join(tail_text))} characters omitted ...]\n" + "".join(tail_text)[-(output_limit // 2):]
    return {"status": status, "exit_code": code, "output": text, "shell": shell,
            "seconds": round(time.monotonic() - started, 3), "runs_on": "this computer", **usage}


# What `run_tests` runs on this computer for each detected suite.
TEST_COMMANDS = {"unittest": "python -m unittest discover -v", "bun_test": "bun test", "npm_test": "npm test",
                 "cargo_test": "cargo test"}


def run_tests(root: Path, runners: list[dict], cancel: threading.Event, on_chunk=None, limits: dict | None = None) -> dict:
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
        report = None
        if runner["kind"] == "bun_test" and command == TEST_COMMANDS["bun_test"]:
            # Bun prints no names for passing tests off a terminal; its JUnit report has them.
            # The report goes outside the project so it is never seen as a project change.
            handle, name = tempfile.mkstemp(prefix="dikdik-junit-", suffix=".xml")
            os.close(handle)
            report = Path(name)
            command = f'{command} --reporter=junit --reporter-outfile="{report.as_posix()}"'
        try:
            result = run(cwd, command, cancel, timeout=runner.get("timeout", 300), on_chunk=on_chunk, limits=limits, keep_running=True)
            if report is not None:
                from .engineering import junit_results
                try:
                    parsed = junit_results(report.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    parsed = {"passed": set(), "failed": set()}
                if parsed["passed"] or parsed["failed"]:
                    result["tests"] = {"passed": sorted(parsed["passed"]), "failed": sorted(parsed["failed"])}
        finally:
            if report is not None:
                report.unlink(missing_ok=True)
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
        argv, shell_name = shell_argv(command, tuple(env or ()))
        flags = _creation_flags()
        process = subprocess.Popen(argv, cwd=root, env=child_env(env), stdin=subprocess.DEVNULL,
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
