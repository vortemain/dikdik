"""Research statistics for the agent: survey, thesis and business data, stdlib only.

`run(root, arguments)` is the body of the `statistics` tool. It loads a table
(CSV/TSV/JSON/XLSX/ODS, or a table inside a .docx) from a folder the agent may
read, optionally filters rows, and runs one analysis:

  overview      every column: type, missing, distinct values, top values or summary
  frequencies   counts and valid percentages per category (Likert order kept)
  descriptives  n, mean, SD, SE, 95% CI, median, IQR, min/max, skewness, kurtosis
  likert        per-item distribution, mean, SD, agree/disagree share, verbal reading,
                plus the composite (scale) mean and Cronbach's alpha
  crosstab      contingency table, row/column %, chi-square, Cramér's V, Fisher (2×2)
  reliability   Cronbach's alpha, item-total correlations, alpha if item deleted
  compare       a numeric/ordinal outcome across groups: Welch t + Mann-Whitney U
                (2 groups) or one-way ANOVA + Kruskal-Wallis (3+), with effect sizes
  correlation   Pearson and Spearman matrices with p-values and n
  regression    OLS (linear) or binary logistic: coefficients, SE, t/z, p, 95% CI,
                odds ratios, R²/adjusted R²/F or pseudo-R², model chi-square

Columns are named exactly, by a unique prefix ("9." or "9"), or as "#12" (1-based
position). Ordinal answers like "4 = Agree", "Strongly agree", "Yes"/"No" are
coded to numbers automatically; the coding used is returned so it can be reported.
All p-values come from exact distribution functions (incomplete beta / gamma).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from . import docextract

LIMITS = {"rows": 200_000, "file_bytes": 48 * 1024 * 1024, "categories": 60, "columns": 60}

STATISTICS_TOOL = {
    "type": "function",
    "function": {
        "name": "statistics",
        "description": (
            "Run research-grade statistics on a data table (CSV, TSV, JSON array, XLSX/ODS sheet, or a table in a "
            "DOCX). Analyses: overview (start here: lists every column with its position, type and values), "
            "frequencies, descriptives, likert (item distributions, means, SD, agreement, composite score and "
            "Cronbach's alpha), crosstab (chi-square, Cramér's V, Fisher exact), reliability, compare (t-test/"
            "Mann-Whitney or ANOVA/Kruskal-Wallis with effect sizes), correlation (Pearson + Spearman with p), "
            "regression (linear OLS or binary logistic with odds ratios). Likert text such as '4 = Agree' or "
            "'Strongly agree' and Yes/No answers are coded automatically and the coding is returned. Refer to "
            "columns by exact name, a unique prefix such as '9.' , or '#12' for the 12th column. Use 'where' to "
            "keep only some rows (e.g. consenting respondents). Composite scores: pass several columns as one "
            "variable with {'mean_of': [...]} in place of a column name."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the data file (relative to the working folder)."},
                "sheet": {"type": "string", "description": "Sheet name for workbooks, or table number (1-based) inside a DOCX."},
                "analysis": {"type": "string", "enum": ["overview", "frequencies", "descriptives", "likert", "crosstab",
                                                         "reliability", "compare", "correlation", "regression"]},
                "columns": {"type": "array", "items": {}, "description": "Columns (or {'mean_of': [...], 'name': '...'} composites) to analyze."},
                "row": {"description": "crosstab: the row variable."},
                "column": {"description": "crosstab: the column variable."},
                "outcome": {"description": "compare/regression: the dependent variable (column or composite)."},
                "group": {"description": "compare: the grouping column."},
                "predictors": {"type": "array", "items": {}, "description": "regression: independent variables."},
                "model": {"type": "string", "enum": ["linear", "logistic"], "description": "regression type (default linear)."},
                "positive": {"type": "string", "description": "logistic: the outcome value counted as 1 (default: the less common value or 'Yes')."},
                "scale": {"type": "object", "description": "Optional explicit coding, e.g. {'Strongly disagree':1,...,'Strongly agree':5}."},
                "where": {"type": "array", "items": {"type": "object"},
                          "description": "Row filters: [{'column': '...', 'equals'|'not_equals'|'contains'|'in': value}]."},
                "header_row": {"type": "integer", "description": "1-based row holding the column names (default: detected)."},
            },
            "required": ["path", "analysis"],
        },
    },
}


# --- distributions -------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-300 else 1e-300
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def gammainc_upper(s: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(s, x)."""
    if x <= 0:
        return 1.0
    if x < s + 1:
        term = total = 1.0 / s
        n = s
        for _ in range(1000):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return max(0.0, 1.0 - total * math.exp(-x + s * math.log(x) - math.lgamma(s)))
    b = x + 1 - s
    c = 1 / 1e-300
    d = 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - s)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-14:
            break
    return min(1.0, math.exp(-x + s * math.log(x) - math.lgamma(s)) * h)


def p_t(t: float, df: float) -> float:
    """Two-sided p-value of Student's t."""
    if df <= 0 or math.isnan(t):
        return float("nan")
    return betainc(df / 2, 0.5, df / (df + t * t))


def p_chi2(x: float, df: float) -> float:
    return gammainc_upper(df / 2, x / 2) if df > 0 else float("nan")


def p_f(f: float, d1: float, d2: float) -> float:
    if f <= 0 or d1 <= 0 or d2 <= 0:
        return 1.0
    return betainc(d2 / 2, d1 / 2, d2 / (d2 + d1 * f))


def p_norm(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def t_critical(df: float, alpha: float = 0.05) -> float:
    low, high = 0.0, 50.0
    for _ in range(100):
        mid = (low + high) / 2
        if p_t(mid, df) > alpha:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _r(value, digits: int = 4):
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    return round(value, digits)


def _p(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(f"{value:.4g}") if value >= 0.0001 else float(f"{value:.2e}")


# --- coding of answers ------------------------------------------------------------

LIKERT_WORDS = [
    ({"strongly disagree", "very dissatisfied", "never", "very poor", "very low", "not at all", "very unlikely", "very bad"}, 1),
    ({"disagree", "dissatisfied", "rarely", "poor", "low", "slightly", "unlikely", "bad"}, 2),
    ({"neutral", "neither agree nor disagree", "undecided", "not sure", "unsure", "sometimes", "fair", "moderate",
      "moderately", "average", "neither"}, 3),
    ({"agree", "satisfied", "often", "good", "high", "very", "likely"}, 4),
    ({"strongly agree", "very satisfied", "always", "very good", "excellent", "very high", "extremely", "very likely"}, 5),
]
_CODED = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*(?:=|-|–|:|\.|\))\s*([^\W\d_].*)$")
_YES = {"yes", "y", "true", "agree", "present", "available"}
_NO = {"no", "n", "false", "absent", "not available"}
_DONT_KNOW = {"i don't know", "i dont know", "don't know", "dont know", "not sure", "unsure", "n/a", "na", "not applicable",
              "prefer not to say", "i don’t know", "don’t know"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower().replace("’", "'"))


def _number(text: str) -> float | None:
    cleaned = str(text).strip().replace(",", "")
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _range_key(text: str) -> float | None:
    """Ordering key for bands like '28-37 years', '<1 year', 'Above 50', '2–5 years'."""
    norm = _norm(text)
    match = re.search(r"(\d+(?:\.\d+)?)\s*([^\d]*)", norm)
    if not match:
        return None
    unit = match.group(2)
    if not re.search(r"year|yr", unit) and re.search(r"year|yr", norm[match.end():]):
        unit = norm[match.end():] if not re.search(r"month|week|day", unit) else unit
    scale = 1 / 12 if re.search(r"month", unit) else 1 / 52 if re.search(r"week", unit) else 1 / 365 if re.search(r"day", unit) else 1
    numbers = [float(match.group(1)) * scale]
    if re.search(r"less than|below|under|<|up to", norm):
        return numbers[0] - 0.5
    if re.search(r"more than|above|over|>|\+|and above|or more", norm):
        return numbers[0] + 0.5
    return numbers[0]


class Coding:
    """How a column's text answers map to numbers (or why they do not)."""

    def __init__(self, values: list[str], scale: dict | None = None):
        present = [v for v in values if str(v).strip()]
        self.levels: list[str] = []
        self.map: dict[str, float] = {}
        self.kind = "empty"
        self.excluded: list[str] = []
        if not present:
            return
        distinct = list(dict.fromkeys(str(v).strip() for v in present))
        if scale:
            lowered = {_norm(k): float(v) for k, v in scale.items() if _number(str(v)) is not None}
            hits = [v for v in distinct if _norm(v) in lowered]
            if len(hits) >= max(1, len(distinct) // 2):
                self.kind = "ordinal"
                self.map = {v: lowered[_norm(v)] for v in hits}
                self.excluded = [v for v in distinct if v not in self.map]
                self.levels = sorted(self.map, key=lambda v: self.map[v])
                return
        numeric = [_number(v) for v in present]
        if sum(n is not None for n in numeric) >= 0.9 * len(present):
            self.kind = "numeric"
            return
        coded = {v: float(m.group(1)) for v in distinct if (m := _CODED.match(v))}
        if coded and len(coded) >= 0.8 * len(distinct):
            self._ordinal(coded, distinct)
            return
        words = {}
        for value in distinct:
            norm = _norm(value)
            for group, score in LIKERT_WORDS:
                if norm in group:
                    words[value] = float(score)
                    break
        if words and len(words) >= max(2, 0.8 * len([v for v in distinct if _norm(v) not in _DONT_KNOW])):
            self._ordinal(words, distinct)
            return
        yes_no = {v: 1.0 if _norm(v).rstrip(".").split(",")[0] in _YES or _norm(v).startswith("yes")
                  else 0.0 for v in distinct
                  if _norm(v).rstrip(".") in _YES | _NO or _norm(v).startswith(("yes", "no"))}
        if yes_no and len(set(yes_no.values())) == 2 and len(yes_no) >= 0.6 * len(distinct):
            self._ordinal(yes_no, distinct, binary=True)
            return
        keys = {v: _range_key(v) for v in distinct}
        if all(k is not None for k in keys.values()) and len(distinct) > 1:
            self.kind = "ordered_bands"
            self.levels = sorted(distinct, key=lambda v: keys[v])
            self.map = {v: float(i + 1) for i, v in enumerate(self.levels)}
            return
        self.kind = "categorical"
        counts: dict[str, int] = {}
        for value in present:
            counts[str(value).strip()] = counts.get(str(value).strip(), 0) + 1
        self.levels = sorted(counts, key=lambda v: (-counts[v], v))

    def _ordinal(self, mapping: dict[str, float], distinct: list[str], binary: bool = False) -> None:
        self.kind = "binary" if binary else "ordinal"
        self.map = mapping
        self.excluded = [v for v in distinct if v not in mapping]
        self.levels = sorted(mapping, key=lambda v: mapping[v]) + self.excluded

    def value(self, text) -> float | None:
        text = str(text).strip()
        if not text:
            return None
        if self.kind == "numeric":
            return _number(text)
        return self.map.get(text)

    def describe(self) -> dict:
        info: dict = {"type": self.kind}
        if self.map:
            info["coding"] = {k: (int(v) if float(v).is_integer() else v) for k, v in sorted(self.map.items(), key=lambda kv: kv[1])}
        if self.excluded:
            info["treated_as_missing"] = self.excluded
        return info


# --- tables ------------------------------------------------------------------------

class Table:
    def __init__(self, header: list[str], rows: list[list[str]], source: str):
        self.header = header
        self.rows = rows
        self.source = source
        self._codings: dict[int, Coding] = {}
        self.scale: dict | None = None

    def index(self, ref) -> int:
        if isinstance(ref, int) and not isinstance(ref, bool):
            if 1 <= ref <= len(self.header):
                return ref - 1
            raise ValueError(f"There is no column #{ref}; the table has {len(self.header)} columns")
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("Name a column")
        ref = ref.strip()
        if re.fullmatch(r"#\d+", ref):
            return self.index(int(ref[1:]))
        if ref in self.header:
            return self.header.index(ref)
        lowered = [_norm(h) for h in self.header]
        target = _norm(ref)
        if target in lowered:
            return lowered.index(target)
        if re.fullmatch(r"\d+", ref):  # questionnaire item number: "9" matches "9. ..." / "Q9 ..."
            pattern = re.compile(rf"^(?:q(?:uestion)?\s*)?{ref}(?:[.):\s]|$)")
            hits = [i for i, h in enumerate(lowered) if pattern.match(h)]
            if len(hits) == 1:
                return hits[0]
        hits = [i for i, h in enumerate(lowered) if h.startswith(target)]
        if len(hits) == 1:
            return hits[0]
        contains = [i for i, h in enumerate(lowered) if target in h]
        if len(contains) == 1:
            return contains[0]
        if len(hits) > 1 or len(contains) > 1:
            names = [self.header[i] for i in (hits or contains)][:8]
            raise ValueError(f"'{ref}' matches several columns: {names}. Use the exact name or '#position'.")
        raise ValueError(f"No column matches '{ref}'. Run analysis 'overview' to list the columns.")

    def coding(self, index: int) -> Coding:
        if index not in self._codings:
            self._codings[index] = Coding([row[index] if index < len(row) else "" for row in self.rows], self.scale)
        return self._codings[index]

    def raw(self, index: int) -> list[str]:
        return [row[index].strip() if index < len(row) else "" for row in self.rows]

    def variable(self, ref) -> tuple[str, list[float | None], dict]:
        """A numeric variable: a coded column or the mean of several (a composite score)."""
        if isinstance(ref, dict) and isinstance(ref.get("mean_of"), list) and ref["mean_of"]:
            indices = [self.index(item) for item in ref["mean_of"]]
            columns = [self.variable(i + 1)[1] for i in indices]
            values = []
            for row in range(len(self.rows)):
                items = [col[row] for col in columns if col[row] is not None]
                values.append(sum(items) / len(items) if len(items) >= max(1, math.ceil(len(columns) / 2)) else None)
            name = str(ref.get("name") or f"Mean of {len(indices)} items")
            return name, values, {"type": "composite", "items": [self.header[i] for i in indices],
                                  "rule": "row mean of answered items (at least half answered)"}
        index = self.index(ref)
        coding = self.coding(index)
        if coding.kind in {"categorical", "empty"}:
            raise ValueError(f"'{self.header[index]}' is categorical (values like {coding.levels[:5]}); "
                             "use frequencies/crosstab, or pass a 'scale' to code it.")
        return self.header[index], [coding.value(v) for v in self.raw(index)], coding.describe()

    def category(self, ref) -> tuple[str, list[str], Coding]:
        index = self.index(ref)
        return self.header[index], self.raw(index), self.coding(index)


def _header_row(rows: list[list[str]]) -> int:
    """The first row that looks like column names: mostly filled text, not numbers."""
    for index, row in enumerate(rows[:30]):
        filled = [cell for cell in row if str(cell).strip()]
        if len(filled) >= max(2, 0.5 * max((len(r) for r in rows[:30]), default=1)) and \
                sum(_number(cell) is None for cell in filled) >= 0.7 * len(filled):
            return index
    return 0


def load(root: Path, arguments: dict) -> Table:
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Choose a data file")
    target = resolve(root, path)
    if target.stat().st_size > LIMITS["file_bytes"]:
        raise ValueError("Data file is larger than 48 MB")
    suffix = target.suffix.lower()
    sheet = arguments.get("sheet")
    if suffix == ".json":
        data = json.loads(target.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise ValueError("JSON data must be an array of objects")
        names: list[str] = []
        for item in data[:500]:
            names.extend(k for k in item if k not in names)
        grid = [names] + [["" if item.get(n) is None else str(item.get(n)) for n in names] for item in data]
    else:
        document = docextract.extract(target, full=True)
        if document["kind"] == "xlsx":
            sheets = document["sheets"]
            chosen = sheets[0]
            if sheet not in (None, ""):
                match = [s for s in sheets if s["name"].lower() == str(sheet).lower()]
                if not match and str(sheet).isdigit() and 1 <= int(sheet) <= len(sheets):
                    match = [sheets[int(sheet) - 1]]
                if not match:
                    raise ValueError(f"No sheet named {sheet}; sheets: {[s['name'] for s in sheets]}")
                chosen = match[0]
            elif not any(chosen["rows"]) and len(sheets) > 1:
                chosen = next((s for s in sheets if s["rows"]), chosen)
            grid = chosen["rows"]
        elif document["kind"] == "table":
            grid = document["rows"]
        elif document["kind"] == "docx":
            tables = [b["rows"] for b in document["blocks"] if b["type"] == "table"]
            if not tables:
                raise ValueError("That document has no tables")
            number = int(sheet) if str(sheet or "").isdigit() else 1
            if not 1 <= number <= len(tables):
                raise ValueError(f"The document has {len(tables)} tables")
            grid = tables[number - 1]
        else:
            raise ValueError("statistics reads CSV, TSV, JSON, XLSX/ODS or tables inside DOCX files")
    grid = [list(map(str, row)) for row in grid][: LIMITS["rows"] + 50]
    if not grid:
        raise ValueError("The table is empty")
    header_at = arguments.get("header_row")
    start = int(header_at) - 1 if isinstance(header_at, int) and header_at >= 1 else _header_row(grid)
    header = [" ".join(str(h).split()) or f"Column {i + 1}" for i, h in enumerate(grid[start])]
    width = max(len(row) for row in grid[start:])
    header += [f"Column {i + 1}" for i in range(len(header), width)]
    seen: dict[str, int] = {}
    for i, name in enumerate(header):  # duplicate names get a suffix so every column is addressable
        if name in seen:
            seen[name] += 1
            header[i] = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
    rows = [row + [""] * (width - len(row)) for row in grid[start + 1:] if any(str(c).strip() for c in row)]
    table = Table(header, rows, path)
    if isinstance(arguments.get("scale"), dict):
        table.scale = arguments["scale"]
    return table


def resolve(root: Path, path: str) -> Path:
    """A file inside root under the workspace file policy: relative, no traversal, links or secrets."""
    from .documents import _safe_target
    return _safe_target(root, path)


def _filter(table: Table, where) -> list[str]:
    if not where:
        return []
    if not isinstance(where, list):
        where = [where]
    notes = []
    for rule in where:
        if not isinstance(rule, dict) or "column" not in rule:
            raise ValueError("Each filter needs a column")
        index = table.index(rule["column"])
        before = len(table.rows)

        def keep(cell: str) -> bool:
            cell = _norm(cell)
            if "equals" in rule:
                return cell == _norm(rule["equals"])
            if "not_equals" in rule:
                return cell != _norm(rule["not_equals"])
            if "contains" in rule:
                return _norm(rule["contains"]) in cell
            if "in" in rule and isinstance(rule["in"], list):
                return cell in {_norm(v) for v in rule["in"]}
            if "min" in rule or "max" in rule:
                value = _number(cell)
                return value is not None and value >= float(rule.get("min", -math.inf)) and value <= float(rule.get("max", math.inf))
            raise ValueError("A filter needs equals, not_equals, contains, in, min or max")
        table.rows = [row for row in table.rows if keep(row[index] if index < len(row) else "")]
        table._codings.clear()
        notes.append(f"{table.header[index]}: kept {len(table.rows)} of {before} rows")
    return notes


# --- summaries --------------------------------------------------------------------

def _describe(values: list[float]) -> dict:
    data = sorted(values)
    n = len(data)
    if not n:
        return {"n": 0}
    mean = sum(data) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in data) / (n - 1)) if n > 1 else 0.0

    def q(p):
        position = (n - 1) * p
        low, high = math.floor(position), math.ceil(position)
        return data[low] + (data[high] - data[low]) * (position - low)
    result = {"n": n, "mean": _r(mean), "sd": _r(sd), "median": _r(q(0.5)), "q1": _r(q(0.25)), "q3": _r(q(0.75)),
              "min": _r(data[0]), "max": _r(data[-1])}
    if n > 1:
        se = sd / math.sqrt(n)
        margin = t_critical(n - 1) * se
        result.update({"se": _r(se), "ci95": [_r(mean - margin), _r(mean + margin)]})
    if n > 2 and sd > 0:
        m3 = sum((v - mean) ** 3 for v in data) / n
        m2 = sum((v - mean) ** 2 for v in data) / n
        result["skewness"] = _r(m3 / m2 ** 1.5 * math.sqrt(n * (n - 1)) / (n - 2))
        if n > 3:
            m4 = sum((v - mean) ** 4 for v in data) / n
            g2 = m4 / m2 ** 2 - 3
            result["excess_kurtosis"] = _r(((n + 1) * g2 + 6) * (n - 1) / ((n - 2) * (n - 3)))
    return result


def _frequencies(table: Table, ref) -> dict:
    name, raw, coding = table.category(ref)
    counts: dict[str, int] = {}
    missing = 0
    for value in raw:
        if not value:
            missing += 1
            continue
        counts[value] = counts.get(value, 0) + 1
    order = coding.levels if coding.kind not in {"numeric"} else sorted(counts, key=lambda v: _number(v) or 0)
    order = [v for v in order if v in counts] + [v for v in counts if v not in order]
    valid = sum(counts.values())
    rows, cumulative = [], 0.0
    for value in order[: LIMITS["categories"]]:
        percent = 100 * counts[value] / valid if valid else 0
        cumulative += percent
        rows.append({"value": value, "n": counts[value], "percent": _r(percent, 1), "cumulative_percent": _r(cumulative, 1)})
    result = {"column": name, "valid_n": valid, "missing": missing, "categories": rows, **coding.describe()}
    if len(order) > LIMITS["categories"]:
        result["note"] = f"{len(order)} distinct values; first {LIMITS['categories']} shown"
    if coding.map and coding.kind != "binary":
        coded = [coding.value(v) for v in raw]
        result["summary"] = _describe([v for v in coded if v is not None])
    return result


def _verbal(mean: float, top: float) -> str:
    """The usual interval reading of a mean on a 1..k agreement scale."""
    if top == 5:
        bands = [(1.80, "Strongly disagree"), (2.60, "Disagree"), (3.40, "Neutral"), (4.20, "Agree"), (99, "Strongly agree")]
    elif top == 4:
        bands = [(1.75, "Strongly disagree"), (2.50, "Disagree"), (3.25, "Agree"), (99, "Strongly agree")]
    else:
        return ""
    return next(label for limit, label in bands if mean <= limit)


def _alpha(columns: list[list[float | None]]) -> dict:
    complete = [row for row in zip(*columns) if all(v is not None for v in row)]
    k = len(columns)
    n = len(complete)
    if k < 2 or n < 3:
        return {"alpha": None, "n_complete": n, "items": k, "note": "Needs at least 2 items and 3 complete responses"}

    def var(values):
        m = sum(values) / len(values)
        return sum((v - m) ** 2 for v in values) / (len(values) - 1)
    item_vars = [var([row[i] for row in complete]) for i in range(k)]
    totals = [sum(row) for row in complete]
    total_var = var(totals)
    alpha = (k / (k - 1)) * (1 - sum(item_vars) / total_var) if total_var > 0 else None
    items = []
    for i in range(k):
        rest = [t - row[i] for t, row in zip(totals, complete)]
        r = _pearson([row[i] for row in complete], rest)[0]
        others = [j for j in range(k) if j != i]
        if len(others) >= 2:
            sub_totals = [sum(row[j] for j in others) for row in complete]
            sub_var = var(sub_totals)
            deleted = (len(others) / (len(others) - 1)) * (1 - sum(item_vars[j] for j in others) / sub_var) if sub_var > 0 else None
        else:
            deleted = None
        items.append({"corrected_item_total_r": _r(r), "alpha_if_deleted": _r(deleted)})
    reading = None if alpha is None else ("excellent" if alpha >= .9 else "good" if alpha >= .8 else "acceptable" if alpha >= .7
                                         else "questionable" if alpha >= .6 else "poor" if alpha >= .5 else "unacceptable")
    return {"alpha": _r(alpha), "reading": reading, "n_complete": n, "items": k, "per_item": items}


def _likert(table: Table, refs) -> dict:
    if not refs:
        raise ValueError("Name the Likert items in columns")
    items, columns, top_values = [], [], []
    for ref in refs:
        name, values, info = table.variable(ref)
        index = table.index(ref) if not isinstance(ref, dict) else None
        raw = table.raw(index) if index is not None else []
        counts: dict[str, int] = {}
        coding = table.coding(index) if index is not None else None
        for value in raw:
            if value:
                counts[value] = counts.get(value, 0) + 1
        valid = [v for v in values if v is not None]
        top = max(coding.map.values()) if coding and coding.map else (max(valid) if valid else 0)
        bottom = min(coding.map.values()) if coding and coding.map else (min(valid) if valid else 0)
        top_values.append(top)
        d = _describe(valid)
        mid = (top + bottom) / 2
        agree = sum(1 for v in valid if v > mid)
        disagree = sum(1 for v in valid if v < mid)
        item = {"item": name, "n": len(valid), "mean": d.get("mean"), "sd": d.get("sd"),
                "agree_percent": _r(100 * agree / len(valid), 1) if valid else None,
                "neutral_percent": _r(100 * (len(valid) - agree - disagree) / len(valid), 1) if valid else None,
                "disagree_percent": _r(100 * disagree / len(valid), 1) if valid else None}
        if coding and coding.levels:
            item["distribution"] = [{"value": level, "n": counts.get(level, 0),
                                     "percent": _r(100 * counts.get(level, 0) / max(sum(counts.values()), 1), 1)}
                                    for level in coding.levels if counts.get(level)]
        if d.get("mean") is not None and top in (4, 5) and bottom == 1:
            item["interpretation"] = _verbal(d["mean"], top)
        if coding and coding.excluded:
            item["not_scored"] = {v: counts.get(v, 0) for v in coding.excluded}
        items.append(item)
        columns.append(values)
    composite_rows = []
    for row in zip(*columns):
        answered = [v for v in row if v is not None]
        composite_rows.append(sum(answered) / len(answered) if len(answered) >= math.ceil(len(columns) / 2) else None)
    composite = _describe([v for v in composite_rows if v is not None])
    top = max(top_values) if top_values else 0
    result = {"items": items, "composite": {**{k: composite.get(k) for k in ("n", "mean", "sd", "median", "min", "max", "ci95")},
                                            **({"interpretation": _verbal(composite["mean"], top)} if composite.get("mean") is not None and top in (4, 5) else {})},
              "reliability": _alpha(columns) if len(columns) > 1 else None,
              "coding": table.variable(refs[0])[2].get("coding") if not isinstance(refs[0], dict) else None,
              "reading_rule": "Mean bands on a 5-point scale: 1.00–1.80 strongly disagree, 1.81–2.60 disagree, 2.61–3.40 neutral, "
                              "3.41–4.20 agree, 4.21–5.00 strongly agree. Agree % counts answers above the scale midpoint."}
    ranked = sorted((i for i in items if i["mean"] is not None), key=lambda i: -i["mean"])
    if ranked:
        result["highest_item"], result["lowest_item"] = ranked[0]["item"], ranked[-1]["item"]
    return result


# --- tests ------------------------------------------------------------------------

def _crosstab(table: Table, row_ref, col_ref) -> dict:
    row_name, row_raw, row_coding = table.category(row_ref)
    col_name, col_raw, col_coding = table.category(col_ref)
    pairs = [(r, c) for r, c in zip(row_raw, col_raw) if r and c]
    row_levels = [v for v in row_coding.levels if any(p[0] == v for p in pairs)] or sorted({p[0] for p in pairs})
    col_levels = [v for v in col_coding.levels if any(p[1] == v for p in pairs)] or sorted({p[1] for p in pairs})
    row_levels += sorted({p[0] for p in pairs} - set(row_levels))
    col_levels += sorted({p[1] for p in pairs} - set(col_levels))
    if len(row_levels) > LIMITS["categories"] or len(col_levels) > LIMITS["categories"]:
        raise ValueError("Too many categories for a contingency table; recode first")
    counts = [[sum(1 for p in pairs if p == (r, c)) for c in col_levels] for r in row_levels]
    n = len(pairs)
    row_totals = [sum(row) for row in counts]
    col_totals = [sum(counts[i][j] for i in range(len(row_levels))) for j in range(len(col_levels))]
    result: dict = {"row_variable": row_name, "column_variable": col_name, "n": n, "columns": col_levels,
                    "table": [{"row": r, "counts": counts[i], "total": row_totals[i],
                               "row_percent": [_r(100 * c / row_totals[i], 1) if row_totals[i] else None for c in counts[i]]}
                              for i, r in enumerate(row_levels)],
                    "column_totals": col_totals}
    if len(row_levels) < 2 or len(col_levels) < 2 or n == 0:
        result["note"] = "A test needs at least two categories on each side"
        return result
    chi2, small = 0.0, 0
    for i in range(len(row_levels)):
        for j in range(len(col_levels)):
            expected = row_totals[i] * col_totals[j] / n
            if expected < 5:
                small += 1
            if expected > 0:
                chi2 += (counts[i][j] - expected) ** 2 / expected
    df = (len(row_levels) - 1) * (len(col_levels) - 1)
    cells = len(row_levels) * len(col_levels)
    result["chi_square"] = {"statistic": _r(chi2, 3), "df": df, "p": _p(p_chi2(chi2, df)),
                            "cramers_v": _r(math.sqrt(chi2 / (n * (min(len(row_levels), len(col_levels)) - 1))), 3),
                            "expected_below_5_percent": _r(100 * small / cells, 1)}
    if df == 1:
        a, b = counts[0]
        c, d = counts[1]
        result["fisher_exact_p"] = _p(_fisher(a, b, c, d))
        yates = sum((abs(counts[i][j] - row_totals[i] * col_totals[j] / n) - 0.5) ** 2 / (row_totals[i] * col_totals[j] / n)
                    for i in range(2) for j in range(2) if row_totals[i] * col_totals[j])
        result["chi_square"]["yates_corrected_p"] = _p(p_chi2(max(yates, 0), 1))
        if b * c:
            odds = (a * d) / (b * c)
            if a and d:
                se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
                result["odds_ratio"] = {"value": _r(odds, 3), "ci95": [_r(math.exp(math.log(odds) - 1.96 * se), 3),
                                                                      _r(math.exp(math.log(odds) + 1.96 * se), 3)]}
    if small / cells > 0.2:
        result["warning"] = ("More than 20% of cells expect fewer than 5 counts, so the chi-square p-value is unreliable"
                             + ("; report Fisher's exact p." if df == 1 else "; merge sparse categories or report this caution."))
    return result


def _fisher(a: int, b: int, c: int, d: int) -> float:
    n = a + b + c + d
    row1, col1 = a + b, a + c

    def prob(x):
        return math.exp(math.lgamma(row1 + 1) + math.lgamma(n - row1 + 1) + math.lgamma(col1 + 1) + math.lgamma(n - col1 + 1)
                        - math.lgamma(n + 1) - math.lgamma(x + 1) - math.lgamma(row1 - x + 1)
                        - math.lgamma(col1 - x + 1) - math.lgamma(n - row1 - col1 + x + 1))
    observed = prob(a)
    low, high = max(0, row1 + col1 - n), min(row1, col1)
    return min(1.0, sum(p for x in range(low, high + 1) if (p := prob(x)) <= observed * (1 + 1e-7)))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _pearson(a: list[float], b: list[float]) -> tuple[float | None, float | None]:
    n = len(a)
    if n < 3:
        return None, None
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None, None
    r = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)
    r = max(-1.0, min(1.0, r))
    if abs(r) == 1:
        return r, 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, p_t(t, n - 2)


def _compare(table: Table, outcome_ref, group_ref) -> dict:
    name, values, info = table.variable(outcome_ref)
    group_name, groups_raw, coding = table.category(group_ref)
    groups: dict[str, list[float]] = {}
    for value, group in zip(values, groups_raw):
        if value is not None and group:
            groups.setdefault(group, []).append(value)
    order = [g for g in coding.levels if g in groups] + [g for g in groups if g not in coding.levels]
    groups = {g: groups[g] for g in order if len(groups[g]) >= 1}
    summary = [{"group": g, **{k: v for k, v in _describe(vals).items() if k in ("n", "mean", "sd", "median", "q1", "q3")}}
               for g, vals in groups.items()]
    result: dict = {"outcome": name, "outcome_coding": info, "group_variable": group_name, "groups": summary}
    usable = {g: v for g, v in groups.items() if len(v) >= 2}
    if len(usable) < 2:
        result["note"] = "A comparison needs at least two groups with two or more values"
        return result
    everything = [v for vals in usable.values() for v in vals]
    ranks = _ranks(everything)
    ranked, position = {}, 0
    for g, vals in usable.items():
        ranked[g] = ranks[position:position + len(vals)]
        position += len(vals)
    n_total = len(everything)
    if len(usable) == 2:
        (g1, a), (g2, b) = usable.items()
        m1, m2 = sum(a) / len(a), sum(b) / len(b)
        v1 = sum((x - m1) ** 2 for x in a) / (len(a) - 1)
        v2 = sum((x - m2) ** 2 for x in b) / (len(b) - 1)
        se = math.sqrt(v1 / len(a) + v2 / len(b))
        if se > 0:
            t = (m1 - m2) / se
            df = (v1 / len(a) + v2 / len(b)) ** 2 / ((v1 / len(a)) ** 2 / (len(a) - 1) + (v2 / len(b)) ** 2 / (len(b) - 1))
            pooled = math.sqrt(((len(a) - 1) * v1 + (len(b) - 1) * v2) / (len(a) + len(b) - 2))
            margin = t_critical(df) * se
            result["welch_t_test"] = {"t": _r(t, 3), "df": _r(df, 2), "p": _p(p_t(t, df)),
                                      "mean_difference": _r(m1 - m2), "ci95": [_r(m1 - m2 - margin), _r(m1 - m2 + margin)],
                                      "cohens_d": _r((m1 - m2) / pooled, 3) if pooled else None}
        r1 = sum(ranked[g1])
        u1 = r1 - len(a) * (len(a) + 1) / 2
        u = min(u1, len(a) * len(b) - u1)
        ties = _tie_correction(everything)
        sigma = math.sqrt(len(a) * len(b) / 12 * ((n_total + 1) - ties / (n_total * (n_total - 1))))
        z = (u - len(a) * len(b) / 2) / sigma if sigma else 0
        result["mann_whitney_u"] = {"u": _r(u, 1), "z": _r(z, 3), "p": _p(p_norm(z)), "r_effect": _r(abs(z) / math.sqrt(n_total), 3)}
    else:
        grand = sum(everything) / n_total
        between = sum(len(v) * (sum(v) / len(v) - grand) ** 2 for v in usable.values())
        within = sum(sum((x - sum(v) / len(v)) ** 2 for x in v) for v in usable.values())
        k = len(usable)
        df1, df2 = k - 1, n_total - k
        if within > 0 and df2 > 0:
            f = (between / df1) / (within / df2)
            result["anova"] = {"f": _r(f, 3), "df_between": df1, "df_within": df2, "p": _p(p_f(f, df1, df2)),
                               "eta_squared": _r(between / (between + within), 3)}
        h = 12 / (n_total * (n_total + 1)) * sum(sum(r) ** 2 / len(r) for r in ranked.values()) - 3 * (n_total + 1)
        ties = _tie_correction(everything)
        if n_total ** 3 - n_total:
            h /= 1 - ties / (n_total ** 3 - n_total)
        result["kruskal_wallis"] = {"h": _r(h, 3), "df": k - 1, "p": _p(p_chi2(h, k - 1)),
                                    "epsilon_squared": _r(h / ((n_total ** 2 - 1) / (n_total + 1)), 3)}
    small = [g for g, v in groups.items() if len(v) < 5]
    if small:
        result["warning"] = f"Small groups ({', '.join(small[:6])}) make these tests fragile; report them with caution."
    return result


def _tie_correction(values: list[float]) -> float:
    counts: dict[float, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sum(t ** 3 - t for t in counts.values() if t > 1)


def _correlation(table: Table, refs) -> dict:
    if not refs or len(refs) < 2:
        raise ValueError("Name at least two variables")
    variables = [table.variable(ref) for ref in refs[: LIMITS["columns"]]]
    names = [v[0] for v in variables]
    pearson, spearman = {}, {}
    for i, (name_a, a, _) in enumerate(variables):
        pearson[name_a], spearman[name_a] = {}, {}
        for j, (name_b, b, _) in enumerate(variables):
            pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
            if i == j:
                pearson[name_a][name_b] = {"r": 1.0, "n": len(pairs)}
                spearman[name_a][name_b] = {"rho": 1.0, "n": len(pairs)}
                continue
            xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
            r, p = _pearson(xs, ys)
            rho, ps = _pearson(_ranks(xs), _ranks(ys)) if len(pairs) >= 3 else (None, None)
            pearson[name_a][name_b] = {"r": _r(r, 3), "p": _p(p), "n": len(pairs)}
            spearman[name_a][name_b] = {"rho": _r(rho, 3), "p": _p(ps), "n": len(pairs)}
    return {"variables": names, "codings": {v[0]: v[2] for v in variables}, "pearson": pearson, "spearman": spearman,
            "reading_rule": "|r| < .30 weak, .30–.49 moderate, ≥ .50 strong (Cohen). Spearman suits ordinal Likert items."}


# --- regression -----------------------------------------------------------------------

def _invert(matrix: list[list[float]]) -> list[list[float]] | None:
    n = len(matrix)
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        factor = aug[col][col]
        aug[col] = [v / factor for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col]:
                f = aug[r][col]
                aug[r] = [v - f * w for v, w in zip(aug[r], aug[col])]
    return [row[n:] for row in aug]


def _design(table: Table, outcome_ref, predictor_refs, logistic: bool, positive):
    if not predictor_refs:
        raise ValueError("Name the predictors")
    if logistic:
        y_name, y_raw, y_coding = table.category(outcome_ref)
        present = [v for v in y_raw if v]
        levels = list(dict.fromkeys(present))
        if positive:
            target = next((v for v in levels if _norm(v) == _norm(positive)), None)
            if target is None:
                raise ValueError(f"'{positive}' is not a value of {y_name}; values: {levels[:10]}")
        elif y_coding.kind == "binary":
            target = next(v for v in levels if y_coding.map.get(v) == 1.0)
        else:
            if len(levels) != 2:
                raise ValueError(f"Logistic regression needs a two-valued outcome or 'positive'; {y_name} has {levels[:10]}")
            target = min(levels, key=present.count)
        y_values = [None if not v or (y_coding.kind == "binary" and v not in y_coding.map and not positive) else (1.0 if v == target else 0.0) for v in y_raw]
        y_info = {"type": "binary", "positive": target}
    else:
        y_name, y_values, y_info = table.variable(outcome_ref)
        target = None
    columns, names, infos = [], [], []
    for ref in predictor_refs:
        try:
            name, values, info = table.variable(ref)
            columns.append(values)
            names.append(name)
            infos.append(info)
        except ValueError:
            name, raw, coding = table.category(ref)  # categorical predictor: dummy-code against the largest group
            levels = [v for v in coding.levels if v]
            reference = levels[0]
            for level in levels[1:12]:
                columns.append([None if not v else (1.0 if v == level else 0.0) for v in raw])
                names.append(f"{name} = {level}")
                infos.append({"type": "dummy", "reference": reference})
    rows = [(y, [col[i] for col in columns]) for i, y in enumerate(y_values)
            if y is not None and all(col[i] is not None for col in columns)]
    return y_name, y_info, names, infos, rows


def _regression(table: Table, outcome_ref, predictor_refs, model: str, positive) -> dict:
    logistic = model == "logistic"
    y_name, y_info, names, infos, rows = _design(table, outcome_ref, predictor_refs, logistic, positive)
    n, k = len(rows), len(names)
    if n < k + 3:
        raise ValueError(f"Only {n} complete rows for {k} predictors; drop predictors or fill missing data")
    X = [[1.0] + x for _, x in rows]
    y = [r[0] for r in rows]
    p = k + 1
    labels = ["(Intercept)"] + names
    result: dict = {"model": "logistic" if logistic else "linear (OLS)", "outcome": y_name, "outcome_coding": y_info,
                    "n": n, "predictor_codings": dict(zip(names, infos))}
    if not logistic:
        xtx = [[sum(X[r][i] * X[r][j] for r in range(n)) for j in range(p)] for i in range(p)]
        inv = _invert(xtx)
        if inv is None:
            raise ValueError("Predictors are perfectly collinear; remove a redundant one")
        xty = [sum(X[r][i] * y[r] for r in range(n)) for i in range(p)]
        beta = [sum(inv[i][j] * xty[j] for j in range(p)) for i in range(p)]
        fitted = [sum(b * x for b, x in zip(beta, row)) for row in X]
        ss_res = sum((a - f) ** 2 for a, f in zip(y, fitted))
        mean_y = sum(y) / n
        ss_tot = sum((a - mean_y) ** 2 for a in y)
        df_res = n - p
        sigma2 = ss_res / df_res
        r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
        sd_y = math.sqrt(ss_tot / (n - 1)) if n > 1 else 0
        tcrit = t_critical(df_res)
        coefficients = []
        for i in range(p):
            se = math.sqrt(max(inv[i][i] * sigma2, 0))
            t = beta[i] / se if se else float("nan")
            entry = {"term": labels[i], "b": _r(beta[i]), "se": _r(se), "t": _r(t, 3), "p": _p(p_t(t, df_res)),
                     "ci95": [_r(beta[i] - tcrit * se), _r(beta[i] + tcrit * se)]}
            if i and sd_y:
                xs = [row[i] for row in X]
                mx = sum(xs) / n
                sd_x = math.sqrt(sum((v - mx) ** 2 for v in xs) / (n - 1))
                entry["beta_standardized"] = _r(beta[i] * sd_x / sd_y, 3)
            coefficients.append(entry)
        f = (r2 / k) / ((1 - r2) / df_res) if r2 < 1 and k else float("nan")
        result.update({"coefficients": coefficients, "r": _r(math.sqrt(max(r2, 0)), 4), "r_squared": _r(r2, 4),
                       "adjusted_r_squared": _r(1 - (1 - r2) * (n - 1) / df_res, 4),
                       "f": _r(f, 3), "df": [k, df_res], "p_model": _p(p_f(f, k, df_res)),
                       "std_error_of_estimate": _r(math.sqrt(sigma2), 4)})
        if k > 1:
            result["vif"] = _vif(X, names)
        result["equation"] = f"{y_name} = " + " + ".join([f"{_r(beta[0], 3)}"] + [f"{_r(b, 3)}×[{nm}]" for b, nm in zip(beta[1:], names)])
        return result
    beta = [0.0] * p
    converged = False
    inv = None
    for _ in range(50):
        eta = [sum(b * x for b, x in zip(beta, row)) for row in X]
        mu = [1 / (1 + math.exp(-max(min(e, 30), -30))) for e in eta]
        w = [m * (1 - m) for m in mu]
        hessian = [[sum(X[r][i] * X[r][j] * w[r] for r in range(n)) + (1e-9 if i == j else 0.0) for j in range(p)] for i in range(p)]
        inv = _invert(hessian)
        if inv is None:
            raise ValueError("The logistic model cannot be estimated (complete separation or collinear predictors)")
        gradient = [sum(X[r][i] * (y[r] - mu[r]) for r in range(n)) for i in range(p)]
        step = [sum(inv[i][j] * gradient[j] for j in range(p)) for i in range(p)]
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-8:
            converged = True
            break
        if max(abs(b) for b in beta) > 25:
            break  # coefficients running away: the outcome is (quasi-)perfectly separated
    eta = [sum(b * x for b, x in zip(beta, row)) for row in X]
    mu = [min(max(1 / (1 + math.exp(-max(min(e, 30), -30))), 1e-12), 1 - 1e-12) for e in eta]
    ll = sum(a * math.log(m) + (1 - a) * math.log(1 - m) for a, m in zip(y, mu))
    base = sum(y) / n
    ll0 = n * (base * math.log(base) + (1 - base) * math.log(1 - base)) if 0 < base < 1 else 0.0
    model_chi2 = 2 * (ll - ll0)
    coefficients = []
    for i in range(p):
        se = math.sqrt(max(inv[i][i], 0)) if inv else float("nan")
        z = beta[i] / se if se else float("nan")
        coefficients.append({"term": labels[i], "b": _r(beta[i]), "se": _r(se), "wald_z": _r(z, 3), "p": _p(p_norm(z)),
                             "odds_ratio": _r(math.exp(max(min(beta[i], 50), -50)), 4),
                             "or_ci95": [_r(math.exp(max(min(beta[i] - 1.96 * se, 50), -50)), 4),
                                         _r(math.exp(max(min(beta[i] + 1.96 * se, 50), -50)), 4)]})
    correct = sum(1 for a, m in zip(y, mu) if (m >= 0.5) == (a == 1.0))
    result.update({"coefficients": coefficients, "converged": converged, "log_likelihood": _r(ll, 3),
                   "model_chi_square": _r(model_chi2, 3), "df": k, "p_model": _p(p_chi2(model_chi2, k)),
                   "mcfadden_r_squared": _r(1 - ll / ll0, 4) if ll0 else None,
                   "nagelkerke_r_squared": _r((1 - math.exp(2 * (ll0 - ll) / n)) / (1 - math.exp(2 * ll0 / n)), 4) if ll0 else None,
                   "classification_accuracy_percent": _r(100 * correct / n, 1),
                   "events": int(sum(y)), "events_per_predictor": _r(min(sum(y), n - sum(y)) / k, 1)})
    if not converged or max(abs(b) for b in beta[1:]) > 15:
        result["warning"] = ("Complete or quasi-complete separation: a predictor (almost) perfectly predicts the outcome, so the "
                             "odds ratios and p-values are not meaningful. Report the crosstab instead, or merge categories.")
        result["separation"] = True
    elif min(sum(y), n - sum(y)) / k < 10:
        result["warning"] = "Fewer than 10 events per predictor; estimates may be unstable."
    return result


def _vif(X: list[list[float]], names: list[str]) -> dict:
    out = {}
    for j in range(1, len(X[0])):
        others = [[1.0] + [row[c] for c in range(1, len(row)) if c != j] for row in X]
        target = [row[j] for row in X]
        p = len(others[0])
        xtx = [[sum(r[a] * r[b] for r in others) for b in range(p)] for a in range(p)]
        inv = _invert(xtx)
        if inv is None:
            out[names[j - 1]] = None
            continue
        xty = [sum(r[a] * t for r, t in zip(others, target)) for a in range(p)]
        beta = [sum(inv[a][b] * xty[b] for b in range(p)) for a in range(p)]
        fitted = [sum(b * v for b, v in zip(beta, r)) for r in others]
        mean = sum(target) / len(target)
        ss_tot = sum((t - mean) ** 2 for t in target)
        r2 = 1 - sum((t - f) ** 2 for t, f in zip(target, fitted)) / ss_tot if ss_tot else 1
        out[names[j - 1]] = _r(1 / (1 - r2), 3) if r2 < 1 else None
    return out


# --- entry point ----------------------------------------------------------------------

def _overview(table: Table) -> dict:
    columns = []
    for index, name in enumerate(table.header):
        coding = table.coding(index)
        raw = table.raw(index)
        missing = sum(1 for v in raw if not v)
        entry = {"#": index + 1, "name": name, "type": coding.kind, "missing": missing}
        counts: dict[str, int] = {}
        for value in raw:
            if value:
                counts[value] = counts.get(value, 0) + 1
        entry["distinct"] = len(counts)
        if coding.kind == "numeric":
            entry["summary"] = {k: v for k, v in _describe([x for x in (coding.value(v) for v in raw) if x is not None]).items()
                                if k in ("n", "mean", "sd", "min", "max")}
        elif len(counts) <= 15:
            order = [v for v in coding.levels if v in counts] + [v for v in counts if v not in coding.levels]
            entry["values"] = {v: counts[v] for v in order}
        else:
            entry["examples"] = [v[:80] for v in list(counts)[:5]]
            if len(counts) > 0.5 * max(len(raw) - missing, 1):
                entry["looks_like"] = "free text or identifiers"
        if coding.map:
            entry["coding"] = coding.describe().get("coding")
        columns.append(entry)
    return {"source": table.source, "rows": len(table.rows), "column_count": len(table.header), "columns": columns,
            "next": "Use frequencies for demographics, likert for scale items (one call per construct), crosstab for "
                    "associations, reliability, compare, correlation and regression for hypotheses."}


def run(root: Path, arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("Arguments must be an object")
    analysis = arguments.get("analysis") or "overview"
    table = load(Path(root), arguments)
    filters = _filter(table, arguments.get("where"))
    columns = arguments.get("columns") or []
    if not isinstance(columns, list):
        columns = [columns]
    if analysis == "overview":
        result = _overview(table)
    elif analysis == "frequencies":
        if not columns:
            raise ValueError("Name the columns to count")
        result = {"frequencies": [_frequencies(table, ref) for ref in columns[: LIMITS["columns"]]], "n_rows": len(table.rows)}
    elif analysis == "descriptives":
        if not columns:
            raise ValueError("Name the columns to describe")
        out = []
        for ref in columns[: LIMITS["columns"]]:
            name, values, info = table.variable(ref)
            out.append({"variable": name, **_describe([v for v in values if v is not None]),
                        "missing": sum(1 for v in values if v is None), "coding": info})
        result = {"descriptives": out}
    elif analysis == "likert":
        result = _likert(table, columns[: LIMITS["columns"]])
    elif analysis == "reliability":
        variables = [table.variable(ref) for ref in columns[: LIMITS["columns"]]]
        alpha = _alpha([v[1] for v in variables])
        for item, name in zip(alpha.get("per_item") or [], [v[0] for v in variables]):
            item["item"] = name
        result = {"cronbach": alpha}
    elif analysis == "crosstab":
        result = _crosstab(table, arguments.get("row"), arguments.get("column"))
    elif analysis == "compare":
        result = _compare(table, arguments.get("outcome"), arguments.get("group"))
    elif analysis == "correlation":
        result = _correlation(table, columns)
    elif analysis == "regression":
        result = _regression(table, arguments.get("outcome"), arguments.get("predictors") or columns,
                             arguments.get("model") or "linear", arguments.get("positive"))
    else:
        raise ValueError(f"Unknown analysis {analysis}")
    if filters:
        result["filters"] = filters
    result.setdefault("n_rows_analyzed", len(table.rows))
    return result
