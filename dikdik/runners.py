"""Project test runners and the agent's sandboxed shell, all inside restricted Docker.

Nothing here runs repository code on the host. Every container has no network, a
read-only root filesystem, no capabilities, an unprivileged user, and bounded
memory, processes, time and output. Images are never pulled automatically.
"""

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import codecs
from pathlib import Path, PurePosixPath
from typing import Callable

from .repository import inventory
from .sandbox import cleanup_container, run_tests as run_python_tests

STARTED = "DIKDIK_CONTAINER_STARTED"
IMAGES = {"python": "python:3.13-slim", "bun": "oven/bun:1", "node": "node:22-slim", "rust": "rust:1-slim"}
OUTPUT_LIMIT = 65536
SHELL_OUTPUT_LIMIT = 32768
MAX_SHELL_CHANGES = 50


def detect_runners(files: dict[str, str]) -> list[dict]:
    """Which test suites this candidate has, from its manifests and file names."""
    runners = []
    names = {PurePosixPath(path).name for path in files}
    if any(PurePosixPath(p).name.startswith("test") and p.endswith(".py") for p in files):
        runners.append({"kind": "unittest", "label": "Python unittest", "image": IMAGES["python"]})
    if "package.json" in files:
        try:
            scripts = json.loads(files["package.json"]).get("scripts", {})
        except (ValueError, AttributeError):
            scripts = {}
        test_script = scripts.get("test", "") if isinstance(scripts, dict) else ""
        bun = bool({"bun.lock", "bun.lockb", "bunfig.toml"} & names) or "bun test" in str(test_script)
        if bun:
            runners.append({"kind": "bun_test", "label": "bun test", "image": IMAGES["bun"], "argv": ["bun", "test"]})
        elif test_script:
            runners.append({"kind": "npm_test", "label": "npm test", "image": IMAGES["node"], "argv": ["npm", "test", "--silent"]})
    if "Cargo.toml" in files:
        runners.append({"kind": "cargo_test", "label": "cargo test (offline)", "image": IMAGES["rust"],
                        "argv": ["cargo", "test", "--offline", "--quiet"], "memory": "1g", "timeout": 300})
    return runners


def container_command(workspace: Path, name: str, image: str, argv: list[str], *, writable: bool = False,
                      memory: str = "512m", tmp: str = "256m") -> list[str]:
    # `sh` prints a marker first, so a non-zero exit can be told apart from Docker failing to start.
    return [
        "docker", "run", "--name", name, "--rm", "--pull=never",
        "--log-driver=none", "--ulimit=nofile=256:256",
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=128",
        f"--memory={memory}", f"--memory-swap={memory}", "--cpus=1",
        "--user=65534:65534", f"--tmpfs=/tmp:rw,exec,nosuid,size={tmp}",
        "--mount", f"type=bind,source={workspace},target=/workspace{'' if writable else ',readonly'}",
        "--workdir=/workspace", "--env=HOME=/tmp", "--env=CARGO_TARGET_DIR=/tmp/target",
        "--env=BUN_INSTALL_CACHE_DIR=/tmp/bun", "--env=npm_config_cache=/tmp/npm", "--env=CI=1",
        image, "sh", "-c", f'echo {STARTED}; exec "$@"', "sh", *argv,
    ]


def bounded(argv: list[str], seconds: float) -> list[str]:
    """coreutils timeout inside the container, so it ends even if this service dies."""
    return ["timeout", "-k", "5", str(int(seconds) + 5), *argv]


def run_container(command: list[str], name: str, cancel: threading.Event, timeout: float,
                  limit: int = OUTPUT_LIMIT, on_chunk: Callable[[str], None] | None = None) -> dict:
    started = time.monotonic()
    output, truncated = bytearray(), threading.Event()
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, shell=False)
    except OSError:
        return {"status": "blocked", "error": "Docker CLI unavailable", "exit_code": None, "output": "", "seconds": 0.0}

    def drain():
        pending = bytearray()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        container_started = False
        while block := proc.stdout.read(4096):
            room = limit - len(output)
            output.extend(block[:max(0, room)])
            if len(block) > room:
                truncated.set()
            if on_chunk is not None:
                pending.extend(block)
                if not container_started and b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    container_started = line.rstrip(b"\r") == STARTED.encode()
                if container_started and pending:
                    text = decoder.decode(bytes(pending), final=False)
                    pending.clear()
                    if text:
                        on_chunk(text)
        if on_chunk is not None and container_started:
            text = decoder.decode(bytes(pending), final=True)
            if text:
                on_chunk(text)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    reason = None
    while proc.poll() is None:
        if cancel.wait(.05):
            reason = "cancelled"
            break
        if time.monotonic() - started >= timeout:
            reason = "timed_out"
            break
    cleanup = cleanup_container(name)  # stopping the client does not stop the container
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)
    reader.join(timeout=5)
    proc.stdout.close()
    text = output.decode("utf-8", errors="replace")
    code = proc.returncode
    if reason is None:
        reason = ("blocked" if STARTED not in text or code in {125, 126, 127} else "passed" if code == 0 else "failed")
    text = text.replace(STARTED + "\n", "", 1).replace(STARTED + "\r\n", "", 1)
    if cleanup and reason == "passed":
        reason = "inconclusive"
    return {"status": reason, "exit_code": code, "output": text, "output_truncated": truncated.is_set(),
            "cleanup_warning": cleanup, "seconds": round(time.monotonic() - started, 3)}


def _name(name: str | None) -> str:
    return name or "dikdik-" + uuid.uuid4().hex


def run_project_tests(snapshot: Path, files: dict[str, str], cancel: threading.Event, name: str | None = None) -> dict:
    """Run every detected suite. A Python-only project keeps the receipt-checked unittest result."""
    runners = detect_runners(files)
    if not runners:
        return {"kind": "tests", "status": "not_run", "error": "No test suite found (Python test_*.py, package.json test script, or Cargo.toml)"}
    runs = []
    for runner in runners:
        if cancel.is_set():
            break
        if runner["kind"] == "unittest":
            result = run_python_tests(snapshot, cancel, name=_name(name))
        else:
            limit = runner.get("timeout", 120)
            command = container_command(snapshot, _name(name), runner["image"], bounded(runner["argv"], limit), memory=runner.get("memory", "512m"),
                                        tmp="1g" if runner["kind"] == "cargo_test" else "256m")
            result = {"kind": runner["kind"], "image": runner["image"], **run_container(command, command[3], cancel, runner.get("timeout", 120))}
        runs.append({**result, "label": runner["label"]})
    if len(runs) == 1:
        return runs[0]
    order = ["cancelled", "blocked", "inconclusive", "timed_out", "output_limit", "failed", "passed"]
    worst = min((run["status"] for run in runs), key=lambda status: order.index(status) if status in order else 0) if runs else "cancelled"
    return {"kind": "tests", "status": worst, "runs": runs,
            "output": "\n\n".join(f"== {run['label']}: {run['status']} ==\n{run.get('output', '')}" for run in runs)[:OUTPUT_LIMIT]}


def run_shell(workdir: Path, files: dict[str, str], command: str, cancel: threading.Event, *,
              image: str = "python", timeout: float = 60, name: str | None = None,
              on_chunk: Callable[[str], None] | None = None,
              output_limit: int = SHELL_OUTPUT_LIMIT) -> dict:
    """Run one shell command on a throwaway copy of the candidate; report changed text files."""
    if image not in IMAGES:
        raise ValueError(f"Image must be one of {', '.join(sorted(IMAGES))}")
    if not command.strip() or len(command) > 4000:
        raise ValueError("Command must contain 1–4000 characters")
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    for path, text in files.items():
        target = workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
    if os.name != "nt":  # the container runs as nobody and must be able to write the copy
        for folder in [workdir, *[p for p in workdir.rglob("*") if p.is_dir()]]:
            folder.chmod(0o777)
        for item in workdir.rglob("*"):
            if item.is_file():
                item.chmod(0o666)
    name = _name(name)
    args = (container_command(workdir, name, IMAGES[image], bounded(["sh", "-c", command], timeout), writable=True),
            name, cancel, timeout + 10, output_limit)
    result = run_container(*args, on_chunk=on_chunk) if on_chunk is not None else run_container(*args)
    changed: dict[str, str] = {}
    deleted: list[str] = []
    try:
        after = inventory(workdir)
        changed = {path: text for path, text in after.items() if files.get(path) != text}
        deleted = sorted(path for path in files if path not in after)
    except ValueError as exc:
        result["sync_error"] = str(exc)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if len(changed) > MAX_SHELL_CHANGES:
        result["sync_error"] = f"Command changed more than {MAX_SHELL_CHANGES} files; none were kept"
        changed = {}
    return {**result, "image": IMAGES[image], "changed": changed, "deleted_ignored": deleted}
