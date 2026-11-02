"""Task-success benchmark: does the agent actually solve engineering tasks?

    DIKDIK_MODEL_URL=... DIKDIK_MODEL=... DIKDIK_API_KEY=... python scripts/benchmark_tasks.py [--tasks repair,validation] [--output results.json]
    python scripts/benchmark_tasks.py --saved-model [PROFILE]   # a model profile saved in the Dikdik app

Each task in benchmarks/tasks/<name>/ has a fixture project (`repo/`), an objective (`task.json`),
**hidden oracle tests** (`oracle/`) and a reference `solution/`; the agent sees none of the last two.
tests/test_benchmark_tasks.py proves every oracle fails on the fixture and passes on the solution. The harness copies the project,
runs one ordinary local-mode agent turn against it (isolated, throwaway data directory; your
~/.dikdik is never touched), then copies the oracle tests into the result and runs them in a fresh
process. Success is the oracle passing, independent of what the agent or its own tests claim.

Results record the benchmark version (a hash of every task file), the environment, the model
endpoint host and name (never the key), and per task: the agent's state, its own verification
claim, the computed release decision, the oracle outcome, model/tool calls, reported tokens and
cost, and wall time. Running it calls the configured model and may cost money.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dikdik import __version__  # noqa: E402
from dikdik.engine import TERMINAL, Engine  # noqa: E402
from dikdik.model_config import ModelResolver  # noqa: E402
from dikdik.secrets_store import SecretsStore  # noqa: E402
from dikdik.store import Store  # noqa: E402

TASKS = ROOT / "benchmarks" / "tasks"


def version(tasks: list[Path]) -> str:
    digest = hashlib.sha256()
    for task in tasks:
        for path in sorted(p for p in task.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
            digest.update(path.relative_to(TASKS).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()[:16]


def oracle(workspace: Path, task: Path, timeout: float = 120) -> dict:
    """Run the hidden tests against a fresh copy of the agent's result."""
    with tempfile.TemporaryDirectory(prefix="dikdik-oracle-") as temp:
        judged = Path(temp) / "judged"
        shutil.copytree(workspace, judged, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        for test in (task / "oracle").glob("*.py"):
            shutil.copy2(test, judged / test.name)
        try:
            run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-v", "-p", "test_oracle*.py"], cwd=judged,
                                 capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"passed": False, "reason": "oracle timed out"}
    tail = (run.stdout + run.stderr)[-3000:]
    return {"passed": run.returncode == 0, "exit_code": run.returncode, "output_tail": tail}


def saved_model(data: Path, folder: Path, name: str | None = None) -> tuple[ModelResolver, str | None]:
    """A resolver over a private copy of a Dikdik data folder: the run uses a model profile saved in the
    app without opening (or migrating) the real database. The key is decrypted only in this process."""
    source = sqlite3.connect(f"file:{(data / 'state.sqlite3').as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(folder / "state.sqlite3")
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    for name_ in ("secrets.bin", "secrets.key"):
        if (data / name_).is_file():
            shutil.copy2(data / name_, folder / name_)
    resolver = ModelResolver(Store(folder / "state.sqlite3"), SecretsStore(folder / "secrets.bin", folder / "secrets.key"))
    if not name or name == "default":
        return resolver, None
    with resolver.store.connect() as db:
        rows = db.execute("SELECT id, name, model FROM profiles").fetchall()
    found = [row["id"] for row in rows if name in (row["id"], row["name"], row["model"])]
    if len(found) != 1:
        raise SystemExit(f"No single saved model profile matches {name!r}")
    with resolver.store.connect() as db:  # only the private copy changes
        db.execute("UPDATE profiles SET is_default = (id = ?)", (found[0],))
    return resolver, None


def watched() -> list[Path]:
    """This checkout and, from a git worktree, the main checkout: an agent must change neither."""
    roots = [ROOT]
    try:
        common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        if common and Path(common).parent.resolve() != ROOT.resolve():
            roots.append(Path(common).parent)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return roots


def outside_snapshot(roots: list[Path]) -> dict[str, tuple[int, int]]:
    skip = {".git", ".claude", "node_modules", "__pycache__", "dist", "build", "target"}
    found = {}
    for root in roots:
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in files:
                path = Path(folder) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return found


def run_task(task: Path, timeout: float | None = None, models: tuple[ModelResolver, str | None] | None = None) -> dict:
    spec = json.loads((task / "task.json").read_text(encoding="utf-8"))
    roots = watched()
    before = outside_snapshot(roots)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"dikdik-bench-{task.name}-") as temp:
        workspace, data = Path(temp) / "workspace", Path(temp) / "data"
        shutil.copytree(task / "repo", workspace)
        engine = Engine(workspace, data)
        try:
            if models:
                engine.models = models[0]
            engine.set_trusted(True)
            # A UI task also approves the browser, so the agent can check its own app with preview_app.
            ident = engine.submit({"objective": spec["objective"], "mode": "agent", "verification": "local",
                                   "approve_external": True, "approve_execution": True, "confirmation": "never",
                                   **({"approve_browser": True} if spec.get("browser") else {}),
                                   **({"model_profile": models[1]} if models and models[1] else {})})
            deadline = time.monotonic() + (timeout or spec.get("timeout_seconds", 900))
            while engine.store.status(ident) not in TERMINAL - {"draft"} and time.monotonic() < deadline:
                time.sleep(0.5)
            record = engine.store.get(ident, include_events=False)
            if record["state"] not in TERMINAL:
                engine.cancel(ident)
            steps = timeline(engine.store.get(ident)["events"])
        finally:
            engine.close()
        result = record.get("result") or {}
        judged = oracle(workspace, task)
    after = outside_snapshot(roots)
    outside = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    agent = result.get("agent", {})
    usage = agent.get("usage", {})
    return {"task": task.name, "category": spec.get("category", ""), "state": record["state"],
            "agent_claimed_tests_verified": bool(result.get("tests_verified")),
            "release_decision": (result.get("release_gate") or {}).get("decision"),
            "oracle_passed": judged["passed"], "oracle": judged,
            # Writing outside the task's workspace (for example into Dikdik itself) is never a success.
            "outside_changes": outside[:50],
            "success": judged["passed"] and record["state"] == "completed" and not outside,
            "model_calls": agent.get("model_calls"), "tool_calls": agent.get("tool_calls"),
            "reported_tokens": usage.get("total_tokens"), "reported_cost": usage.get("reported_cost"),
            "seconds": round(time.monotonic() - started, 1), "error": result.get("error"),
            "tools_used": sorted({step["tool"] for step in steps if step.get("tool")}), "timeline": steps}


def timeline(events: list[dict]) -> list[dict]:
    """Each tool call's outcome and each recovery, so a failed run can be diagnosed after its data is gone."""
    steps = []
    for event in events:
        data = event.get("data") or {}
        if event["kind"] == "agent_tool" and data.get("phase") == "end":
            seen = data.get("observation") if isinstance(data.get("observation"), dict) else {}
            step = {"tool": data.get("name"), "failed": bool(data.get("failed"))}
            for key in ("status", "error", "issues", "next_step"):
                if seen.get(key):
                    step[key] = seen[key][:4] if isinstance(seen[key], list) else str(seen[key])[:300]
            steps.append(step)
        elif event["kind"] in ("agent_recovery", "hook_blocked"):
            steps.append({"recovery": data.get("action") or event["kind"], "reason": str(data.get("reason", ""))[:300]})
    return steps


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", default="", help="comma-separated task names (default: all)")
    parser.add_argument("--output", default=None, help="write the JSON report here")
    parser.add_argument("--timeout", type=float, default=None, help="seconds per task (default: each task's own)")
    parser.add_argument("--saved-model", nargs="?", const="default", default=None, metavar="PROFILE",
                        help="use a model profile saved in the Dikdik app (its id, name or model; default: the default profile)")
    parser.add_argument("--data", default=os.path.expanduser("~/.dikdik"), help="the app's data folder for --saved-model")
    parser.add_argument("--repeat", type=int, default=1, help="independent runs of each task, for a success rate")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if args.saved_model is None and not os.environ.get("DIKDIK_MODEL_URL") and not os.environ.get("NEBIUS_API_KEY") and not os.environ.get("NVIDIA_API_KEY"):
        parser.error("Configure a model with DIKDIK_MODEL_URL, DIKDIK_MODEL and DIKDIK_API_KEY, or use --saved-model (this run calls it)")
    chosen = [t.strip() for t in args.tasks.split(",") if t.strip()]
    tasks = [p for p in sorted(TASKS.iterdir()) if p.is_dir() and (not chosen or p.name in chosen)]
    if not tasks:
        parser.error("No benchmark tasks matched")
    copy = tempfile.TemporaryDirectory(prefix="dikdik-bench-models-") if args.saved_model is not None else None
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results = []
    try:
        models = saved_model(Path(args.data), Path(copy.name), args.saved_model) if copy else None
        described = models[0].describe(models[1]) if models else {}
        endpoint = os.environ.get("DIKDIK_MODEL_URL", "")
        model = ({"host": described.get("host", ""), "model": described.get("model", ""), "profile": described.get("profile", "")}
                 if models else {"host": urlsplit(endpoint).hostname or "", "model": os.environ.get("DIKDIK_MODEL", "")})
        for attempt in range(1, args.repeat + 1):  # rounds, so an interrupted run still covers every task evenly
            for task in tasks:
                results.append({**run_task(task, args.timeout, models), "attempt": attempt})
                text = json.dumps(report(tasks, model, started, results), indent=2)
                if args.output:  # after every task, so a long run's results survive an interruption
                    Path(args.output).write_text(text + "\n", encoding="utf-8")
    finally:
        if copy:
            copy.cleanup()  # the copied key store goes with it
    print(text)
    return 0


def report(tasks: list[Path], model: dict, started: str, results: list[dict]) -> dict:
    per_task = {}
    for result in results:
        entry = per_task.setdefault(result["task"], {"runs": 0, "succeeded": 0, "cost": 0.0})
        entry["runs"] += 1
        entry["succeeded"] += bool(result["success"])
        entry["cost"] = round(entry["cost"] + (result.get("reported_cost") or 0), 4)
    for entry in per_task.values():
        entry["success_rate"] = round(entry["succeeded"] / entry["runs"], 3)
    return {"benchmark": "dikdik-task-success", "benchmark_version": version(tasks), "dikdik": __version__,
            "environment": {"python": platform.python_version(), "platform": platform.system(), "machine": platform.machine()},
            "model": model, "started": started, "results": results,
            "summary": {"runs": len(results), "succeeded": sum(r["success"] for r in results),
                        "success_rate": round(sum(r["success"] for r in results) / len(results), 3),
                        "per_task": per_task,
                        "claimed_but_failed_oracle": sum(r["agent_claimed_tests_verified"] and not r["oracle_passed"] for r in results),
                        "wrote_outside_workspace": sum(bool(r["outside_changes"]) for r in results),
                        "reported_cost": round(sum(r.get("reported_cost") or 0 for r in results), 4),
                        "seconds": round(sum(r["seconds"] for r in results), 1)}}


if __name__ == "__main__":
    sys.exit(main())
