"""Engineering evidence for an agent turn: traceable requirements, a security scan, a release gate.

Everything here is deterministic. The model may *record* requirements and *name* the tests that
prove them, but it cannot mark anything verified: a requirement is verified only when each named
test appears in the output of a real passing test run on the current files, and the release
decision is computed from those runs and the scanner, never from what the model reports.

Decisions follow the constitution's release vocabulary:
APPROVED, APPROVED_WITH_DOCUMENTED_RISK, BLOCKED (evidence missing), REJECTED (known defect).
"""

from __future__ import annotations

import math
import re
import statistics

from . import accessibility, security_scan


def _tool(name: str, description: str, properties: dict, required: list) -> dict:
    # Same schema shape as agent._tool; defined here because agent.py imports this module.
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False}}}


KINDS = ("explicit", "implied", "engineering", "optional")
PRIORITIES = ("must", "should", "could")
REQUIREMENT_ID = re.compile(r"REQ-[0-9]{1,4}")
MAX_REQUIREMENTS = 100

RECORD_REQUIREMENTS = _tool(
    "record_requirements",
    "Record the request as traceable requirements (replaces the whole list). Separate what the user asked for "
    "(explicit) from what it implies (implied), what a working product needs (engineering) and optional extras; never "
    "promote optional extras to must. Name the tests that prove each requirement: a requirement counts as verified only "
    "when those tests appear in a real passing run_tests output on the current files. Recording does not verify anything.",
    {"requirements": {"type": "array", "maxItems": MAX_REQUIREMENTS, "items": {"type": "object", "properties": {
        "id": {"type": "string", "description": "REQ-1, REQ-2, ..."},
        "description": {"type": "string"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "priority": {"type": "string", "enum": list(PRIORITIES)},
        "acceptance": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "tests": {"type": "array", "items": {"type": "string"}, "maxItems": 20,
                  "description": "Test names as the test runner prints them, e.g. test_add"}},
        "required": ["id", "description", "kind", "priority"], "additionalProperties": False}}},
    ["requirements"])
SECURITY_SCAN = _tool(
    "security_scan",
    "Scan the current files for committed secrets and dangerous code patterns (shell injection, eval, unsafe "
    "deserialization, disabled TLS checks, SQL built by formatting, HTML injection sinks). Deterministic pattern and "
    "AST rules: a clean result means these rules found nothing, not that the code is secure.",
    {"changed_only": {"type": "boolean", "description": "Only files this turn changed (default true)"}}, [])
RELEASE_GATE = _tool(
    "release_gate",
    "Compute the release decision for the current files from real evidence: test runs, the security scan of changed "
    "files and requirement traceability. Use it before finish and report its decision and open risks honestly.",
    {}, [])
QUALITY_CHECK = _tool(
    "quality_check",
    "Check the files this turn changed for reliability (missing timeouts, silently ignored errors), "
    "internationalization (naive timestamps, platform-default encodings, money as floats), privacy (credentials or "
    "personal data in logs) and static accessibility of HTML (names, labels, language, title, headings). Deterministic "
    "rules; rendered accessibility, contrast and focus are measured by preview_app.",
    {}, [])
MEASURE_PERFORMANCE = _tool(
    "measure_performance",
    "Measure a read-only command's wall time over several runs (for example a benchmark script, a build, or a "
    "request loop) and check it against an explicit budget and the project's saved baseline. Every run must exit 0 and "
    "change no files. Reports min, median, p95 and max. Measurements include process start-up and machine noise; "
    "compare like with like.",
    {"name": {"type": "string", "description": "Short stable name for this measurement, e.g. startup or search-1k"},
     "command": {"type": "string"}, "runs": {"type": "integer", "minimum": 1, "maximum": 20},
     "budget_ms": {"type": "number", "minimum": 1, "description": "Fail the budget when p95 exceeds this"},
     "save_baseline": {"type": "boolean", "description": "Record this result as the baseline (only when it passes)"},
     "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300}},
    ["name", "command"])
ARCHITECTURE_MAP = _tool(
    "architecture_map",
    "Map the project's real module dependency graph from its import statements: module and import counts, "
    "import cycles, the most depended-on modules, external packages, modules nothing imports, and a package-level "
    "Mermaid diagram. Use it before structural changes. Static imports only.", {}, [])
IMPACTED_TESTS = _tool(
    "impacted_tests",
    "List the tests that depend (through imports, transitively) on the given files, or on the files this turn changed. "
    "Run these first for fast feedback; run_tests still runs the full suite before finish. Tests that import nothing "
    "resolvable are reported separately as unmapped, never assumed unaffected.",
    {"paths": {"type": "array", "items": {"type": "string"}, "maxItems": 200}}, [])
RECORD_DECISION = _tool(
    "record_decision",
    "Write an architecture decision record to docs/adr/NNNN-title.md (numbered after existing records) with context, "
    "alternatives, decision, consequences, risks and reversal conditions. Use it for decisions a future maintainer "
    "needs to understand, not for routine edits.",
    {"title": {"type": "string"}, "context": {"type": "string"}, "alternatives": {"type": "string"},
     "decision": {"type": "string"}, "consequences": {"type": "string"}, "risks": {"type": "string"},
     "reversal": {"type": "string"}, "status": {"type": "string", "enum": ["proposed", "accepted", "superseded", "deprecated"]}},
    ["title", "context", "decision", "consequences"])
LOAD_TEST = _tool(
    "load_test",
    "Start the project's server (use {port} in the command), send many HTTP requests to one path at a set concurrency "
    "from this machine, and report throughput, p50/p95/p99 latency, status counts and the error rate (connection errors "
    "and 5xx), checked against an optional p95 budget and allowed error rate. Only the project's own loopback server "
    "is reachable. Requests can change the app's data; use GET unless the request is about writes.",
    {"command": {"type": "string"}, "path": {"type": "string"}, "method": {"type": "string"},
     "requests": {"type": "integer", "minimum": 1, "maximum": 5000}, "concurrency": {"type": "integer", "minimum": 1, "maximum": 50},
     "body": {"type": "string"}, "content_type": {"type": "string"},
     "p95_budget_ms": {"type": "number", "minimum": 1}, "max_error_rate": {"type": "number", "minimum": 0, "maximum": 1}},
    ["command"])
DAST_SCAN = _tool(
    "dast_scan",
    "Start the project's web server (use {port} in the command) and run dynamic security checks against it on loopback: "
    "crawl same-origin links and GET forms from the start paths, then check security headers, cookie flags, CORS, "
    "reflected input (XSS), SQL and stack-trace error disclosure, open redirects, exposed .git/.env files and directory "
    "listings. GET requests only: POST forms are reported, never submitted. A bounded smoke scan, not a penetration test.",
    {"command": {"type": "string"}, "paths": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
     "max_pages": {"type": "integer", "minimum": 1, "maximum": 60}},
    ["command"])
FAULT_TEST = _tool(
    "fault_test",
    "Check how the project's service behaves when a dependency fails. dependency_command starts the dependency (a stub "
    "API, a database, another service; {port} is its port); command starts the application with {port} for itself and "
    "{dependency_port} for the dependency it calls, which points at a fault proxy. Each scenario injects latency, "
    "connection resets, early close, a blackhole (partition) or the dependency being down, then clears it. The app passes "
    "when it answers path within max_ms with a handled status (below 500, or 502/503/504 unless degraded_statuses is "
    "given), stays running, and recovers within recovery_seconds.",
    {"command": {"type": "string"}, "dependency_command": {"type": "string"}, "path": {"type": "string"},
     "scenarios": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
         "fault": {"type": "string", "enum": ["latency", "reset", "close", "blackhole", "down"]},
         "latency_ms": {"type": "integer", "minimum": 0, "maximum": 30000}}, "required": ["fault"], "additionalProperties": False}},
     "max_ms": {"type": "integer", "minimum": 100, "maximum": 60000}, "recovery_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
     "degraded_statuses": {"type": "array", "items": {"type": "integer"}}},
    ["command", "dependency_command"])
REPEAT_COMMAND = _tool(
    "repeat_command",
    "Run the same command several times (for example the test suite or one test) and report whether it passes every "
    "time, fails every time or is flaky. Use it to expose order-, timing- or concurrency-dependent failures. "
    "Every run must change no files.",
    {"command": {"type": "string"}, "runs": {"type": "integer", "minimum": 2, "maximum": 20},
     "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300}},
    ["command"])
TOOLS = [RECORD_REQUIREMENTS, SECURITY_SCAN, QUALITY_CHECK, ARCHITECTURE_MAP, IMPACTED_TESTS, RECORD_DECISION, RELEASE_GATE]
MEASUREMENT_NAME = re.compile(r"[\w][\w .-]{0,59}")
REGRESSION_RATIO, REGRESSION_FLOOR_MS = 1.2, 25.0  # slower than 120% of baseline and by more than 25 ms


class Baselines:
    """Saved performance baselines for one project, kept in the shared settings table."""

    def __init__(self, store, workspace: str):
        self.store, self.prefix = store, f"perf_baseline:{workspace}:"

    def get(self, name: str) -> dict | None:
        import json
        with self.store.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (self.prefix + name,)).fetchone()
        try:
            return json.loads(row[0]) if row else None
        except ValueError:
            return None

    def put(self, name: str, record: dict) -> None:
        import json
        with self.store.connect() as db:
            db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (self.prefix + name, json.dumps(record)))


def repeat(run, candidate: dict[str, str], arguments: dict) -> dict:
    """Run a command `runs` times through the agent's shell and classify the outcomes."""
    runs = arguments.get("runs", 5)
    outcomes = []
    for index in range(runs):
        ran = run(dict(candidate), arguments["command"], arguments.get("timeout_seconds", 120))
        if ran.get("changed"):
            raise ValueError("The repeated command changed project files; repeat a command that only reads")
        outcomes.append({"run": index + 1, "status": ran.get("status"), "exit_code": ran.get("exit_code"),
                         **({"output_tail": str(ran.get("output", ""))[-1500:]} if ran.get("status") != "passed" else {})})
    passed = sum(o["status"] == "passed" for o in outcomes)
    verdict = "STABLE_PASS" if passed == runs else "STABLE_FAIL" if passed == 0 else "FLAKY"
    detail = f"{arguments['command'][:80]}: {passed}/{runs} runs passed"
    return {"verdict": verdict, "passed": passed, "runs": runs, "outcomes": outcomes,
            "results": [{"name": "stability", "status": "PASS" if verdict == "STABLE_PASS" else verdict, "detail": detail}],
            "note": "Repetition exposes nondeterminism; it cannot prove its absence."}


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]  # nearest rank


def measure(run, candidate: dict[str, str], arguments: dict, baselines=None, clock=None) -> dict:
    """Run `arguments["command"]` through `run(files, command, timeout, image)` (the agent's shell)
    several times and compare the timings with the budget and the saved baseline."""
    import time
    clock = clock or time.perf_counter
    name = arguments.get("name")
    if not isinstance(name, str) or not MEASUREMENT_NAME.fullmatch(name):
        raise ValueError("name must be 1-60 letters, digits, spaces, '.', '-' or '_'")
    runs = arguments.get("runs", 5)
    budget = arguments.get("budget_ms")
    timings = []
    for _ in range(runs):
        started = clock()
        ran = run(dict(candidate), arguments["command"], arguments.get("timeout_seconds", 60))
        elapsed = (clock() - started) * 1000
        if ran.get("status") != "passed":
            return {"name": name, "status": "FAILED", "runs_completed": len(timings),
                    "error": ran.get("error") or f"Run {len(timings) + 1} exited {ran.get('exit_code')}",
                    "output": str(ran.get("output", ""))[-4000:],
                    "results": [{"name": name, "status": "FAILED", "detail": f"{name}: the measured command failed"}]}
        if ran.get("changed"):
            raise ValueError("The measured command changed project files; measure a command that only reads")
        timings.append(round(elapsed, 3))
    stats = {"runs": len(timings), "min_ms": min(timings), "median_ms": round(statistics.median(timings), 3),
             "p95_ms": _percentile(timings, 0.95), "max_ms": max(timings)}
    results = []
    if budget is not None:
        within = stats["p95_ms"] <= budget
        results.append({"name": name, "check": "budget", "status": "PASS" if within else "OVER_BUDGET",
                        "detail": f"{name} p95 {stats['p95_ms']:.0f} ms {'<=' if within else '>'} budget {budget:.0f} ms"})
    previous = baselines.get(name) if baselines is not None else None
    if previous and previous.get("command") == arguments["command"]:
        limit = max(previous["median_ms"] * REGRESSION_RATIO, previous["median_ms"] + REGRESSION_FLOOR_MS)
        slower = stats["median_ms"] > limit
        results.append({"name": name, "check": "baseline", "status": "REGRESSION" if slower else "PASS",
                        "detail": f"{name} median {stats['median_ms']:.0f} ms vs baseline {previous['median_ms']:.0f} ms"
                                  + (" (slower beyond 20% and 25 ms)" if slower else "")})
    elif previous:
        results.append({"name": name, "check": "baseline", "status": "PASS",
                        "detail": f"{name}: the saved baseline measured a different command; not compared"})
    saved = False
    if arguments.get("save_baseline") is True and baselines is not None and all(r["status"] == "PASS" for r in results):
        baselines.put(name, {"command": arguments["command"], **stats})
        saved = True
    return {"name": name, "status": "PASS" if all(r["status"] == "PASS" for r in results) else "RISK", **stats,
            "timings_ms": timings, "results": results, "baseline": previous, "baseline_saved": saved,
            "limitations": "Wall time on this machine, including process start-up; noisy below a few milliseconds."}
UI_SUFFIXES = (".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".css", ".scss")


def parse_requirements(value) -> list[dict]:
    """Validate the model's list; anything malformed is rejected with a reason, not silently fixed."""
    if not isinstance(value, list) or len(value) > MAX_REQUIREMENTS:
        raise ValueError(f"requirements must be a list of at most {MAX_REQUIREMENTS} items")
    out, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each requirement must be an object")
        unknown = set(item) - {"id", "description", "kind", "priority", "acceptance", "tests"}
        if unknown:  # e.g. a self-reported "status": verification is computed, never recorded
            raise ValueError(f"Unsupported requirement fields: {', '.join(sorted(unknown))}")
        ident = item.get("id")
        if not isinstance(ident, str) or not REQUIREMENT_ID.fullmatch(ident) or ident in seen:
            raise ValueError("Requirement ids must be unique and look like REQ-1")
        seen.add(ident)
        description = item.get("description")
        if not isinstance(description, str) or not description.strip() or len(description) > 1000:
            raise ValueError(f"{ident}: description must be 1-1000 characters")
        if item.get("kind") not in KINDS or item.get("priority") not in PRIORITIES:
            raise ValueError(f"{ident}: kind must be one of {', '.join(KINDS)} and priority one of {', '.join(PRIORITIES)}")
        if item["kind"] == "optional" and item["priority"] == "must":
            raise ValueError(f"{ident}: an optional enhancement cannot be a must requirement")
        lists = {}
        for field, limit in (("acceptance", 10), ("tests", 20)):
            entries = item.get(field, [])
            if not isinstance(entries, list) or len(entries) > limit or not all(isinstance(e, str) and 0 < len(e) <= 300 for e in entries):
                raise ValueError(f"{ident}: {field} must be a list of at most {limit} short strings")
            lists[field] = [e.strip() for e in entries]
        out.append({"id": ident, "description": description.strip(), "kind": item["kind"], "priority": item["priority"], **lists})
    return out


def changed_files(candidate: dict[str, str], baseline: dict[str, str]) -> dict[str, str]:
    return {path: text for path, text in candidate.items() if baseline.get(path) != text}


def scan(candidate: dict[str, str], baseline: dict[str, str], changed_only: bool = True,
         categories: tuple[str, ...] = ("security",)) -> dict:
    files = changed_files(candidate, baseline) if changed_only else candidate
    report = security_scan.scan_files(files)
    findings = [f for f in report["findings"] if f["category"] in categories]
    if changed_only:
        # A finding counts against this turn only if the turn introduced it.
        before = {(f["rule"], f["file"], f["evidence"]) for f in security_scan.scan_files(
            {p: baseline[p] for p in files if p in baseline})["findings"]}
        findings = [f for f in findings if (f["rule"], f["file"], f["evidence"]) not in before]
    summary = {name: sum(f["severity"] == name for f in findings) for name in security_scan.SEVERITIES}
    return {"scope": "changed files" if changed_only else "all loaded files", "categories": list(categories),
            "scanned_files": report["scanned_files"],
            "summary": summary, "findings": findings[:50], "truncated": len(findings) > 50,
            "suppressed": report["suppressed"], "skipped": report["skipped"][:20],
            "limitations": "Pattern and AST rules only; no data-flow analysis. Clean means these rules found nothing."}


_UNITTEST = re.compile(r"^(\w+) \(([\w.]+)\)")
_UNITTEST_STATUS = re.compile(r"\.\.\. (ok|expected failure|FAIL|ERROR|skipped.*|unexpected success)\s*$")
_PYTEST = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")
_CARGO = re.compile(r"^test (\S+) \.\.\. (ok|FAILED|ignored)\s*$")
_GO = re.compile(r"^\s*--- (PASS|FAIL|SKIP): (\S+)")
_JS_PASS = re.compile(r"^\s*(?:✓|✔|√|\(pass\))\s+(.+?)(?:\s+\[[\d.]+\s*m?s\]|\s+\(\d+(?:\.\d+)?\s*m?s\))?\s*$")
_JS_FAIL = re.compile(r"^\s*(?:✗|✕|×|\(fail\))\s+(.+?)(?:\s+\[[\d.]+\s*m?s\]|\s+\(\d+(?:\.\d+)?\s*m?s\))?\s*$")


def test_results(output: str) -> dict[str, set[str]]:
    """Per-test outcomes parsed from runner output: {"passed": names, "failed": names}.

    Understands unittest -v, pytest -v, cargo test, go test -v and bun/jest/vitest reporters.
    Each test is recorded under every name a requirement might use for it (method, Class.method,
    the full dotted or :: id). Anything not reported as passing is never counted as passed."""
    passed, failed = set(), set()
    def record(outcome: bool, *names: str):
        (passed if outcome else failed).update(n for n in names if n)
    pending = None
    for line in output.splitlines():
        line = line.rstrip()
        match = _UNITTEST.match(line)
        if match:
            method, dotted = match.groups()
            pending = (method, dotted, ".".join(dotted.split(".")[-2:]))
        status = _UNITTEST_STATUS.search(line)
        if pending and status:  # the status can follow a docstring line
            record(status.group(1) in ("ok", "expected failure"), *pending)
            pending = None
            continue
        if match := _PYTEST.match(line):
            node, outcome = match.groups()
            parts = node.split("::")
            record(outcome in ("PASSED", "XFAIL"), node, parts[-1], "::".join(parts[1:]), ".".join(parts[1:]))
        elif match := _CARGO.match(line):
            name, outcome = match.groups()
            record(outcome == "ok", name, name.rsplit("::", 1)[-1])
        elif match := _GO.match(line):
            outcome, name = match.groups()
            record(outcome == "PASS", name, name.rsplit("/", 1)[-1])
        elif match := _JS_PASS.match(line):
            title = match.group(1).strip()
            record(True, title, title.rsplit(" > ", 1)[-1])
        elif match := _JS_FAIL.match(line):
            title = match.group(1).strip()
            record(False, title, title.rsplit(" > ", 1)[-1])
    return {"passed": passed - failed, "failed": failed}


def junit_results(xml_text: str) -> dict[str, set[str]]:
    """Per-test outcomes from a JUnit XML report (bun, jest-junit, pytest --junitxml, ...)."""
    import xml.etree.ElementTree as ElementTree
    passed, failed = set(), set()
    if not isinstance(xml_text, str) or len(xml_text) > 5_000_000:
        return {"passed": passed, "failed": failed}
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return {"passed": passed, "failed": failed}
    for case in root.iter("testcase"):
        name, classname = case.get("name", ""), case.get("classname", "")
        names = {name, f"{classname}.{name}" if classname else "", f"{classname} > {name}" if classname else ""} - {""}
        bad = any(child.tag in ("failure", "error", "skipped") for child in case)
        (failed if bad else passed).update(names)
    return {"passed": passed - failed, "failed": failed}


def _passing_results(checks: list[dict]) -> dict[str, set[str]]:
    """Per-test outcomes of the most recent passing test check: structured `tests` fields from
    the runners (JUnit) merged with what its output text reports."""
    for check in reversed(checks):
        if check.get("status") != "passed" or check.get("kind") == "python_syntax":
            continue
        passed, failed = set(), set()
        for source in [check, *[r for r in check.get("runs", []) if isinstance(r, dict)]]:
            structured = source.get("tests")
            if isinstance(structured, dict):
                passed.update(n for n in structured.get("passed", []) if isinstance(n, str))
                failed.update(n for n in structured.get("failed", []) if isinstance(n, str))
        parsed = test_results(check.get("output", "") if isinstance(check.get("output"), str) else "")
        passed |= parsed["passed"]
        failed |= parsed["failed"]
        return {"passed": passed - failed, "failed": failed}
    return {"passed": set(), "failed": set()}


def trace(requirements: list[dict], checks: list[dict], tests_current: bool) -> list[dict]:
    results = _passing_results(checks) if tests_current else {"passed": set(), "failed": set()}
    traced = []
    for requirement in requirements:
        tests = requirement.get("tests", [])
        found = [t for t in tests if t in results["passed"]]
        failing = [t for t in tests if t in results["failed"]]
        if not tests:
            status, evidence = "UNVERIFIED", "No test is linked to this requirement"
        elif not tests_current:
            status, evidence = "NOT_RUN", "No passing test run on the current files"
        elif failing:
            status, evidence = "FAILED", f"Reported failing or skipped: {', '.join(failing)}"
        elif len(found) == len(tests):
            status, evidence = "VERIFIED", f"Reported passing: {', '.join(found)}"
        else:
            missing = [t for t in tests if t not in found]
            status, evidence = "INCONCLUSIVE", (f"Not reported as passing: {', '.join(missing)}"
                                                + ("" if results["passed"] else " (the runner printed no per-test results)"))
        traced.append({"id": requirement["id"], "priority": requirement["priority"], "kind": requirement["kind"],
                       "status": status, "evidence": evidence})
    return traced


def quality(candidate: dict[str, str], baseline: dict[str, str]) -> dict:
    """Reliability, i18n and privacy findings this turn introduced, plus static HTML accessibility."""
    report = scan(candidate, baseline, categories=("reliability", "i18n", "privacy", "infrastructure"))
    report["limitations"] = "Pattern and AST rules on changed files; test files are exempt from these categories."
    introduced = []
    for path, text in changed_files(candidate, baseline).items():
        if not path.lower().endswith((".html", ".htm")):
            continue
        before = {}
        for violation in accessibility.check_html(baseline[path]) if path in baseline else []:
            before[violation["rule"]] = before.get(violation["rule"], 0) + 1
        for violation in accessibility.check_html(text):
            if before.get(violation["rule"], 0) > 0:
                before[violation["rule"]] -= 1  # already present before this turn
            else:
                introduced.append({"file": path, **violation})
    report["accessibility"] = {"introduced": introduced[:50],
                               "serious": sum(v["impact"] == "serious" for v in introduced),
                               "note": "Static HTML structure only; preview_app measures the rendered page."}
    return report


def release_gate(requirements: list[dict], checks: list[dict], tests_current: bool, verification_available: bool,
                 candidate: dict[str, str], baseline: dict[str, str], preview: dict | None = None,
                 performance: dict | None = None, stability: dict | None = None, dast: dict | None = None,
                 resilience: dict | None = None) -> dict:
    gates = []
    last = next((c for c in reversed(checks) if c.get("kind") != "python_syntax"), None)
    if tests_current:
        gates.append({"gate": "tests", "status": "PASS", "detail": "Tests passed on the current files"})
    elif last is not None and last.get("status") == "failed":
        gates.append({"gate": "tests", "status": "FAIL", "detail": "The latest test run failed"})
    elif not verification_available:
        gates.append({"gate": "tests", "status": "RISK", "detail": "No automated test runner is available; behaviour is unverified"})
    else:
        gates.append({"gate": "tests", "status": "MISSING", "detail": "No passing test run on the current files"})

    security = scan(candidate, baseline)
    counts = security["summary"]
    if counts["critical"]:
        gates.append({"gate": "security", "status": "FAIL", "detail": f"{counts['critical']} critical finding(s) introduced"})
    elif counts["high"]:
        gates.append({"gate": "security", "status": "RISK", "detail": f"{counts['high']} high-severity finding(s) need review"})
    else:
        gates.append({"gate": "security", "status": "PASS", "detail": f"No critical or high findings in {security['scanned_files']} changed file(s)"})

    checked = quality(candidate, baseline)
    qcounts = checked["summary"]
    if qcounts["critical"] or qcounts["high"] or qcounts["medium"]:
        gates.append({"gate": "quality", "status": "RISK", "detail": f"{qcounts['critical'] + qcounts['high'] + qcounts['medium']} "
                      "reliability, i18n or privacy finding(s) of medium or higher severity introduced"})
    else:
        gates.append({"gate": "quality", "status": "PASS", "detail": f"No medium-or-higher reliability, i18n or privacy findings"
                      + (f" ({qcounts['low']} low)" if qcounts["low"] else "")})

    ui_changed = [p for p in changed_files(candidate, baseline) if p.lower().endswith(UI_SUFFIXES)]
    rendered = [a for a in (preview or {}).get("accessibility", []) if isinstance(a, dict)]
    serious_rendered = sum(int(a.get("serious", 0)) for a in rendered)
    serious_static = checked["accessibility"]["serious"]
    if serious_rendered or serious_static:
        gates.append({"gate": "accessibility", "status": "RISK", "detail": f"{serious_rendered + serious_static} serious WCAG failure(s)"
                      f" ({'rendered preview' if serious_rendered else 'static HTML'})"})
    elif rendered:
        moderate = sum(int(a.get("moderate", 0)) for a in rendered)
        gates.append({"gate": "accessibility", "status": "PASS", "detail": "No serious failures in the rendered preview"
                      + (f"; {moderate} moderate item(s) to review" if moderate else "")})
    elif ui_changed:
        gates.append({"gate": "accessibility", "status": "RISK", "detail": "UI files changed but no rendered accessibility audit ran (use preview_app)"})
    else:
        gates.append({"gate": "accessibility", "status": "NOT_APPLICABLE", "detail": "No UI files changed"})

    compared = [v for v in (preview or {}).get("visual", []) if isinstance(v, dict)]
    if compared:
        changed = [v["view"] for v in compared if v.get("verdict") == "CHANGED"]
        new = [v["view"] for v in compared if v.get("verdict") == "NO_BASELINE"]
        if changed:
            gates.append({"gate": "visual", "status": "RISK", "detail": "Differs from the accepted baseline (owner review): " + ", ".join(changed),
                          "views": changed + new})
        elif len(new) == len(compared):
            gates.append({"gate": "visual", "status": "NOT_APPLICABLE", "detail": "No accepted baseline yet; captures recorded for the owner to accept",
                          "views": new})
        else:
            gates.append({"gate": "visual", "status": "PASS", "detail": f"{len(compared) - len(new)} view(s) match the accepted baseline"})

    if dast:
        counts = dast.get("summary", {})
        if counts.get("critical"):
            gates.append({"gate": "dynamic_security", "status": "FAIL", "detail": f"{counts['critical']} critical finding(s) on the running app"})
        elif counts.get("high"):
            gates.append({"gate": "dynamic_security", "status": "RISK", "detail": f"{counts['high']} high-severity finding(s) on the running app"})
        else:
            gates.append({"gate": "dynamic_security", "status": "PASS", "detail": f"No critical or high findings across {len(dast.get('pages', []))} page(s)"
                          + (f"; {counts['medium']} medium" if counts.get("medium") else "")})

    if resilience:
        broken = [r for r in resilience.get("results", []) if r.get("status") != "PASS"]
        gates.append({"gate": "resilience", "status": "RISK" if broken else "PASS",
                      "detail": "; ".join(r["detail"] for r in broken) if broken else f"{len(resilience.get('results', []))} dependency fault(s) handled"})

    if performance:
        failed = [r for r in performance.get("results", []) if r.get("status") != "PASS"]
        gates.append({"gate": "performance", "status": "RISK" if failed else "PASS",
                      "detail": ("; ".join(r.get("detail", "") for r in failed) if failed
                                 else f"{len(performance.get('results', []))} measured budget(s) met")})

    if stability:
        flaky = [r for r in stability.get("results", []) if r.get("status") != "PASS"]
        gates.append({"gate": "stability", "status": "RISK" if flaky else "PASS",
                      "detail": "; ".join(r["detail"] for r in flaky) if flaky else stability["results"][0]["detail"]})

    traced = trace(requirements, checks, tests_current)
    musts = [r for r in traced if r["priority"] == "must"]
    if not requirements:
        gates.append({"gate": "requirements", "status": "NOT_APPLICABLE", "detail": "No requirements were recorded"})
    elif all(r["status"] == "VERIFIED" for r in musts):
        gates.append({"gate": "requirements", "status": "PASS", "detail": f"{len(musts)} must requirement(s) verified by tests"})
    else:
        open_ids = [r["id"] for r in musts if r["status"] != "VERIFIED"]
        gates.append({"gate": "requirements", "status": "RISK", "detail": "Not verified by tests: " + ", ".join(open_ids)})

    statuses = {g["status"] for g in gates}
    decision = ("REJECTED" if "FAIL" in statuses else "BLOCKED" if "MISSING" in statuses
                else "APPROVED_WITH_DOCUMENTED_RISK" if "RISK" in statuses else "APPROVED")
    return {"decision": decision, "gates": gates, "requirements": traced,
            "security_findings": [{k: f[k] for k in ("id", "rule", "severity", "file", "line", "message")} for f in security["findings"][:20]],
            "quality_findings": [{k: f[k] for k in ("id", "rule", "category", "severity", "file", "line", "message")} for f in checked["findings"][:20]],
            "note": "Computed from test runs, the scanners, preview audits and measurements; model statements do not change it."}
