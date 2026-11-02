"""Architecture evidence from the code itself: the module dependency graph, its cycles and
structure, change impact (which tests depend on what changed) and numbered decision records.

The graph is built from real import statements — Python `import`/`from` (absolute and relative)
and JavaScript/TypeScript `import`, `export ... from`, `require()` and `import()` with relative
specifiers — resolved to files in the project. Dynamic imports, plugin loading by name and
imports through string variables are invisible to it, so the change-impact selection always
reports tests it could not map, and the full suite remains the release check.

    python -m dikdik.architecture [PATH] [--changed FILE ...] [--mermaid] [--json]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path, PurePosixPath

PY = (".py", ".pyw")
JS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
JS_IMPORT = re.compile(r"""(?:\bimport\s+(?:[\w*{}\s,$]+\s+from\s+)?|\bexport\s+[\w*{}\s,$]*\s*from\s+|\brequire\s*\(\s*|\bimport\s*\(\s*)["']([^"']+)["']""")
TEST = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$")


def is_test(path: str) -> bool:
    return TEST.search(path) is not None


def _python_module_index(paths: set[str]) -> dict[str, str]:
    """Dotted module name -> file, for every Python file, under each plausible source root."""
    index = {}
    for path in paths:
        if not path.endswith(PY):
            continue
        parts = list(PurePosixPath(path).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for start in range(len(parts)):  # src/pkg/mod.py is importable as pkg.mod and src.pkg.mod
            if parts[start:]:
                index.setdefault(".".join(parts[start:]), path)
    return index


def _python_imports(path: str, text: str, index: dict[str, str]) -> tuple[set[str], set[str]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set(), set()
    package = list(PurePosixPath(path).parent.parts)
    internal, external = set(), set()

    def resolve(name: str):
        candidates = [name]
        while "." in candidates[-1]:
            candidates.append(candidates[-1].rsplit(".", 1)[0])
        return next((index[c] for c in candidates if c in index), None)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = resolve(alias.name)
                (internal.add(target) if target else external.add(alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                prefix = ".".join([*base, *(node.module.split(".") if node.module else [])])
                names = [f"{prefix}.{alias.name}" for alias in node.names] + [prefix]
            else:
                names = [f"{node.module}.{alias.name}" for alias in node.names] + [node.module or ""]
            target = next((resolve(n) for n in names if n and resolve(n)), None)
            if target:
                for n in names[:-1]:  # `from pkg import a, b` may import submodules a and b
                    sub = index.get(n)
                    if sub:
                        internal.add(sub)
                internal.add(target)
            elif not node.level and node.module:
                external.add(node.module.split(".")[0])
    # Importing pkg.mod executes pkg/__init__.py first, so a package's initializers are dependencies too.
    initializers = {index[name] for name in index if index[name].endswith("/__init__.py")}
    for target in list(internal):
        folder = PurePosixPath(target).parent
        while folder.parts:
            init = f"{folder.as_posix()}/__init__.py"
            if init in initializers:
                internal.add(init)
            folder = folder.parent
    internal.discard(path)
    return internal, external


def _js_imports(path: str, text: str, paths: set[str]) -> tuple[set[str], set[str]]:
    internal, external = set(), set()
    folder = PurePosixPath(path).parent
    for specifier in JS_IMPORT.findall(text):
        if not specifier.startswith("."):
            name = specifier.split("/")[0] if not specifier.startswith("@") else "/".join(specifier.split("/")[:2])
            external.add(name.removeprefix("node:"))
            continue
        base = PurePosixPath(*[p for p in (folder / specifier).parts])
        normalized = []
        for part in base.parts:
            if part == "..":
                if normalized:
                    normalized.pop()
            elif part != ".":
                normalized.append(part)
        stem = "/".join(normalized)
        options = [stem, *[stem + suffix for suffix in JS], *[f"{stem}/index{suffix}" for suffix in JS]]
        if stem.endswith((".js", ".jsx")):  # TypeScript imports name the emitted .js file
            options += [stem.rsplit(".", 1)[0] + suffix for suffix in (".ts", ".tsx")]
        target = next((o for o in options if o in paths), None)
        if target and target != path:
            internal.add(target)
    return internal, external


def dependency_graph(files: dict[str, str]) -> dict:
    """{"edges": {file: [files it imports]}, "external": {file: [packages]}} for source files."""
    sources = {p: t for p, t in files.items() if p.endswith(PY + JS) and isinstance(t, str)}
    paths = set(sources)
    index = _python_module_index(paths)
    edges, external = {}, {}
    for path, text in sorted(sources.items()):
        internal, outside = (_python_imports(path, text, index) if path.endswith(PY) else _js_imports(path, text, paths))
        edges[path] = sorted(internal)
        external[path] = sorted(outside)
    return {"edges": edges, "external": external}


def cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components with more than one module (Tarjan, iterative)."""
    index, low, on_stack, stack, found, counter = {}, {}, set(), [], [], [0]
    for root in sorted(edges):
        if root in index:
            continue
        work = [(root, iter(edges.get(root, [])))]
        index[root] = low[root] = counter[0]; counter[0] += 1
        stack.append(root); on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter[0]; counter[0] += 1
                    stack.append(child); on_stack.add(child)
                    work.append((child, iter(edges.get(child, []))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop(); on_stack.discard(member); component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    found.append(sorted(component))
    return sorted(found, key=lambda c: (-len(c), c))


def reverse(edges: dict[str, list[str]]) -> dict[str, set[str]]:
    users = {node: set() for node in edges}
    for node, targets in edges.items():
        for target in targets:
            users.setdefault(target, set()).add(node)
    return users


def impact(edges: dict[str, list[str]], changed: list[str]) -> dict:
    """Modules that depend on the changed files (transitively) and the tests among them."""
    users = reverse(edges)
    affected, queue = set(), [c for c in changed if c in users or c in edges]
    while queue:
        node = queue.pop()
        if node in affected:
            continue
        affected.add(node)
        queue.extend(users.get(node, ()))
    tests = sorted(p for p in affected if is_test(p))
    all_tests = sorted(p for p in edges if is_test(p))
    unmapped = sorted(p for p in all_tests if not edges.get(p))  # imports nothing we can resolve
    unknown = sorted(c for c in changed if c not in edges and c not in users)
    return {"changed": sorted(changed), "affected_modules": sorted(affected - set(tests)), "tests": tests,
            "unmapped_tests": unmapped, "unresolved_changes": unknown, "total_tests": len(all_tests),
            "note": "Run these tests first for fast feedback; run the full suite before release. "
                    "Unmapped tests import nothing resolvable (they may exercise code through subprocesses or "
                    "dynamic loading) and are not proven unaffected."}


def summary(files: dict[str, str], top: int = 10) -> dict:
    graph = dependency_graph(files)
    edges = graph["edges"]
    users = reverse(edges)
    production = [p for p in edges if not is_test(p)]
    fan_in = sorted(((len(users.get(p, ())), p) for p in production), reverse=True)[:top]
    fan_out = sorted(((len(edges[p]), p) for p in production), reverse=True)[:top]
    packages = {}
    for name in [n for names in graph["external"].values() for n in names]:
        packages[name] = packages.get(name, 0) + 1
    entry_like = re.compile(r"(^|/)(__main__|main|cli|server|app|index|setup|conftest|manage)\.\w+$|^scripts/")
    orphans = sorted(p for p in production if not users.get(p) and not entry_like.search(p) and not p.endswith("__init__.py"))
    return {"modules": len(edges), "production_modules": len(production), "test_modules": len(edges) - len(production),
            "edges": sum(len(t) for t in edges.values()), "cycles": cycles(edges),
            "most_depended_on": [{"module": p, "dependents": n} for n, p in fan_in if n],
            "most_dependencies": [{"module": p, "imports": n} for n, p in fan_out if n],
            "external_packages": sorted(packages, key=lambda k: (-packages[k], k))[:40],
            "not_imported": orphans[:40],
            "mermaid": mermaid(edges),
            "limitations": "Static imports only; dynamic imports and name-based plugin loading are not seen."}


def mermaid(edges: dict[str, list[str]], depth: int = 1) -> str:
    """A package-level flowchart (production code only) small enough to read."""
    def group(path):
        parts = PurePosixPath(path).parts
        return "/".join(parts[:depth]) if len(parts) > depth else PurePosixPath(path).stem
    links = set()
    for source, targets in edges.items():
        if is_test(source):
            continue
        for target in targets:
            a, b = group(source), group(target)
            if a != b and not is_test(target):
                links.add((a, b))
    ident = lambda name: re.sub(r"\W", "_", name) or "root"
    lines = ["flowchart LR"] + [f'    {ident(a)}["{a}"] --> {ident(b)}["{b}"]' for a, b in sorted(links)]
    return "\n".join(lines) if links else "flowchart LR\n    single[\"one package\"]"


# --- architecture decision records ---------------------------------------------------------------
ADR_DIR = "docs/adr"
ADR_FIELDS = ("title", "context", "decision", "alternatives", "consequences", "risks", "reversal")
ADR_STATUS = ("proposed", "accepted", "superseded", "deprecated")


def next_adr(paths) -> tuple[int, str]:
    numbers = [int(m.group(1)) for p in paths if (m := re.match(rf"{ADR_DIR}/(\d{{4}})-", p))]
    return (max(numbers) + 1 if numbers else 1), ADR_DIR


def adr(number: int, fields: dict, date: str) -> tuple[str, str]:
    """(path, markdown) for a decision record with the constitution's required sections."""
    missing = [f for f in ("title", "context", "decision", "consequences") if not str(fields.get(f, "")).strip()]
    if missing:
        raise ValueError("A decision record needs " + ", ".join(missing))
    status = fields.get("status", "accepted")
    if status not in ADR_STATUS:
        raise ValueError("status must be one of " + ", ".join(ADR_STATUS))
    for key, value in fields.items():
        if key not in (*ADR_FIELDS, "status") or not isinstance(value, str) or len(value) > 6000:
            raise ValueError(f"Unsupported or oversized decision field: {key}")
    slug = re.sub(r"[^a-z0-9]+", "-", fields["title"].lower()).strip("-")[:60] or "decision"
    sections = [f"# ADR-{number:04d}: {fields['title'].strip()}", "", f"- Status: {status}", f"- Date: {date}", ""]
    for key, heading in (("context", "Context"), ("alternatives", "Alternatives considered"), ("decision", "Decision"),
                         ("consequences", "Consequences"), ("risks", "Risks"), ("reversal", "Reversal conditions")):
        if str(fields.get(key, "")).strip():
            sections += [f"## {heading}", "", fields[key].strip(), ""]
    return f"{ADR_DIR}/{number:04d}-{slug}.md", "\n".join(sections)


def main(argv=None) -> int:
    from .security_scan import project_files
    parser = argparse.ArgumentParser(description="Module dependency graph, cycles and change impact")
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--changed", nargs="*", default=None, help="changed files: print the tests that depend on them")
    parser.add_argument("--mermaid", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    files = project_files(Path(args.path))
    if args.changed is not None:
        result = impact(dependency_graph(files)["edges"], [c.replace("\\", "/") for c in args.changed])
        print(json.dumps(result, indent=2) if args.json else "\n".join(result["tests"] + [f"# unmapped: {t}" for t in result["unmapped_tests"]]))
        return 0
    report = summary(files)
    if args.mermaid:
        print(report["mermaid"])
    elif args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['modules']} modules ({report['test_modules']} tests), {report['edges']} imports, {len(report['cycles'])} cycle(s)")
        for cycle in report["cycles"]:
            print("  cycle: " + " -> ".join(cycle))
        for item in report["most_depended_on"][:5]:
            print(f"  depended on by {item['dependents']:3}: {item['module']}")
    return 1 if report["cycles"] and "--fail-on-cycles" in (argv or sys.argv) else 0


if __name__ == "__main__":
    sys.exit(main())
