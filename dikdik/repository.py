"""Bounded text snapshots. Repository content is data, never instructions."""

import ast
import codecs
import fnmatch
import hashlib
import os
import unicodedata
from pathlib import Path, PurePosixPath


EXCLUDED = {"node_modules", "__pycache__", "dist", "build", "vendor", "venv"}
MAX_TEXT_FILE_BYTES = 256_000
MAX_PREVIEW_BYTES = 4_000_000
TEXT = {"." + ext for ext in """
py pyi pyw ts tsx mts cts js jsx mjs cjs json jsonc jsonl toml md markdown mdx txt rst adoc asciidoc
yaml yml css scss sass less styl html htm xhtml xml xsl xslt xsd svg vue svelte astro rs lock
go mod sum java kt kts scala sc cs csx fs fsx fsi vb vbs swift m mm h c cc cpp cxx hpp hxx hh ino
rb erb rake gemspec php php3 php4 php5 phtml blade pl pm pod r rmd jl lua nim nims zig dart
sh bash zsh fish ksh csh ps1 psm1 psd1 bat cmd sql pgsql sqlite graphql gql proto thrift
ex exs erl hrl clj cljs cljc edn el lisp lsp scm ss rkt hs lhs ml mli re rei elm purs p6 raku
pas pp d di f for f77 f90 f95 f03 f08 cob cbl ada adb ads v sv svh vhd vhdl asm s inc
cmake mk mak gradle groovy gvy gsp properties ini cfg conf cnf hcl tf tfvars nix dhall
tex latex bib sty cls typ org wikitext mediawiki diff patch feature cucumber robot
awk sed ahk au3 applescript osa scpt coffee litcoffee sol move vy wgsl glsl vert frag geom tesc tese comp
cl cu cuh metal vala vapi hx haxe hack hh pug jade haml slim mustache hbs handlebars liquid
q qml pro pri prf vim vimrc tcl tk tsv csv log abnf as asc apib avdl bnf capnp ceylon cr dts
ebnf gcode gms gss http hlsl hy icl ics iml j2 jinja jinja2 jison ld lds lean ll lsl mat
mo monkey n ncl odin pde pony prg ps rib roboconf rq sparql st step stp tap ttl turtle wat
""".split()}
TEXT_NAMES = {"dockerfile", "containerfile", "makefile", "gnumakefile", "justfile", "jenkinsfile", "gemfile", "rakefile", "procfile", "vagrantfile", "brewfile", "license", "licence", "readme", "authors", "copying"}


def is_text_file(path: Path | PurePosixPath) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in TEXT or name in TEXT_NAMES or name.startswith(("dockerfile.", "containerfile."))

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
# Old installed native binaries used a narrower set of file types. Do not let
# an otherwise healthy accelerator silently hide new languages or ignore rules.
if _rust_index is not None and getattr(_rust_index, "TEXT_POLICY_VERSION", 0) != 2:
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
    for filename in (".gitignore", ".dikdikignore"):
        ignore = directory / filename
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
    if path.is_absolute() or path.as_posix() != value or len(path.parts) > 41 or not is_text_file(path):
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


def _snapshot_limits(files: dict[str, str], *, max_file_bytes=256000, max_total_bytes=8_000_000, max_files=1000) -> None:
    if len(files) > max_files:
        raise ValueError(f"Snapshot exceeds {max_files} text files")
    total = 0
    for text in files.values():
        if not isinstance(text, str) or "\x00" in text:
            raise ValueError("File content must be UTF-8 text without NUL bytes")
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            raise ValueError("File content must be valid UTF-8 text") from None
        if size > max_file_bytes:
            raise ValueError(f"File exceeds {max_file_bytes // 1000}KB buffered-edit budget")
        total += size
    if total > max_total_bytes:
        raise ValueError(f"Snapshot exceeds {max_total_bytes // 1_000_000}MB buffered-edit budget")


def create_file(files: dict[str, str], path: str, content: str, *, limits: dict | None = None) -> dict[str, str]:
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
    _snapshot_limits(result, **(limits or {}))
    return result


def inventory(root: Path) -> dict[str, str]:
    """Bounded initial cache; deferred files remain available to workspace tools. Ignores are conservative."""
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


def _inventory_python(root: Path, large_files: list[dict] | None = None) -> dict[str, str]:
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
                elif entry.is_file(follow_symlinks=False) and is_text_file(path):
                    size = entry.stat(follow_symlinks=False).st_size
                    if size > MAX_TEXT_FILE_BYTES:
                        if large_files is not None:
                            large_files.append({"path": rel, "bytes": size})
                        continue
                    with path.open("rb") as source:
                        raw = source.read(MAX_TEXT_FILE_BYTES + 1)
                    if len(raw) > MAX_TEXT_FILE_BYTES:
                        if large_files is not None:
                            large_files.append({"path": rel, "bytes": path.stat().st_size})
                        continue
                    if total + len(raw) > 8_000_000 or len(files) >= 1000:
                        if large_files is not None:
                            large_files.append({"path": rel, "bytes": len(raw)})
                        continue
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    if "\x00" not in text:
                        files[rel] = text
                        total += len(raw)

    walk(root, [])
    return dict(sorted(files.items()))


def metadata(files: dict[str, str]) -> dict:
    return {
        "files": [{"path": p, "bytes": len(t.encode()), "sha256": hashlib.sha256(t.encode()).hexdigest()} for p, t in files.items()],
        "python_tests": [p for p in files if PurePosixPath(p).name.startswith("test") and p.endswith(".py")],
        "manifests": [p for p in files if PurePosixPath(p).name in {"pyproject.toml", "package.json", "Cargo.toml", "requirements.txt"}],
        "limitations": "Bounded initial UTF-8 cache; agent tools can discover, read and search additional workspace files on demand, including large files. Conservative gitignore subset; hidden, binary and credential-like paths excluded. No semantic index.",
    }


def workspace_metadata(root: Path) -> dict:
    """Explorer inventory includes large assets without loading them into model context."""
    large_files: list[dict] = []
    files = _inventory_python(root, large_files)
    return {**metadata(files), "large_files": sorted(large_files, key=lambda entry: entry["path"])}


def workspace_text(root: Path, path: str) -> dict:
    """Read-only preview under the same ignore/secret/link policy as inventory."""
    if not isinstance(path, str) or not path:
        raise KeyError(path)
    large_files: list[dict] = []
    files = _inventory_python(root, large_files)
    if path in files:
        return {"path": path, "version": "workspace", "text": files[path], "indexed": True, "truncated": False}
    if not any(entry["path"] == path for entry in large_files):
        raise KeyError(path)
    target = root / path
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise KeyError(path)
    if target.resolve().relative_to(root.resolve()).as_posix() != path:
        raise KeyError(path)
    with target.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        raw = source.read(MAX_PREVIEW_BYTES + 1)
    truncated = len(raw) > MAX_PREVIEW_BYTES
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(raw[:MAX_PREVIEW_BYTES], final=not truncated)
    except UnicodeDecodeError:
        raise ValueError("This file is not UTF-8 text. Open it in VS Code to view it.") from None
    if "\x00" in text:
        raise ValueError("This file contains binary data. Open it in VS Code to view it.")
    return {"path": path, "version": "workspace", "text": text, "bytes": size, "indexed": False, "truncated": truncated}


def is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    filename = PurePosixPath(path).name.lower()
    return any(p.lower() in {"tests", "test", "__tests__"} for p in parts) or filename.startswith("test") or filename == "conftest.py" or ".test." in filename or ".spec." in filename


def apply_edits(files: dict[str, str], edits: list, *, protected_paths: set[str] | None = None, limits: dict | None = None) -> dict[str, str]:
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
        _snapshot_limits(result, **(limits or {}))
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
