"""Measure the agent: real tasks on throwaway project copies, harness overhead, and run history.

    python -m dikdik.bench run benchmarks/tasks.json [--profile NAME] [--only a,b] [--repeat N] [--out report.json]
    python -m dikdik.bench compare before.json after.json
    python -m dikdik.bench overhead [--files 350] [--steps 30]
    python -m dikdik.bench history [--limit 20]

``run`` exports each task's project from git (so uncommitted work is never used or touched),
runs one ordinary local-mode agent task on the copy with a throwaway data folder, then
scores it: the task's own checks, file invariants (UTF-8, no mixed line endings) and cost.
The model comes from DIKDIK_MODEL_URL/DIKDIK_MODEL/DIKDIK_API_KEY or, failing that, a model
profile read from your Dikdik data folder; the key stays in memory. ``overhead`` needs no model:
a scripted model isolates Dikdik's own per-step cost. ``history`` reads ~/.dikdik read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from .local_exec import link_dir as link, unlink_dir as unlink

DEFAULT_DATA = Path.home() / ".dikdik"
SHELL_READ = re.compile(r"\s*(?:cd\s+\S+\s*&&\s*)?(?:cat|head|tail|sed\s+-n|grep|rg|ls|find|wc|type|Get-Content)\b")  # reading through the shell
DEFAULT_ANSWER = "I can't add more detail right now; use your best judgment from the code and the request."
REQUEST = {"mode": "agent", "verification": "local", "approve_external": True,
           "approve_execution": True, "confirmation": "never"}


# --- metrics ---------------------------------------------------------------------------

def metrics(state: str, result: dict | None, events: list[dict]) -> dict:
    """Time, calls, failures and tokens of one task, from its event history. Counted from
    events rather than the final result so resumed runs report their whole cost."""
    result = result or {}
    usage: dict[str, float] = {}
    model_seconds = tool_seconds = 0.0
    model_started, tools_started = None, {}
    model_calls = tool_calls = failed_tools = 0
    by_tool, errors, reads, names = Counter(), Counter(), Counter(), {}
    batch_sizes, shell_reads, first_edit = [], 0, None
    begin = events[0]["time"] if events else 0.0
    for event in events:
        kind, data, when = event["kind"], event["data"], event["time"]
        if kind == "agent_model":
            phase = data.get("phase")
            if phase == "start":
                model_started = when
                model_calls += 1
            elif phase in {"end", "error", "retry"} and model_started is not None:
                model_seconds += when - model_started
                model_started = None
            if phase == "end":
                batch_sizes.append(data.get("requested_tools", 0))
                for key, value in (data.get("usage") or {}).items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage[key] = usage.get(key, 0) + value
        elif kind == "agent_tool":
            if data.get("phase") == "start":
                name, arguments = data.get("name", "?"), data.get("arguments") or {}
                tools_started[data.get("id")] = when
                names[data.get("id")] = name
                tool_calls += 1
                by_tool[name] += 1
                if name == "read_file":
                    reads[(arguments.get("path"), arguments.get("start_line", 1))] += 1
                if name == "run_command" and SHELL_READ.match(str(arguments.get("command", ""))):
                    shell_reads += 1
                if name in {"edit_file", "create_file"} and first_edit is None:
                    first_edit = when - begin
            elif data.get("phase") == "end":
                started = tools_started.pop(data.get("id"), None)
                if started is not None:
                    tool_seconds += when - started
                if data.get("failed"):
                    failed_tools += 1
                    observation = data.get("observation") or {}
                    reason = str(observation.get("error") or observation.get("status") or "failed")
                    errors[f"{names.get(data.get('id'), '?')}: {reason[:80]}"] += 1
    times = [event["time"] for event in events]
    seconds = (max(times) - min(times)) if times else 0.0
    count = lambda kind: sum(1 for event in events if event["kind"] == kind)
    return {"state": state, "seconds": round(seconds, 1), "model_seconds": round(model_seconds, 1),
            "tool_seconds": round(tool_seconds, 1),
            "harness_seconds": round(max(0.0, seconds - model_seconds - tool_seconds), 1),
            "model_calls": model_calls, "tool_calls": tool_calls, "failed_tool_calls": failed_tools,
            "recoveries": count("agent_recovery"), "compactions": count("agent_context_compacted"),
            "input_tokens": int(usage.get("input_tokens", 0)), "output_tokens": int(usage.get("output_tokens", 0)),
            "cache_read_tokens": int(usage.get("cache_read_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
            "cost": round(usage["reported_cost"], 6) if "reported_cost" in usage else None,
            "changed_files": list(result.get("changed_files") or []),
            "verification": result.get("verification_level"),
            "tools": dict(by_tool.most_common()),
            "tools_per_model_call": round(sum(batch_sizes) / len(batch_sizes), 2) if batch_sizes else 0.0,
            "duplicate_reads": sum(count - 1 for count in reads.values() if count > 1),
            "shell_reads": shell_reads,
            "first_edit_seconds": round(first_edit, 1) if first_edit is not None else None,
            "top_errors": dict(errors.most_common(5))}


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    median = lambda key: round(statistics.median(row[key] for row in rows), 1)
    costs = [row["cost"] for row in rows if row.get("cost") is not None]
    passed = [row for row in rows if row.get("passed", row["state"] == "completed")]
    return {"tasks": len(rows), "passed": len(passed), "pass_rate": round(len(passed) / len(rows), 3),
            "median_seconds": median("seconds"), "median_model_calls": median("model_calls"),
            "total_tokens": sum(row["total_tokens"] for row in rows),
            "total_cost": round(sum(costs), 4) if costs else None,
            "failed_tool_calls": sum(row["failed_tool_calls"] for row in rows),
            "compactions": sum(row["compactions"] for row in rows)}


# --- model -----------------------------------------------------------------------------

class _ReadOnlyStore:
    """Enough of Store for ModelResolver, opened read-only so a running Dikdik is untouched."""

    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        return db


def resolve_model(data: Path = DEFAULT_DATA, profile: str | None = None):
    """The model to benchmark: environment variables first, else a Dikdik model profile
    (by name or id; the default profile when none is named)."""
    from .model_config import ModelResolver, from_environment
    from .secrets_store import SecretsStore
    if os.environ.get("DIKDIK_MODEL_URL") and not profile:
        return from_environment()
    store = _ReadOnlyStore(data / "state.sqlite3")
    resolver = ModelResolver(store, SecretsStore(data / "secrets.bin", data / "secrets.key"))
    profile_id = None
    if profile:
        with store.connect() as db:
            row = db.execute("SELECT id FROM profiles WHERE id=? OR name=?", (profile, profile)).fetchone()
        if row is None:
            raise ValueError(f"No model profile named {profile!r} in {data}")
        profile_id = row["id"]
    described = resolver.describe(profile_id)
    if not described.get("configured"):
        raise ValueError(described.get("problem") or "No model is configured")
    return resolver.resolve(profile_id)


# --- project copies --------------------------------------------------------------------

COPY_SKIP = shutil.ignore_patterns(".git", "node_modules", ".next", "dist", "build", "target", ".venv", "__pycache__")


def export_project(source: Path, ref: str | None, target: Path) -> None:
    """The project as committed at ``ref``: reproducible, and never the user's unsaved work.
    Without a ref (fixtures not yet committed) the folder is copied as it is."""
    if ref is None:
        shutil.copytree(source, target, ignore=COPY_SKIP)
        return
    archive = target.parent / "export.zip"
    subprocess.run(["git", "-C", str(source), "archive", "--format=zip", "-o", str(archive), ref],
                   check=True, capture_output=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    archive.unlink()


# --- checks ----------------------------------------------------------------------------

def _crlf(text: str) -> bool:
    return text.count("\r\n") * 2 > text.count("\n")


def invariants(source: dict[str, str], root: Path, changed: list[str]) -> list[str]:
    """Problems no change should introduce: non-UTF-8 text and mixed line endings."""
    problems = []
    for path in changed:
        target = root / path
        if not target.is_file():
            continue
        try:
            text = target.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path}: not UTF-8")
            continue
        bare = len(re.findall(r"(?<!\r)\n", text))
        if path in source and _crlf(source[path]) and bare and "\r\n" in text:  # mixed, not a wholesale rewrite
            problems.append(f"{path}: {bare} LF line endings mixed into a CRLF file")
    return problems


def run_checks(checks: list[dict], root: Path, row: dict, suite: Path | None = None) -> list[dict]:
    """Score a finished task. Commands run in the project copy; {python} is this interpreter
    and {suite} the suite's folder, so checkers can live outside the project the agent edits."""
    from . import local_exec
    outcomes = []
    for check in checks:
        ok, detail = False, ""
        if "file" in check:
            target = root / check["file"]
            text = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
            if "exists" in check:
                ok = (text is not None) == bool(check["exists"])
            elif text is None:
                detail = "missing"
            elif "contains" in check:
                ok = check["contains"] in text
            elif "not_contains" in check:
                ok = check["not_contains"] not in text
            elif "matches" in check:
                ok = re.search(check["matches"], text) is not None
        elif "command" in check:
            command = check["command"].replace("{python}", f'"{Path(sys.executable).as_posix()}"').replace("{suite}", Path(suite or root).resolve().as_posix())
            ran = local_exec.run(root, command, threading.Event(), timeout=check.get("timeout", 300))
            ok, detail = ran["status"] == "passed", ran.get("output", "")[-600:]
        elif "changed" in check:
            ok = sorted(check["changed"]) == sorted((row.get("result") or {}).get("changed_files") or [])
        outcomes.append({"check": check, "passed": ok, **({"detail": detail} if detail and not ok else {})})
    return outcomes


# --- run -------------------------------------------------------------------------------

def run_task(spec: dict, config, base: Path, keep: bool = False) -> dict:
    """One benchmark task on a throwaway copy of its project."""
    from .engine import Engine, TERMINAL
    from .repository import inventory
    source = (base / spec["project"]).resolve()
    temp = Path(tempfile.mkdtemp(prefix="dikdik-bench-"))
    root, links = temp / "project", []
    engine = None
    try:
        export_project(source, spec.get("ref", "HEAD"), root)
        for name in spec.get("link", []):
            if (source / name).is_dir() and not (root / name).exists():
                link(source / name, root / name)
                links.append(root / name)
        if spec.get("setup"):
            # Install dependencies inside the copy. A linked node_modules resolves to the real
            # project, which dev servers refuse to load from and which leaks its real path.
            from . import local_exec
            prepared = local_exec.run(root, spec["setup"], threading.Event(), timeout=spec.get("setup_timeout", 900))
            if prepared["status"] != "passed":
                raise RuntimeError(f"setup failed: {prepared.get('output', '')[-800:]}")
        before = inventory(root)
        engine = Engine(root, temp / "data")
        engine.models.resolve = lambda profile_id=None: config
        engine.models.describe = lambda profile_id=None: {"configured": True, **config.public}
        limits = {key: spec[key] for key in ("max_model_calls", "max_tool_calls", "deadline_seconds") if key in spec}
        request = {**REQUEST, "objective": spec["objective"], "approve_browser": bool(spec.get("browser"))}
        if limits:
            request["agent_profile"] = engine.agents.create({"name": "Benchmark", **limits})["id"]
        started = time.monotonic()
        task = engine.submit(request)
        hard_limit = spec.get("deadline_seconds", 1800) + 120
        answered = set()
        while True:
            row = engine.store.get(task, include_events=False)
            if row["state"] in TERMINAL and not engine.busy(task):
                break
            # Nobody is at the keyboard: answer clarifying questions as a busy user would,
            # instead of letting the run wait out its whole time limit.
            for event in engine.store.get(task)["events"]:
                if event["kind"] == "question_requested" and event["data"].get("id") not in answered:
                    answered.add(event["data"]["id"])
                    engine.answer_question(task, {"id": event["data"]["id"], "answer": spec.get("answer", DEFAULT_ANSWER)})
            if time.monotonic() - started > hard_limit:
                engine.cancel(task)
                hard_limit += 60
            time.sleep(0.5)
        row = engine.store.get(task)
        measured = metrics(row["state"], row["result"], row["events"])
        checks = run_checks(spec.get("checks", []), root, row, base)
        problems = invariants(before, root, measured["changed_files"])
        expected = spec.get("expect_state", "completed")
        targets = {key: measured[key] <= limit for key, limit in (spec.get("targets") or {}).items() if key in measured}
        return {"name": spec["name"], **measured, "expected_state": expected,
                "passed": measured["state"] == expected and all(c["passed"] for c in checks) and not problems,
                "checks": checks, "invariant_problems": problems, "on_target": targets,
                "error": (row["result"] or {}).get("error") or (row["result"] or {}).get("pause_reason"),
                **({"workdir": str(root)} if keep else {})}
    finally:
        if engine is not None:
            engine.close()
        for path in links:
            unlink(path)
        if not keep:
            shutil.rmtree(temp, ignore_errors=True)


def run_suite(path: Path, *, profile=None, only=None, repeat=1, data=DEFAULT_DATA, keep=False, out=None) -> dict:
    suite = json.loads(path.read_text(encoding="utf-8"))
    tasks = [task for task in suite["tasks"] if not only or task["name"] in only]
    if not tasks:
        raise ValueError("No benchmark tasks selected")
    config = resolve_model(data, profile)
    rows = []
    from .interactive_browser import available as browser_available
    for spec in tasks:
        if spec.get("needs") == "playwright" and not browser_available():
            print(f"-> {spec['name']} skipped: needs Playwright (install Dikdik's desktop support)", flush=True)
            continue
        for attempt in range(repeat):
            print(f"-> {spec['name']} ({attempt + 1}/{repeat})", flush=True)
            try:
                row = run_task(spec, config, path.parent, keep)
            except Exception as exc:  # one broken task must not lose the rest of the report
                row = {"name": spec["name"], **metrics("error", None, []), "passed": False, "error": f"{type(exc).__name__}: {exc}"}
            rows.append(row)
            print(_table([row], header=False), flush=True)
    report = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "suite": str(path),
              "model": config.public.get("model"), "provider": config.public.get("provider_name") or config.public.get("host"),
              "summary": summarize(rows), "results": rows}
    if out:
        Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# --- overhead --------------------------------------------------------------------------

class _Scripted:
    def __init__(self, replies):
        self.replies = list(replies)

    def respond(self, messages, tools):
        return self.replies.pop(0)


def overhead(files: int = 350, steps: int = 30, file_bytes: int = 10_000) -> dict:
    """Dikdik's own cost per step on a project of realistic size, with a model that answers
    instantly: what remains is inventory, checkpoints, events, hooks and file writes."""
    from .agent import ModelResponse, ToolCall
    from .engine import Engine, TERMINAL
    steps = min(steps, files)
    replies = []
    for i in range(steps):
        replies.append(ModelResponse("", [ToolCall(f"r{i}", "read_file", {"path": f"src/module{i}.ts"}),
                                          ToolCall(f"s{i}", "search_text", {"query": f"value{i} ="})]))
        replies.append(ModelResponse("", [ToolCall(f"e{i}", "edit_file", {"path": f"src/module{i}.ts",
                                                                          "before": f"value{i} = 0", "after": f"value{i} = 1"})]))
    replies.append(ModelResponse("Done.", []))
    with tempfile.TemporaryDirectory(prefix="dikdik-overhead-") as temp:
        root = Path(temp) / "project"
        (root / "src").mkdir(parents=True)
        filler = "// " + "x" * 96 + "\n"
        for i in range(files):
            body = f"export const value{i} = 0;\n" + filler * max(0, (file_bytes - 30) // len(filler))
            (root / "src" / f"module{i}.ts").write_text(body, encoding="utf-8")
        environment = {"DIKDIK_MODEL_URL": "https://bench.invalid/v1/chat/completions", "DIKDIK_MODEL": "scripted", "DIKDIK_API_KEY": "scripted"}
        with patch.dict(os.environ, environment), patch("dikdik.engine.CompatibleAgentModel", return_value=_Scripted(replies)):
            engine = Engine(root, Path(temp) / "data")
            try:
                task = engine.submit({**REQUEST, "objective": "Set each module value to 1"})
                while True:
                    if engine.store.get(task, include_events=False)["state"] in TERMINAL and not engine.busy(task):
                        break
                    time.sleep(0.05)
                row = engine.store.get(task)
                checkpoint = engine._transcript_path(task)
                size = checkpoint.stat().st_size if checkpoint.is_file() else 0
            finally:
                engine.close()
    measured = metrics(row["state"], row["result"], row["events"])
    return {"state": row["state"], "files": files, "steps": steps, "model_calls": measured["model_calls"],
            "tool_calls": measured["tool_calls"], "seconds": measured["seconds"],
            "ms_per_model_call": round(1000 * measured["seconds"] / max(1, measured["model_calls"]), 1),
            "checkpoint_bytes": size}


# --- history ---------------------------------------------------------------------------

def history(data: Path = DEFAULT_DATA, limit: int = 20) -> list[dict]:
    """Metrics of the most recent real agent tasks, read-only."""
    db = _ReadOnlyStore(data / "state.sqlite3").connect()
    rows = []
    for task in db.execute("SELECT * FROM tasks ORDER BY created DESC LIMIT ?", (limit,)):
        request = json.loads(task["request"])
        if request.get("mode") != "agent":
            continue
        events = [{"kind": e["kind"], "time": e["time"], "data": json.loads(e["data"])}
                  for e in db.execute("SELECT kind, time, data FROM events WHERE task_id=? ORDER BY seq", (task["id"],))]
        result = json.loads(task["result"]) if task["result"] else None
        rows.append({"name": " ".join(request.get("objective", "").split())[:48], "id": task["id"][:8],
                     **metrics(task["state"], result, events)})
    return rows


# --- output ----------------------------------------------------------------------------

COLUMNS = [("name", 34), ("state", 9), ("seconds", 8), ("model_calls", 6), ("tool_calls", 6),
           ("failed_tool_calls", 5), ("tools_per_model_call", 6), ("duplicate_reads", 5), ("shell_reads", 6),
           ("compactions", 5), ("total_tokens", 9), ("passed", 6)]
LABELS = {"model_calls": "model", "tool_calls": "tools", "failed_tool_calls": "fail", "tools_per_model_call": "batch",
          "duplicate_reads": "dupe", "shell_reads": "shrd", "compactions": "cmpct", "total_tokens": "tokens", "seconds": "secs"}


def _table(rows: list[dict], header: bool = True) -> str:
    lines = []
    if header:
        lines.append(" ".join(LABELS.get(key, key).ljust(width) for key, width in COLUMNS))
    for row in rows:
        lines.append(" ".join(str(row.get(key, "")).ljust(width)[:width] for key, width in COLUMNS))
    return "\n".join(lines)


def compare(before: dict, after: dict) -> str:
    """Per-task and overall change between two reports (after minus before)."""
    old = {row["name"]: row for row in before["results"]}
    lines = [f"{'task':34} {'passed':13} {'secs':>16} {'model calls':>14} {'tokens':>20}"]
    for row in after["results"]:
        prior = old.get(row["name"])
        if prior is None:
            continue
        delta = lambda key: f"{prior[key]}->{row[key]}"
        lines.append(f"{row['name'][:34]:34} {delta('passed'):13} {delta('seconds'):>16} {delta('model_calls'):>14} {delta('total_tokens'):>20}")
    for key in ("pass_rate", "median_seconds", "median_model_calls", "total_tokens", "total_cost"):
        lines.append(f"{key}: {before['summary'].get(key)} -> {after['summary'].get(key)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m dikdik.bench", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run benchmark tasks with a real model")
    run.add_argument("suite", type=Path)
    run.add_argument("--profile", help="Dikdik model profile name or id (default: environment, else default profile)")
    run.add_argument("--only", help="comma-separated task names")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--data", type=Path, default=DEFAULT_DATA)
    run.add_argument("--out", type=Path)
    run.add_argument("--keep", action="store_true", help="keep each task's project copy for inspection")
    diff = commands.add_parser("compare", help="compare two run reports")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    cost = commands.add_parser("overhead", help="measure Dikdik's own per-step cost (no model needed)")
    cost.add_argument("--files", type=int, default=350)
    cost.add_argument("--steps", type=int, default=30)
    past = commands.add_parser("history", help="metrics of recent real agent tasks")
    past.add_argument("--data", type=Path, default=DEFAULT_DATA)
    past.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            report = run_suite(args.suite, profile=args.profile, only=set(args.only.split(",")) if args.only else None,
                               repeat=args.repeat, data=args.data, keep=args.keep, out=args.out)
            print(_table(report["results"]))
            print(json.dumps(report["summary"], indent=2))
        elif args.command == "compare":
            load = lambda path: json.loads(path.read_text(encoding="utf-8"))
            print(compare(load(args.before), load(args.after)))
        elif args.command == "overhead":
            print(json.dumps(overhead(args.files, args.steps), indent=2))
        else:
            rows = history(args.data, args.limit)
            print(_table(rows))
            print(json.dumps(summarize(rows), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
