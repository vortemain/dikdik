"""Extract readable structure from office documents with the standard library only.

The workspace and chat need to *show* documents, not just store them: a .docx
should read like a document, a spreadsheet like a grid. Everything here is
bounded, offline and dependency-free so the wheel keeps shipping unchanged.

`extract(path)` returns a JSON-friendly dict. Shapes:

  docx  -> {"kind": "docx", "blocks": [...], "meta": {...}, "truncated": bool}
           block: heading {"type","level","text"} | para {"type","text"} |
           list item {"type":"li","style","text"} | table {"type","rows":[[...]]}
  xlsx  -> {"kind": "xlsx", "sheets": [{"name", "rows": [[cell]], "truncated"}]}
  pptx  -> {"kind": "pptx", "slides": [{"title", "points", "notes"}]}
  pdf   -> {"kind": "pdf", "text", "pages": [...]}
  table -> {"kind": "table", "rows", "delimiter", "has_header", "columns"}

`attachment_text(path, mime)` gives the model a bounded plain-text view of any
attachment, so research files arrive as content instead of "binary".
"""

from __future__ import annotations

import csv
import re
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

DOC_LIMITS = {
    "blocks": 1200,          # docx paragraphs / tables / list items
    "block_chars": 400,      # per paragraph
    "table_rows": 200,       # per docx table
    "sheet_rows": 400,       # per worksheet
    "sheet_cols": 40,
    "sheets": 12,
    "slides": 80,
    "pdf_chars": 60_000,
    "text_chars": 48_000,    # attachment text window
}
_CELL_RE = re.compile(r"([A-Z]+)(\d+)")
_TEXT_RUN_RE = re.compile(rb"\(((?:[^()\\]|\\.)+)\)\s*Tj|\[((?:[^\]\\]|\\.)*)\]\s*TJ")


def _text(value) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text.strip()


def _clamp(text: str, limit: int) -> str:
    text = _text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _col_index(reference: str) -> int:
    letters = _CELL_RE.match(reference).group(1) if _CELL_RE.match(reference) else ""
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return max(index - 1, 0)


# --- docx / pptx (Office Open XML) --------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_paragraph(paragraph) -> dict:
    text = "".join(node.text or "" for node in paragraph.iter(f"{_W}t"))
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    name = style.get(f"{_W}val", "") if style is not None else ""
    match = re.fullmatch(r"[Hh]eading\s*(\d)", name)
    if match:
        return {"type": "h", "level": int(match.group(1)), "text": _clamp(text, DOC_LIMITS["block_chars"])}
    if paragraph.find(f"{_W}pPr/{_W}numPr") is not None:
        return {"type": "li", "text": _clamp(text, DOC_LIMITS["block_chars"])}
    return {"type": "p", "text": _clamp(text, DOC_LIMITS["block_chars"])}


def _docx_table(table) -> dict:
    rows = []
    for row in table.iter(f"{_W}tr"):
        cells = ["".join(node.text or "" for node in cell.iter(f"{_W}t")) for cell in row.findall(f"{_W}tc")]
        rows.append([_clamp(cell, 120) for cell in cells])
        if len(rows) >= DOC_LIMITS["table_rows"]:
            break
    return {"type": "table", "rows": rows}


def _extract_docx(archive: zipfile.ZipFile) -> dict:
    root = ElementTree.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{_W}body")
    blocks: list[dict] = []
    if body is not None:
        for child in body:
            if child.tag == f"{_W}p":
                blocks.append(_docx_paragraph(child))
            elif child.tag == f"{_W}tbl":
                blocks.append(_docx_table(child))
            if len(blocks) >= DOC_LIMITS["blocks"]:
                break
    truncated = len(blocks) >= DOC_LIMITS["blocks"]
    blocks = blocks[: DOC_LIMITS["blocks"]]
    kept = [block for block in blocks if (block.get("text") or "").strip() or block.get("rows")]
    return {"kind": "docx", "blocks": kept, "truncated": truncated or any(
        len(block.get("rows", [])) >= DOC_LIMITS["table_rows"] for block in kept)}


_SPREADSHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xlsx_shared(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(f"{_SPREADSHEET}t")) for item in root]


def _xlsx_rows(handle, shared: list[str]) -> dict:
    rows: list[list[str]] = []
    truncated = False
    root = ElementTree.fromstring(handle.read())
    for row in root.iter(f"{_SPREADSHEET}row"):
        cells: dict[int, str] = {}
        for cell in row.findall(f"{_SPREADSHEET}c"):
            reference = cell.get("r") or ""
            index = _col_index(reference)
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
                elif kind == "b" and raw:
                    value = "TRUE" if raw == "1" else "FALSE"
                else:
                    value = raw
            cells[index] = value
        if cells:
            width = min(max(cells) + 1, DOC_LIMITS["sheet_cols"])
            rows.append([cells.get(column, "") for column in range(width)])
        if len(rows) >= DOC_LIMITS["sheet_rows"]:
            truncated = True
            break
    return {"rows": rows, "truncated": truncated}


def _extract_xlsx(archive: zipfile.ZipFile) -> dict:
    shared = _xlsx_shared(archive)
    names: list[str] = []
    # Sheet order comes from the workbook part; part files are picked in numeric order.
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        for sheet in workbook.iter(f"{_SPREADSHEET}sheet"):
            names.append(_text(sheet.get("name")) or f"Sheet {len(names) + 1}")
    except (KeyError, ElementTree.ParseError):
        pass
    candidates = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
        key=lambda name: int(re.search(r"(\d+)", name).group(1)))
    sheets = []
    for index, part in enumerate(candidates[: DOC_LIMITS["sheets"]]):
        with archive.open(part) as handle:
            rows = _xlsx_rows(handle, shared)
        sheets.append({"name": names[index] if index < len(names) else f"Sheet {index + 1}",
                       "rows": rows["rows"], "truncated": rows["truncated"]})
    if not sheets:
        return {"kind": "xlsx", "sheets": [{"name": "Sheet 1", "rows": [], "truncated": False}]}
    return {"kind": "xlsx", "sheets": sheets}


_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _extract_pptx(archive: zipfile.ZipFile) -> dict:
    slides = []
    parts = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=lambda name: int(re.search(r"(\d+)", name).group(1)))
    for part in parts[: DOC_LIMITS["slides"]]:
        root = ElementTree.fromstring(archive.read(part))
        title = ""
        points: list[str] = []
        for shape in root.iter(f"{_P}sp"):
            paragraphs: list[str] = []
            for paragraph in shape.iter(f"{_A}p"):
                text = _text("".join(node.text or "" for node in paragraph.iter(f"{_A}t")))
                if text:
                    paragraphs.append(text)
            if not paragraphs:
                continue
            placeholder = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
            kind = placeholder.get("type", "") if placeholder is not None else ""
            if (kind in {"title", "ctrTitle"} or (not title and kind == "")) and not title:
                title = paragraphs[0]
                points.extend(paragraphs[1:])
            else:
                points.extend(paragraphs)
        slides.append({"title": _clamp(title, 200), "points": [_clamp(point, 300) for point in points[:20]]})
    return {"kind": "pptx", "slides": slides}


# --- pdf (best effort, no third-party dependencies) ---------------------------

def _pdf_stream_text(raw: bytes) -> str:
    text_parts: list[str] = []
    for match in _TEXT_RUN_RE.finditer(raw):
        chunk = match.group(1) or match.group(2)
        try:
            literal = chunk.decode("latin-1", "replace")
        except Exception:  # noqa: BLE001 - bounded best effort
            continue
        literal = literal.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
        if literal.strip():
            text_parts.append(literal)
    return " ".join(text_parts)


def _extract_pdf(data: bytes) -> dict:
    pages: list[str] = []
    for start in re.finditer(rb"stream\r?\n", data):
        offset = start.end()
        stop = data.find(b"endstream", offset)
        if stop <= offset:
            continue
        chunk = data[offset:stop]
        try:
            chunk = zlib.decompress(chunk)
        except zlib.error:
            pass  # uncompressed stream: use it as-is
        if b"BT" in chunk and (b"Tj" in chunk or b"TJ" in chunk):
            pages.append(_pdf_stream_text(chunk))
        if sum(len(page) for page in pages) >= DOC_LIMITS["pdf_chars"]:
            break
    text = " ".join(part.strip() for part in pages if part.strip())
    text = _clamp(text, DOC_LIMITS["pdf_chars"])
    merged = []
    current: list[str] = []
    for part in pages:
        current.append(part)
        if len(" ".join(current)) > 2400:
            merged.append(_clamp(" ".join(current), 3000))
            current = []
    if current:
        merged.append(_clamp(" ".join(current), 3000))
    return {"kind": "pdf", "text": text, "pages": merged[:40], "truncated": len(pages) > 40 or len(text) >= DOC_LIMITS["pdf_chars"]}


# --- plain delimited tables ----------------------------------------------------

def _extract_table(path: Path, suffix: str) -> dict:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t" if suffix == ".tsv" else ","))
    rows = rows[: DOC_LIMITS["sheet_rows"]]
    rows = [[_clamp(cell, 300) for cell in row[: DOC_LIMITS["sheet_cols"]]] for row in rows]
    has_header = bool(rows) and all(cell and not _looks_numeric(cell) for cell in rows[0]) and len(rows) > 1
    return {"kind": "table", "rows": rows, "delimiter": "tab" if suffix == ".tsv" else "comma",
            "has_header": has_header, "columns": rows[0] if has_header else []}


def _looks_numeric(value: str) -> bool:
    try:
        float(value.replace(",", ""))
        return True
    except ValueError:
        return False


# --- entry points --------------------------------------------------------------

KINDS = {".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".pptx": "pptx",
         ".pdf": "pdf", ".csv": "table", ".tsv": "table", ".htm": "html", ".html": "html"}


def kind_for(path: str | Path) -> str | None:
    return KINDS.get(Path(path).suffix.lower())


def extract(path: str | Path) -> dict:
    """Parse a workspace document into displayable structure. Raises on unusable input."""
    target = Path(path)
    suffix = target.suffix.lower()
    kind = KINDS.get(suffix)
    if not kind:
        raise ValueError(f"No document reader for {suffix}")
    if kind == "table":
        return _extract_table(target, suffix)
    if suffix in {".doc", ".xls", ".ppt", ".odt", ".ods", ".odp"}:
        raise ValueError("Legacy Office formats are not supported yet; save as .docx, .xlsx or .pptx.")
    if kind == "pdf":
        data = target.read_bytes()
        if len(data) > 24 * 1024 * 1024:
            raise ValueError("PDF is too large to read in the workspace viewer.")
        return _extract_pdf(data)
    with zipfile.ZipFile(target) as archive:
        if kind == "docx":
            return _extract_docx(archive)
        if kind == "xlsx":
            return _extract_xlsx(archive)
        return _extract_pptx(archive)


def attachment_text(path: str | Path, mime: str = "") -> str:
    """A bounded plain-text view of an attachment for the model's context."""
    target = Path(path)
    suffix = target.suffix.lower()
    plain = {".txt", ".md", ".csv", ".tsv", ".log", ".json", ".xml", ".html", ".htm",
             ".py", ".js", ".ts", ".rs", ".go", ".java", ".c", ".cpp", ".h", ".css",
             ".yaml", ".yml", ".toml", ".sql", ".sh", ".ini", ".cfg"}
    try:
        if suffix in plain:
            text = target.read_text(encoding="utf-8", errors="replace")
        else:
            document = extract(target)
            text = _document_text(document)
    except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        return f"(could not read {target.name}: {error})"
    text = text.strip()
    if len(text) > DOC_LIMITS["text_chars"]:
        text = text[: DOC_LIMITS["text_chars"]].rstrip() + "\n(…truncated)"
    return text or f"({target.name} had no readable text)"


def _document_text(document: dict) -> str:
    kind = document.get("kind")
    if kind == "pdf":
        return str(document.get("text", ""))
    if kind == "docx":
        lines = []
        for block in document.get("blocks", []):
            if block["type"] == "table":
                for row in block["rows"]:
                    lines.append(" | ".join(row))
            else:
                prefix = "#" * int(block.get("level", 1)) + " " if block["type"] == "h" else ("- " if block["type"] == "li" else "")
                if block.get("text"):
                    lines.append(prefix + block["text"])
        return "\n".join(lines)
    if kind == "xlsx":
        lines = []
        for sheet in document.get("sheets", []):
            lines.append(f"[{sheet['name']}]")
            lines.extend("\t".join(row) for row in sheet["rows"])
        return "\n".join(lines)
    if kind == "pptx":
        lines = []
        for index, slide in enumerate(document.get("slides", []), start=1):
            lines.append(f"[Slide {index}] {slide['title']}")
            lines.extend(f"- {point}" for point in slide["points"])
        return "\n".join(lines)
    if kind == "table":
        return "\n".join("\t".join(row) for row in document.get("rows", []))
    return ""
