"""Fixed Docker test runner. No arbitrary host commands or automatic pulls."""

import subprocess
import json
import secrets
import threading
import time
import uuid
from pathlib import Path


IMAGE = "python:3.13-slim"
STARTED = "DIKDIK_SANDBOX_STARTED"
RESULT_PREFIX = "DIKDIK_TEST_RESULT:"


def _test_program(workspace: str, nonce: str) -> str:
    return (
        "import sys,unittest,json; sys.path.insert(0," + repr(workspace) + "); "
        "suite=unittest.defaultTestLoader.discover(" + repr(workspace) + "); "
        "n=suite.countTestCases(); print('DIKDIK discovered',n,'tests',flush=True); "
        "result=unittest.TextTestRunner(verbosity=2).run(suite); "
        "counts={'discovered':n,'ran':result.testsRun,'failures':len(result.failures),"
        "'errors':len(result.errors),'skipped':len(result.skipped),"
        "'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses)}; "
        "print(" + repr(RESULT_PREFIX + nonce + ":") + "+json.dumps(counts),flush=True); "
        "sys.exit(0 if n and result.wasSuccessful() and not result.skipped and not result.expectedFailures and result.testsRun==n else 1)"
    )


def _test_receipt(text: str, nonce: str) -> dict | None:
    prefix = RESULT_PREFIX + nonce + ":"
    lines = [line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        return None
    try:
        counts = json.loads(lines[0])
    except ValueError:
        return None
    fields = {"discovered", "ran", "failures", "errors", "skipped", "expected_failures", "unexpected_successes"}
    if not isinstance(counts, dict) or set(counts) != fields or any(type(v) is not int or v < 0 for v in counts.values()):
        return None
    return counts


def docker_command(snapshot: Path, name: str, nonce: str = "contract-test") -> list[str]:
    test_program = _test_program("/workspace", nonce)
    # A supervisor inside the container also bounds execution if the service dies.
    supervisor = _supervisor_program(test_program)
    return [
        "docker", "run", "--name", name, "--rm", "--pull=never",
        "--log-driver=none", "--ulimit=nofile=128:128",
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=64",
        "--memory=256m", "--memory-swap=256m", "--cpus=1",
        "--user=65534:65534", "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
        "--mount", f"type=bind,source={snapshot},target=/workspace,readonly",
        "--workdir=/workspace", "--env=PYTHONDONTWRITEBYTECODE=1",
        IMAGE, "python", "-I", "-c",
        supervisor,
    ]


def _supervisor_program(test_program: str) -> str:
    return "import subprocess,sys; print(" + repr(STARTED) + ",flush=True); sys.exit(subprocess.call([sys.executable,'-I','-c'," + repr(test_program) + "],timeout=55))"


def cleanup_container(name: str) -> str | None:
    if not name.startswith("dikdik-") or len(name) != 39 or any(c not in "0123456789abcdef" for c in name[7:]):
        raise ValueError("Invalid container identity")
    try:
        cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, shell=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if cleanup.returncode and b"No such container" not in cleanup.stderr:
            return "Container cleanup not confirmed; inspect Docker manually"
    except (OSError, subprocess.TimeoutExpired):
        return "Container cleanup not confirmed; inspect Docker manually"
    return None


def run_tests(snapshot: Path, cancel: threading.Event, timeout: float = 60, name: str | None = None) -> dict:
    name = name or "dikdik-" + uuid.uuid4().hex
    nonce = secrets.token_hex(16)
    started = time.monotonic()
    output = bytearray()
    truncated = threading.Event()
    try:
        proc = subprocess.Popen(docker_command(snapshot, name, nonce), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, shell=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return {"kind": "unittest", "status": "blocked", "error": "Docker CLI unavailable", "exit_code": None}

    def drain():
        while block := proc.stdout.read(4096):
            available = 65536 - len(output)
            output.extend(block[:max(0, available)])
            if len(block) > available:
                truncated.set()

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
        if truncated.is_set():
            reason = "output_limit"
            break
    # Always remove by name: stopping a Docker client does not stop its container.
    cleanup_error = cleanup_container(name)
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)
    reader.join(timeout=5)
    proc.stdout.close()
    text = output.decode("utf-8", errors="replace")
    code = proc.returncode
    if truncated.is_set() and reason is None:
        reason = "output_limit"
    # Docker CLI/daemon errors may return 1 on Windows, not just 125.
    # Only the fixed container supervisor emits this startup marker.
    counts = _test_receipt(text, nonce)
    tests_passed = counts is not None and counts["discovered"] == counts["ran"] > 0 and all(counts[key] == 0 for key in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    status = reason or ("blocked" if STARTED not in text else "passed" if code == 0 and tests_passed else "blocked" if code in {125, 126, 127} else "failed")
    if cleanup_error and status == "passed":
        status = "inconclusive"
    return {"kind": "unittest", "status": status, "exit_code": code, "counts": counts, "receipt_present": counts is not None, "output": text, "output_truncated": truncated.is_set(), "cleanup_warning": cleanup_error, "seconds": round(time.monotonic() - started, 3), "image": IMAGE}
