"""Statistical analysis tool for the agent — deep-research and data work in the workspace.

Bounded, dependency-free: reads a table (CSV/TSV/JSON/XLSX), profiles numeric
columns, aggregates by a category and computes Pearson correlations. The result
is a structured dict the model reasons over and reports in its own words.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from xml.etree import ElementTree

from .docextract import DOC_LIMITS, _SPREADSHEET, _col_index

ANALYZE_LIMITS = {"rows": 50_000, "columns": 60, "file_bytes": 12 * 1024 * 1024, "sheet_rows": 50_000}

ANALYZE_TOOL = {
    "type": "function",
    "function": {
        "name": "analyze_data",
        "description": (
            "Run bounded statistics on a table in the workspace (CSV, TSV, JSON array of objects or an "
            "XLSX workbook sheet). Returns per-column profiles (count, missing, mean, median, standard "
            "deviation, min, max, quartiles), optional group-by aggregation (mean, count, sum per group), "
            "and Pearson correlations between numeric columns. Read-only; cannot edit files."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path of the data file."},
                "sheet": {"type": "string", "description": "For XLSX: the sheet name (default: first sheet)."},
                "columns": {"type": "array", "items": {"type": "string"},
                            "description": "Optional subset of columns to profile."},
                "group_by": {"type": "string", "description": "Optional categorical column to aggregate by."},
                "correlations": {"type": "boolean", "description": "Include a correlation matrix (default true)."},
            },
            "required": ["path"],
        },
    },
}


def _numbers(values: list[float]) -> dict:
    data = sorted(values)
    count = len(data)
    if not count:
        return {"count": 0, "missing": 0}
    mean = sum(data) / count
    variance = sum((value - mean) ** 2 for value in data) / count
    def at(fraction: float) -> float:
        position = (count - 1) * fraction
        low, high = math.floor(position), math.ceil(position)
        return data[low] + (data[high] - data[low]) * (position - low) if low != high else data[low]
    rounded = {key: round(value, 6) for key, value in {
        "mean": mean, "std": math.sqrt(variance), "min": data[0], "max": data[-1],
        "p25": at(0.25), "median": at(0.5), "p75": at(0.75)}.items()}
    return {"count": count, "missing": 0, **rounded}


def _pearson(a: list[float], b: list[float]) -> float | None:
    count = min(len(a), len(b))
    if count < 3:
        return None
    a, b = a[:count], b[:count]
    mean_a, mean_b = sum(a) / count, sum(b) / count
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0 or var_b <= 0:
        return None
    return round(cov / math.sqrt(var_a * var_b), 4)


def _rows_from_csv(path: Path, delimiter: str) -> list[list[str]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return [row[: ANALYZE_LIMITS["columns"]] for _, row in zip(range(ANALYZE_LIMITS["rows"] + 1),
                csv.reader(handle, delimiter=delimiter))]


def _rows_from_json(path: Path) -> list[list[str]]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(data, list) or not data or not all(isinstance(item, dict) for item in data):
        raise ValueError("JSON data must be an array of objects")
    names: list[str] = []
    for item in data[:100]:
        for key in item:
            if key not in names:
                names.append(key)
    names = names[: ANALYZE_LIMITS["columns"]]
    rows = [names] + [[_cell(item.get(name)) for name in names] for item in data[: ANALYZE_LIMITS["rows"]]]
    return rows


def _rows_from_xlsx(path: Path, sheet_name: str) -> list[list[str]]:
    import zipfile
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.iter(f"{_SPREADSHEET}t")) for item in root]
        wanted = None
        try:
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            order = [node.get("name") for node in workbook.iter(f"{_SPREADSHEET}sheet")]
            wanted = sheet_name or (order[0] if order else None)
            if sheet_name and sheet_name not in order:
                raise ValueError(f"No sheet named {sheet_name}")
            index = order.index(wanted) if wanted in order else 0
        except (KeyError, ElementTree.ParseError):
            index = 0
        parts = sorted((name for name in archive.namelist()
                        if name.startswith("xl/worksheets/") and name.endswith(".xml")))
        if index >= len(parts):
            raise ValueError("That sheet does not exist")
        root = ElementTree.fromstring(archive.read(parts[index]))
        rows: list[list[str]] = []
        for row in root.iter(f"{_SPREADSHEET}row"):
            cells: dict[int, str] = {}
            for cell in row.findall(f"{_SPREADSHEET}c"):
                kind = cell.get("t", "")
                if kind == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{_SPREADSHEET}t"))
                else:
                    node = cell.find(f"{_SPREADSHEET}v")
                    raw = (node.text or "").strip() if node is not None else ""
                    if kind == "s" and raw:
                        try:
                            value = shared[int(raw)]
                        except (ValueError, IndexError):
                            value = raw
                    else:
                        value = raw
                cells[_col_index(cell.get("r") or "")] = value
            width = min(max(cells, default=-1) + 1, ANALYZE_LIMITS["columns"])
            rows.append([cells.get(column, "") for column in range(width)])
            if len(rows) >= ANALYZE_LIMITS["sheet_rows"]:
                break
        return rows


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def analyze(root: Path, arguments: dict) -> dict:
    """The analyze_data tool body. `root` is the workspace; paths stay inside it."""
    from .workspace_files import WorkspaceFiles
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("Choose a data file to analyze")
    target = WorkspaceFiles(root).target(path)
    if not target.is_file():
        raise ValueError("That file does not exist")
    if target.stat().st_size > ANALYZE_LIMITS["file_bytes"]:
        raise ValueError("Data file is too large for bounded analysis (12 MB limit)")
    suffix = target.suffix.lower()
    if suffix in {".csv"}:
        rows = _rows_from_csv(target, ",")
    elif suffix == ".tsv":
        rows = _rows_from_csv(target, "\t")
    elif suffix in {".json"}:
        rows = _rows_from_json(target)
    elif suffix in {".xlsx", ".xlsm"}:
        rows = _rows_from_xlsx(target, arguments.get("sheet") or "")
    else:
        raise ValueError("analyze_data reads CSV, TSV, JSON (array of objects) or XLSX files")
    if not rows:
        return {"path": path, "rows": 0, "columns": []}
    header = [name or f"column_{index + 1}" for index, name in enumerate(rows[0])]
    body = rows[1:]
    wanted = arguments.get("columns")
    if isinstance(wanted, list) and wanted:
        keep = [name for name in header if name in wanted]
        if keep:
            indices = [header.index(name) for name in keep]
            header = keep
            body = [[row[i] if i < len(row) else "" for i in indices] for row in body]
    numeric: dict[str, list[float]] = {}
    missing: dict[str, int] = {}
    for index, name in enumerate(header):
        values, gaps = [], 0
        for row in body[: ANALYZE_LIMITS["rows"]]:
            text = row[index].strip() if index < len(row) else ""
            if not text:
                gaps += 1
                continue
            try:
                values.append(float(text.replace(",", "")))
            except ValueError:
                pass
        missing[name] = gaps
        if values and len(values) >= max(3, len(body[: ANALYZE_LIMITS["rows"]]) // 20):
            numeric[name] = values
    profiles = {}
    for name, values in numeric.items():
        profiles[name] = {**_numbers(values), "missing": missing[name],
                          "numeric_share": round(len(values) / max(len(body), 1), 3)}
    for name in header:
        if name not in profiles:
            distinct = {row[header.index(name)] for row in body[:2000] if len(row) > header.index(name) and row[header.index(name)]}
            profiles[name] = {"type": "text", "count": len(body), "missing": missing[name],
                              "distinct_in_first_2000": min(len(distinct), 200)}
    result: dict = {"path": path, "rows": len(body), "columns": header,
                    "profiles": profiles, "limitations": "Descriptive statistics on a bounded sample (50,000 rows)."}
    group_name = arguments.get("group_by")
    if isinstance(group_name, str) and group_name in header:
        column = header.index(group_name)
        numeric_columns = [name for name in header if name in numeric]
        groups: dict[str, dict[str, list[float]]] = {}
        counts: dict[str, int] = {}
        for row in body[: ANALYZE_LIMITS["rows"]]:
            key = row[column].strip() if column < len(row) and row[column].strip() else "(blank)"
            if key not in groups:
                groups[key] = {name: [] for name in numeric_columns}
            counts[key] = counts.get(key, 0) + 1
            for name in numeric_columns:
                text = row[header.index(name)].strip() if header.index(name) < len(row) else ""
                try:
                    groups[key][name].append(float(text.replace(",", "")))
                except ValueError:
                    pass
        top = sorted(groups, key=lambda key: -counts[key])[:30]
        result["group_by"] = {
            "column": group_name,
            "groups": [{"value": key, "count": counts[key],
                        **{name: round(sum(values) / len(values), 6) if values else None
                           for name, values in groups[key].items()}} for key in top],
        }
    if arguments.get("correlations") is not False and len(numeric) >= 2:
        names = list(numeric)
        matrix = {}
        for a in names:
            matrix[a] = {b: _pearson(numeric[a], numeric[b]) for b in names}
        result["correlations"] = matrix
    return result
