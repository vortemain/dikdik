"""Deterministic outcome evaluation. No model-quality claims or host repo execution."""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dikdik.engine import Engine, TERMINAL
from dikdik.repository import inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker", action="store_true", help="Explicitly approve repository tests in restricted Docker")
    parser.add_argument("--output")
    args = parser.parse_args()
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="dikdik-evaluate-") as temp:
        base = Path(temp)
        root = base / "repo"
        root.mkdir()
        fixture = Path(__file__).resolve().parents[1] / "examples" / "repair"
        for file in fixture.glob("*.py"):
            (root / file.name).write_bytes(file.read_bytes())
        source = inventory(root)
        engine = Engine(root, base / "data")
        try:
            cases = [
                ("exact_bugfix_candidate", "calculator.py", "return a - b", "return a + b", "completed" if not args.docker else None),
                ("test_weakening_denied", "test_calculator.py", "self.assertEqual(add(2, 3), 5)", "pass", "failed"),
                ("syntax_regression_detected", "calculator.py", "return a - b", "return (", "failed"),
                ("workspace_escape_denied", "../outside.py", "x", "y", "failed"),
            ]
            for name, path, before, after, expected in cases:
                task = engine.submit({"objective": name, "mode": "offline", "verification": "docker" if args.docker else "syntax", "approve_execution": args.docker, "edits": [{"path": path, "before": before, "after": after}]})
                deadline = time.monotonic() + 90
                while True:
                    result = engine.store.get(task)
                    if result["state"] in TERMINAL:
                        break
                    if time.monotonic() > deadline:
                        engine.cancel(task)
                        raise RuntimeError("Evaluation deadline exceeded")
                    time.sleep(.02)
                # Wait for worker resource release, not just its terminal DB write.
                while True:
                    with engine.lock:
                        active = bool(engine.active)
                    if not active:
                        break
                    time.sleep(.005)
                passed = result["state"] == expected if expected else result["state"] == "completed" and result["result"].get("tests_verified") is True
                unchanged = inventory(root) == source
                content_ok = True
                if name == "exact_bugfix_candidate":
                    content_ok = "return a + b" in (base / "data" / "artifacts" / task / "calculator.py").read_text()
                outcomes.append({"case": name, "outcome": "passed" if passed and unchanged and content_ok else "failed", "task_state": result["state"], "source_unchanged": unchanged, "tests_verified": result["result"].get("tests_verified", False)})
        finally:
            engine.close()
    report = {"mode": "explicit_edits_docker" if args.docker else "explicit_edits_syntax_only", "scope": "Deterministic orchestration and safety regression evaluation, NOT autonomous model task success", "cases": outcomes, "passed": sum(o["outcome"] == "passed" for o in outcomes), "total": len(outcomes)}
    rendered = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
