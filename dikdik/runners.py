"""Which test suites and declared checks a project has. They run on this computer (`local_exec`)."""

import json
import re
from pathlib import PurePosixPath


def detect_runners(files: dict[str, str]) -> list[dict]:
    """Which test suites this candidate has, from its manifests and file names."""
    runners = []
    names = {PurePosixPath(path).name for path in files}
    if any(PurePosixPath(p).name.startswith("test") and p.endswith(".py") for p in files):
        runners.append({"kind": "unittest", "label": "Python unittest"})
    if "package.json" in files:
        try:
            scripts = json.loads(files["package.json"]).get("scripts", {})
        except (ValueError, AttributeError):
            scripts = {}
        test_script = scripts.get("test", "") if isinstance(scripts, dict) else ""
        bun = bool({"bun.lock", "bun.lockb", "bunfig.toml"} & names) or "bun test" in str(test_script)
        if bun:
            runners.append({"kind": "bun_test", "label": "bun test", "argv": ["bun", "test"]})
        elif test_script:
            runners.append({"kind": "npm_test", "label": "npm test", "argv": ["npm", "test", "--silent"]})
    if "Cargo.toml" in files:
        runners.append({"kind": "cargo_test", "label": "cargo test (offline)",
                        "argv": ["cargo", "test", "--offline"], "timeout": 300})
    return runners


def detect_local_checks(files: dict[str, str], *, include_static=False) -> list[dict]:
    """Declared tests first; otherwise use named static checks without calling them tests.

    Monorepos may declare scripts only in nested packages. Root scripts own their
    workspace orchestration and are not duplicated by running every child again.
    """
    manifests, managers = {}, {}
    for path, content in files.items():
        if PurePosixPath(path).name != "package.json":
            continue
        try:
            document = json.loads(content)
            scripts = document.get("scripts", {})
            if isinstance(scripts, dict):
                manifests[path] = {key: value for key, value in scripts.items() if isinstance(value, str) and value.strip()}
                manager = str(document.get("packageManager", "")).split("@", 1)[0]
                if manager in {"npm", "pnpm", "bun", "yarn"}:
                    managers[path] = manager
        except (ValueError, AttributeError):
            pass

    def js_check(path, script, kind):
        directory = PurePosixPath(path).parent
        roots = [directory, *directory.parents]
        manager = managers.get(path, managers.get("package.json", "npm"))
        if "bun test" in manifests[path].get("test", ""):
            manager = "bun"
        for root in roots:
            if any(str(root / lock) in files for lock in ("pnpm-lock.yaml", "pnpm-workspace.yaml")):
                manager = "pnpm"
                break
            if any(str(root / lock) in files for lock in ("bun.lock", "bun.lockb", "bunfig.toml")):
                manager = "bun"
                break
            if str(root / "yarn.lock") in files:
                manager = "yarn"
                break
        cwd = "" if str(directory) == "." else str(directory)
        requirements = []
        targets = list(manifests) if path == "package.json" and re.search(r"(?:\s-r\b|--recursive\b|workspaces\b)", manifests[path][script]) else [path]
        for target in targets:
            value = manifests[target].get(script, "")
            match = re.match(r"\s*(tsc|eslint|biome|vite|next)(?:\s|$)", value)
            if match:
                target_dir = str(PurePosixPath(target).parent)
                requirements.append({"tool": match[1], "cwd": "" if target_dir == "." else target_dir})
        return {"kind": kind, "label": f"{manager} run {script}" + (f" ({cwd})" if cwd else ""),
                "command": f"{manager} run {script}", "cwd": cwd, "timeout": 300,
                "requires": requirements if kind in {"typecheck", "lint", "build"} else []}

    tests = [r for r in detect_runners(files) if r["kind"] not in {"npm_test", "bun_test"}]
    test_manifests = [p for p, scripts in manifests.items() if scripts.get("test")]
    if "package.json" in test_manifests:
        test_manifests = ["package.json"]
    tests.extend(js_check(path, "test", "npm_test") for path in sorted(test_manifests))
    if tests and not include_static:
        return tests
    static = []
    seen_kinds = set()
    for script, kind in (("typecheck", "typecheck"), ("type-check", "typecheck"), ("check:types", "typecheck"), ("lint", "lint"), ("build", "build")):
        paths = [p for p, scripts in manifests.items() if scripts.get(script)]
        if "package.json" in paths:
            paths = ["package.json"]
        if paths:
            if not include_static:
                return [js_check(path, script, kind) for path in sorted(paths)]
            if kind not in seen_kinds:
                static.extend(js_check(path, script, kind) for path in sorted(paths))
                seen_kinds.add(kind)
    if tests or static:
        return tests + static
    # Keep established standalone Bun discovery when no declared script/check exists.
    return [r for r in detect_runners(files) if r["kind"] == "bun_test"]
