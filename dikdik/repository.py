"""Bounded text snapshots. Repository content is data, never instructions."""

import ast
import fnmatch
import hashlib
import os
import unicodedata
from pathlib import Path, PurePosixPath


EXCLUDED = {"node_modules", "__pycache__", "dist", "build", "vendor", "venv"}
TEXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".toml", ".md", ".txt", ".yaml", ".yml", ".css", ".html", ".rs", ".lock"}

# Optional Rust accelerator (crates/dikdik-index). If the `dikdik_index`
# extension is importable, `inventory()` uses it and falls back to the
# pure-Python walk on any exception. The Python path remains canonical:
# the Rust output is validated through the same `_snapshot_limits` and
# NUL/sort invariants, so a semantic mismatch raises rather than silently
# returning a different snapshot.
# `scripts/build_native.py` places the build inside this package.
try:
    from . import dikdik_index as _rust_index  # type: ignore[attr-defined]
except ImportError:
    try:
        import dikdik_index as _rust_index  # type: ignore[import-not-found]
    except ImportError:
        _rust_index = None
INDEXER = "rust" if _rust_index is not None else "python"
SECRET = ("*secret*", "*credential*", "*.pem", "*.key", "*.p12", "*.pfx", "*id_rsa*", "*id_ed25519*", "*.env*", "*token*")
RESERVED_NAMES = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)}


def approved_root(value: str) -> Path:
    original = Path(value).absolute()
    for part in (original, *original.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("Workspace ancestors must not be symlinks or junctions")
    root = original.resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor):
        raise ValueError("Approve a project directory, not a drive root")
    return root


def _ignore_rules(directory: Path, root: Path, inherited: list[tuple[str, str]]) -> list[tuple[str, str]]:
    rules = list(inherited)
    ignore = directory / ".gitignore"
    if ignore.is_file() and not ignore.is_symlink() and ignore.stat().st_size <= 65536:
        base = directory.relative_to(root).as_posix()
        for line in ignore.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "!")):
                rules.append(("" if base == "." else base + "/", line.strip("/")))
    return rules


def _ignored(relative: str, rules: list[tuple[str, str]]) -> bool:
    return any(relative.startswith(base) and (fnmatch.fnmatch(relative[len(base):], pattern) or any(fnmatch.fnmatch(part, pattern) for part in relative[len(base):].split("/"))) for base, pattern in rules)


def _new_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise ValueError("New file path must contain 1–1024 UTF-8 bytes")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or len(path.parts) > 41 or path.suffix.lower() not in TEXT:
        raise ValueError("New file requires a canonical relative supported text path")
    for part in path.parts:
        normalized = unicodedata.normalize("NFKC", part).lower()
        if (part in {".", ".."} or normalized.startswith(".") or normalized in EXCLUDED
                or part.endswith((".", " ")) or len(part.encode("utf-8")) > 255
                or any(not char.isprintable() or char in '\\:<>"|?*~' for char in part)
                or normalized.split(".")[0].rstrip(" .") in RESERVED_NAMES
                or any(fnmatch.fnmatch(normalized, pattern) for pattern in SECRET)):
            raise ValueError("New file path is reserved, excluded or unsafe")
    return path


def can_create_file(root: Path, value: str) -> bool:
    """Read-only project policy; refuse ignored or existing unindexed paths."""
    try:
        path = _new_path(value)
        root = approved_root(str(root))
        directory = root
        rules: list[tuple[str, str]] = []
        for index, part in enumerate(path.parts):
            rules = _ignore_rules(directory, root, rules)
            if _ignored("/".join(path.parts[:index + 1]), rules):
                return False
            target = directory / part
            if target.is_symlink() or target.is_junction():
                return False
            if index == len(path.parts) - 1:
                return not os.path.lexists(target)
            if os.path.lexists(target) and not target.is_dir():
                return False
            if target.exists() and target.resolve(strict=True).relative_to(root).as_posix() != "/".join(path.parts[:index + 1]):
                return False
            directory = target
    except (OSError, ValueError):
        return False
    return False


def _snapshot_limits(files: dict[str, str]) -> None:
    if len(files) > 1000:
        raise ValueError("Snapshot exceeds 1000 text files")
    total = 0
    for text in files.values():
        if not isinstance(text, str) or "\x00" in text:
            raise ValueError("File content must be UTF-8 text without NUL bytes")
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            raise ValueError("File content must be valid UTF-8 text") from None
        if size > 256000:
            raise ValueError("File exceeds 256KB")
        total += size
    if total > 8_000_000:
        raise ValueError("Snapshot exceeds 8MB")


def create_file(files: dict[str, str], path: str, content: str) -> dict[str, str]:
    _new_path(path)
    folded = path.casefold()
    if any(folded == existing.casefold() or folded.startswith(existing.casefold() + "/") or existing.casefold().startswith(folded + "/") for existing in files):
        raise ValueError("New file would overwrite a file or collide with a directory")
    parents = path.split("/")[:-1]
    for existing in files:
        existing_parents = existing.split("/")[:-1]
        for depth in range(1, min(len(parents), len(existing_parents)) + 1):
            current = "/".join(parents[:depth])
            previous = "/".join(existing_parents[:depth])
            if current.casefold() == previous.casefold() and current != previous:
                raise ValueError("New file uses an ambiguous directory-case alias")
    result = {**files, path: content}
    _snapshot_limits(result)
    return result


def inventory(root: Path) -> dict[str, str]:
    """Conservative ignores: negations never re-include excluded content."""
    if _rust_index is not None:
        try:
            return _inventory_rust(root)
        except Exception:
            # Any Rust-side failure (panic surfaced as RuntimeError, ABI
            # mismatch, semantic divergence) falls back to the canonical
            # Python walk. Correctness is never silently degraded.
            pass
    return _inventory_python(root)


def _inventory_rust(root: Path) -> dict[str, str]:
    entries = _rust_index.scan(str(root), 1000, 256000)
    files: dict[str, str] = {}
    for entry in entries:
        path = entry["path"]
        text = entry.get("text")
        if not isinstance(path, str) or not isinstance(text, str) or "\x00" in text:
            # The Rust side already filters these; re-check so a divergence
            # cannot smuggle unsafe content past the canonical invariants.
            raise ValueError(f"Rust inventory returned an unusable entry: {path!r}")
        files[path] = text
    _snapshot_limits(files)
    return dict(sorted(files.items()))


def _inventory_python(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    total = 0
    visited = 0

    def walk(directory: Path, inherited: list[tuple[str, str]], depth: int = 0) -> None:
        nonlocal total, visited
        if depth > 40:
            raise ValueError("Repository nesting exceeds 40 directories")
        rules = _ignore_rules(directory, root, inherited)
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > 10000:
                    raise ValueError("Repository exceeds 10,000 inspected entries; approve a smaller project")
                path = Path(entry.path)
                name = entry.name
                if any(ord(c) < 32 or c in "\\:" for c in name):
                    continue
                rel = path.relative_to(root).as_posix()
                if name.startswith(".") or name in EXCLUDED or any(fnmatch.fnmatch(name.lower(), p) for p in SECRET):
                    continue
                if entry.is_symlink() or path.is_junction():
                    continue
                if _ignored(rel, rules):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    walk(path, rules, depth + 1)
                elif entry.is_file(follow_symlinks=False) and path.suffix.lower() in TEXT:
                    size = entry.stat(follow_symlinks=False).st_size
                    if size > 256000:
                        raise ValueError(f"Text file exceeds 256KB: {rel}")
                    with path.open("rb") as source:
                        raw = source.read(256001)
                    if len(raw) > 256000:
                        raise ValueError(f"Text file grew beyond size limit: {rel}")
                    total += len(raw)
                    if total > 8_000_000 or len(files) >= 1000:
                        raise ValueError("Snapshot exceeds 8MB or 1000 text files")
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    if "\x00" not in text:
                        files[rel] = text

    walk(root, [])
    return dict(sorted(files.items()))


def metadata(files: dict[str, str]) -> dict:
    return {
        "files": [{"path": p, "bytes": len(t.encode()), "sha256": hashlib.sha256(t.encode()).hexdigest()} for p, t in files.items()],
        "python_tests": [p for p in files if PurePosixPath(p).name.startswith("test") and p.endswith(".py")],
        "manifests": [p for p in files if PurePosixPath(p).name in {"pyproject.toml", "package.json", "Cargo.toml", "requirements.txt"}],
        "limitations": "Bounded UTF-8 text snapshot; conservative gitignore subset; hidden, binary and credential-like paths excluded. No semantic index.",
    }


def is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    filename = PurePosixPath(path).name.lower()
    return any(p.lower() in {"tests", "test", "__tests__"} for p in parts) or filename.startswith("test") or filename == "conftest.py" or ".test." in filename or ".spec." in filename


def apply_edits(files: dict[str, str], edits: list, *, protected_paths: set[str] | None = None) -> dict[str, str]:
    if not isinstance(edits, list) or not 1 <= len(edits) <= 20:
        raise ValueError("Provide 1–20 exact replacements")
    result = dict(files)
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "before", "after"} or not all(isinstance(v, str) for v in edit.values()):
            raise ValueError("Each edit requires string path, before and after")
        path, before, after = edit["path"], edit["before"], edit["after"]
        if path not in result or "\\" in path or ".." in PurePosixPath(path).parts:
            raise ValueError("Edits must address inventoried files")
        if is_test_path(path) and (protected_paths is None or path in protected_paths):
            raise ValueError("Existing tests are protected in this milestone")
        if not before or before == after or result[path].count(before) != 1:
            raise ValueError(f"Replacement must match exactly once and change content: {path}")
        result[path] = result[path].replace(before, after, 1)
        _snapshot_limits(result)
    return result


def syntax_check(files: dict[str, str]) -> dict:
    errors = []
    count = 0
    for path, text in files.items():
        if path.endswith(".py"):
            count += 1
            try:
                ast.parse(text.encode("utf-8"), filename=path)
            except (SyntaxError, ValueError, RecursionError) as exc:
                errors.append(f"{path}: {exc}")
    return {"kind": "python_syntax", "status": "failed" if errors else "passed" if count else "not_run", "files_checked": count, "errors": errors, "note": "Parsing only; no repository code executed. Not a test-suite result."}
