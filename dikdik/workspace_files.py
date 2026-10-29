"""On-demand workspace tools: file size is independent of model observation size."""
import fnmatch
import io
import json
import time
from pathlib import Path, PurePosixPath

from .repository import EXCLUDED, SECRET, _ignore_rules, _ignored, is_text_file, workspace_metadata

WORKING_LIMITS = {"max_file_bytes": 64_000_000, "max_total_bytes": 64_000_000, "max_files": 10000}


def _stat(target):
    info = target.stat()
    return info.st_size, info.st_mtime_ns, info.st_ino


def read_page(source, path, *, start_line=1, start_column=1, max_lines=200, max_chars=16000, cancel=None):
    """Page by line and column, including minified lines, without unbounded readline."""
    rows, chars, encoded, number, column = [], 0, 0, 1, 1
    deadline = time.monotonic() + 20
    next_position = None
    while True:
        if cancel is not None and cancel.is_set():
            raise ValueError("Read cancelled")
        if time.monotonic() > deadline:
            next_position = (number, column)
            break
        piece = source.readline(4096)
        if not piece:
            break
        if "\0" in piece:
            raise ValueError("This file contains binary data")
        ends_line = piece.endswith("\n")
        body = piece[:-1] if ends_line else piece
        begin = max(0, start_column - column) if number == start_line else 0
        if number >= start_line:
            if number >= start_line + max_lines:
                next_position = (number, column)
                break
            value = body[begin:]
            if value or ends_line:
                room = min(len(value), max_chars - chars)
                lo, hi = 0, max(0, room)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if encoded + len(json.dumps(value[:mid], ensure_ascii=True)) <= 48000:
                        lo = mid
                    else:
                        hi = mid - 1
                selected = value[:lo]
                if rows and rows[-1][0] == number:
                    rows[-1][1] += selected
                else:
                    rows.append([number, selected])
                chars += len(selected)
                encoded += len(json.dumps(selected, ensure_ascii=True)) + 24
                if lo < len(value):
                    next_position = (number, column + begin + lo)
                    break
        if ends_line:
            number += 1
            column = 1
        else:
            column += len(piece)
    return {"path": path, "start_line": start_line, "start_column": start_column,
            "lines": [row[1].removesuffix("\r") for row in rows], "line_numbers": [row[0] for row in rows],
            "total_lines": None if next_position else number - (1 if column == 1 and number > 1 else 0),
            "truncated": next_position is not None,
            **({"next_start_line": next_position[0], "next_start_column": next_position[1],
                "note": "Continue read_file with next_start_line and next_start_column; no file content is excluded."} if next_position else {})}


class WorkspaceFiles:
    def __init__(self, root, cancel=None, on_adopt=None):
        self.root = Path(root).resolve()
        self.cancel, self.on_adopt = cancel, on_adopt
        self.observed = {}
        self.originals = {}

    def target(self, path):
        if not isinstance(path, str) or not path or len(path) > 1024:
            raise ValueError("Choose a workspace file")
        relative = PurePosixPath(path)
        if relative.is_absolute() or relative.as_posix() != path or ".." in relative.parts or "\\" in path:
            raise ValueError("Path must stay inside the workspace")
        current, rules = self.root, []
        for index, name in enumerate(relative.parts):
            if name.startswith(".") or name in EXCLUDED or any(fnmatch.fnmatch(name.lower(), pattern) for pattern in SECRET) or any(ord(c) < 32 or c in "\\:" for c in name):
                raise ValueError("Path is excluded by workspace policy")
            rules = _ignore_rules(current, self.root, rules)
            if _ignored("/".join(relative.parts[:index + 1]), rules):
                raise ValueError("Path is ignored by workspace policy")
            current = current / name
            if current.is_symlink() or current.is_junction():
                raise ValueError("Workspace tools do not follow links")
        if not current.exists():
            raise ValueError("File not found. Use list_files with the containing folder path, then read an exact returned file path.")
        if current.is_dir():
            raise ValueError("This path is a directory. Use list_files with this folder path to discover its files.")
        if not current.is_file() or not is_text_file(relative) or current.resolve().relative_to(self.root).as_posix() != path:
            raise ValueError("Choose a supported workspace text file")
        return current

    def paths(self):
        data = workspace_metadata(self.root)
        return sorted({row["path"] for row in [*data["files"], *data["large_files"]]})

    def read(self, path, candidate, **options):
        if path in candidate:
            return read_page(io.StringIO(candidate[path]), path, cancel=self.cancel, **options)
        target = self.target(path)
        before = _stat(target)
        try:
            with target.open("r", encoding="utf-8", newline="") as source:
                result = read_page(source, path, cancel=self.cancel, **options)
        except UnicodeError:
            raise ValueError("This file is not UTF-8 text") from None
        if before != _stat(target):
            raise ValueError("File changed while reading it; read it again")
        self.observed[path] = before
        return result

    def load_for_edit(self, path, expected=None, check_observed=True):
        target = self.target(path)
        before = _stat(target)
        if check_observed and path in self.observed and self.observed[path] != before:
            raise ValueError("File changed since it was read; read it again before editing")
        with target.open("rb") as source:
            raw = source.read(WORKING_LIMITS["max_file_bytes"] + 1)
        if len(raw) > WORKING_LIMITS["max_file_bytes"]:
            raise ValueError("This file exceeds the 64MB buffered-edit budget. Use an approved streaming command for this edit; paged reads and searches remain available.")
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ValueError("This file is not UTF-8 text") from None
        if "\0" in text or before != _stat(target):
            raise ValueError("File is binary or changed while loading; read it again")
        return text

    def adopt(self, path, original, expected=None):
        self.target(path)
        self.originals[path] = original
        if self.on_adopt:
            self.on_adopt(path, original, expected)

    def tracked_snapshot(self):
        return {path: self.load_for_edit(path, check_observed=False) for path in self.originals}

    def search(self, candidate, query, path="", offset=0, max_results=40):
        if not query or len(query) > 4096 or "\n" in query:
            raise ValueError("Search requires 1–4096 characters on one line")
        matches, seen, output_bytes = [], 0, 0
        deadline = time.monotonic() + 20
        for name in sorted(set(candidate) | set(self.paths())):
            if path and name != path and not name.startswith(path.rstrip("/") + "/"):
                continue
            try:
                source = io.StringIO(candidate[name]) if name in candidate else self.target(name).open("r", encoding="utf-8", newline="")
                with source:
                    number, column, carry, last = 1, 1, "", -1
                    while True:
                        if (self.cancel is not None and self.cancel.is_set()) or time.monotonic() > deadline:
                            return {"matches": matches, "truncated": True, "next_offset": seen, "note": "Search time budget reached; narrow path or continue with offset."}
                        piece = source.readline(4096)
                        if not piece:
                            break
                        if "\0" in piece:
                            break
                        text = carry + piece
                        base = column - len(carry)
                        position = text.find(query)
                        while position >= 0:
                            absolute = base + position
                            if absolute > last:
                                last = absolute
                                if seen >= offset:
                                    if len(matches) == max_results:
                                        return {"matches": matches, "truncated": True, "next_offset": seen}
                                    begin = max(0, position - 80)
                                    match = {"path": name, "line": number, "column": absolute, "text": text[begin:begin + 240].rstrip("\r\n")}
                                    length = len(json.dumps(match, ensure_ascii=True))
                                    if output_bytes + length > 48000:
                                        return {"matches": matches, "truncated": True, "next_offset": seen}
                                    matches.append(match)
                                    output_bytes += length
                                seen += 1
                            position = text.find(query, position + 1)
                        if piece.endswith("\n"):
                            number, column, carry, last = number + 1, 1, "", -1
                        else:
                            column += len(piece)
                            carry = text[-(len(query) - 1):] if len(query) > 1 else ""
            except (OSError, UnicodeError, ValueError):
                continue
        return {"matches": matches, "truncated": False}
