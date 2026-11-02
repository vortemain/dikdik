"""Mutation testing for Dikdik's critical modules, standard library only.

    python scripts/mutation_test.py dikdik/control.py tests.test_control [--max 60] [--min-score 0.8]

Each mutant changes one operator or constant in a copy of the package (the working tree is
never modified), then runs the named test modules against that copy. A mutant the tests fail
on is *killed*; one they still pass on *survived* and points at missing or weak assertions.
Mutants that time out count as killed. Equivalent mutants (changes with no observable effect)
are possible, so a survivor is a lead to inspect, not automatically a defect.
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWAPS = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt,
         ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}


def mutants(source: str):
    """(description, line, mutated source) for each single-point mutation, in a stable order."""
    tree = ast.parse(source)
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for index, op in enumerate(node.ops):
                if type(op) in SWAPS:
                    sites.append((node, "compare", index))
        elif isinstance(node, ast.BoolOp):
            sites.append((node, "boolop", None))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            sites.append((node, "not", None))
        elif isinstance(node, ast.Constant) and type(node.value) is bool:
            sites.append((node, "bool", None))
        elif isinstance(node, ast.Constant) and type(node.value) is int and not isinstance(node.value, bool):
            sites.append((node, "int", None))
    sites.sort(key=lambda site: (site[0].lineno, site[0].col_offset, site[1]))
    for position, (target, kind, index) in enumerate(sites):
        tree = ast.parse(source)
        node = [n for n in ast.walk(tree) if (getattr(n, "lineno", None), getattr(n, "col_offset", None), type(n)) ==
                (target.lineno, target.col_offset, type(target))][0]
        if kind == "compare":
            old = node.ops[index]
            node.ops[index] = SWAPS[type(old)]()
            description = f"{type(old).__name__} -> {type(node.ops[index]).__name__}"
        elif kind == "boolop":
            old = node.op
            node.op = ast.Or() if isinstance(old, ast.And) else ast.And()
            description = f"{type(old).__name__} -> {type(node.op).__name__}"
        elif kind == "not":
            parent = next(p for p in ast.walk(tree) for field, value in ast.iter_fields(p)
                          if value is node or (isinstance(value, list) and node in value))
            for field, value in ast.iter_fields(parent):
                if value is node:
                    setattr(parent, field, node.operand)
                elif isinstance(value, list) and node in value:
                    value[value.index(node)] = node.operand
            description = "remove not"
        elif kind == "bool":
            node.value = not node.value
            description = f"{not node.value} -> {node.value}"
        else:
            old_value = node.value
            node.value = old_value + 1
            description = f"{old_value} -> {node.value}"
        yield position, description, target.lineno, ast.unparse(tree)


def run_tests(workdir: Path, modules: list[str], timeout: float) -> str:
    try:
        result = subprocess.run([sys.executable, "-m", "unittest", *modules], cwd=workdir, capture_output=True,
                                timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "timeout"
    return "passed" if result.returncode == 0 else "failed"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="module file relative to the repository, e.g. dikdik/control.py")
    parser.add_argument("tests", nargs="+", help="unittest modules that should kill the mutants, e.g. tests.test_control")
    parser.add_argument("--max", type=int, default=60, help="evenly sample at most this many mutants")
    parser.add_argument("--min-score", type=float, default=0.0, help="exit 1 when the kill ratio is below this")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--json", help="write the full report to this file")
    args = parser.parse_args(argv)
    target = (ROOT / args.target).resolve()
    source = target.read_text(encoding="utf-8")
    every = list(mutants(source))
    step = max(1, len(every) // args.max) if args.max else 1
    chosen = every[::step][: args.max] if args.max else every
    with tempfile.TemporaryDirectory(prefix="dikdik-mutation-") as temp:
        work = Path(temp)
        shutil.copytree(ROOT / "dikdik", work / "dikdik", ignore=shutil.ignore_patterns("__pycache__", "*.pyd", "*.so"))
        shutil.copytree(ROOT / "tests", work / "tests", ignore=shutil.ignore_patterns("__pycache__", "web"))
        copy = work / target.relative_to(ROOT)
        if run_tests(work, args.tests, args.timeout) != "passed":
            print("The tests do not pass on the unmutated code; fix that first.", file=sys.stderr)
            return 2
        results = []
        for position, description, line, mutated in chosen:
            copy.write_text(mutated, encoding="utf-8")
            outcome = run_tests(work, args.tests, args.timeout)
            results.append({"id": position, "line": line, "mutation": description,
                            "status": "survived" if outcome == "passed" else "killed", "outcome": outcome})
            print(f"{results[-1]['status']:8} {args.target}:{line}  {description}", flush=True)
        copy.write_text(source, encoding="utf-8")
    killed = sum(r["status"] == "killed" for r in results)
    score = killed / len(results) if results else 1.0
    report = {"target": args.target, "tests": args.tests, "mutants_total": len(every), "mutants_run": len(results),
              "killed": killed, "survived": len(results) - killed, "score": round(score, 3),
              "survivors": [r for r in results if r["status"] == "survived"]}
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{args.target}: {killed}/{len(results)} mutants killed (score {score:.0%}; {len(every)} possible)")
    return 1 if score < args.min_score else 0


if __name__ == "__main__":
    sys.exit(main())
