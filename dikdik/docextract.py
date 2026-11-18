"""Extract readable structure from documents with the standard library only.

The workspace and chat need to *show* documents, not just store them: a .docx
should read like a document, a spreadsheet like a grid, and the model needs the
full text of a thesis or the full grid of a survey, not a 40-column sample.
Everything here is offline and dependency-free so the wheel keeps shipping
unchanged; when `pypdf` happens to be installed it is used for PDFs, otherwise a
built-in reader decodes text with the PDF's own font maps.

`extract(path, name=None, full=False)` returns a JSON-friendly dict. Shapes:

  docx  -> {"kind": "docx", "blocks": [...], "truncated": bool}
           block: heading {"type":"h","level","text"} | para {"type":"p","text"} |
           list item {"type":"li","text"} | table {"type":"table","rows":[[...]]}
  xlsx  -> {"kind": "xlsx", "sheets": [{"name", "rows": [[cell]], "truncated"}]}
  pptx  -> {"kind": "pptx", "slides": [{"title", "points", "notes"}]}
  pdf   -> {"kind": "pdf", "text", "pages": [...]}
  table -> {"kind": "table", "rows", "delimiter", "has_header", "columns"}
  text  -> {"kind": "text", "text"}             (rtf, html, epub, plain text)

`name` is the file's real name when the bytes live under an opaque path (chat
attachments are stored by id), so the reader is chosen from the true extension.
`full=True` lifts the viewer's display bounds to the much larger model bounds.

`attachment_text(path, mime, name)` gives the model a bounded plain-text view of
any attachment; `document_text(path, name)` gives the whole text for paging.
"""

from __future__ import annotations

import csv
import datetime
import html
import io
import re
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

# Viewer bounds: what the Files pane renders.
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
# Model bounds: what read_document and analysis see. Large enough for a thesis or
# a survey export; still bounded so a pathological file cannot exhaust memory.
FULL_LIMITS = {
    "blocks": 40_000,
    "block_chars": 20_000,
    "table_rows": 5_000,
    "sheet_rows": 100_000,
    "sheet_cols": 400,
    "sheets": 60,
    "slides": 1_000,
    "pdf_chars": 3_000_000,
    "text_chars": 3_000_000,
}
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
_CELL_RE = re.compile(r"([A-Z]+)(\d+)")


def _text(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _clamp(text: str, limit: int) -> str:
    text = _text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _col_index(reference: str) -> int:
    match = _CELL_RE.match(reference or "")
    letters = match.group(1) if match else ""
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return max(index - 1, 0)


# --- docx (Office Open XML) ----------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def _docx_runs(node) -> str:
    """Paragraph text with tabs and line breaks kept, field codes and deleted text skipped."""
    parts: list[str] = []
    for child in node.iter():
        tag = child.tag
        if tag == f"{_W}t" or tag == f"{_MATH}t":  # body text and equation text
            parts.append(child.text or "")
        elif tag == f"{_W}tab":
            parts.append("\t")
        elif tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
        elif tag == f"{_W}noBreakHyphen":
            parts.append("-")
    return "".join(parts)


def _docx_paragraph(paragraph, limits, styles: dict) -> dict:
    text = _docx_runs(paragraph)
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    style_id = style.get(f"{_W}val", "") if style is not None else ""
    name = styles.get(style_id, style_id)
    outline = paragraph.find(f"{_W}pPr/{_W}outlineLvl")
    match = re.fullmatch(r"(?:[Hh]eading|Kop|Titre|berschrift)\s*(\d)", name) or re.fullmatch(r"[Hh]eading\s*(\d)", style_id)
    clean = _clamp(text, limits["block_chars"])
    if match:
        return {"type": "h", "level": int(match.group(1)), "text": clean}
    if name.lower() in {"title"}:
        return {"type": "h", "level": 1, "text": clean}
    if outline is not None and (outline.get(f"{_W}val") or "").isdigit() and int(outline.get(f"{_W}val")) < 6 and clean:
        return {"type": "h", "level": int(outline.get(f"{_W}val")) + 1, "text": clean}
    if paragraph.find(f"{_W}pPr/{_W}numPr") is not None or "list" in name.lower():
        return {"type": "li", "text": clean}
    if name.lower() in {"caption"}:
        return {"type": "p", "caption": True, "text": clean}
    return {"type": "p", "text": clean}


def _docx_table(table, limits) -> dict:
    rows = []
    for row in table.findall(f"{_W}tr"):
        cells = []
        for cell in row.findall(f"{_W}tc"):
            paragraphs = [_text(_docx_runs(p)) for p in cell.iter(f"{_W}p")]
            cells.append(_clamp(" ".join(p for p in paragraphs if p), max(300, limits["block_chars"] // 10)))
        rows.append(cells)
        if len(rows) >= limits["table_rows"]:
            break
    return {"type": "table", "rows": rows}


def _docx_styles(archive: zipfile.ZipFile) -> dict:
    """Style id -> display name, so localized or renamed heading styles still read as headings."""
    try:
        root = ElementTree.fromstring(archive.read("word/styles.xml"))
    except (KeyError, ElementTree.ParseError):
        return {}
    names = {}
    for style in root.iter(f"{_W}style"):
        name = style.find(f"{_W}name")
        if name is not None:
            names[style.get(f"{_W}styleId", "")] = name.get(f"{_W}val", "")
    return names


def _extract_docx(archive: zipfile.ZipFile, limits) -> dict:
    root = ElementTree.fromstring(archive.read("word/document.xml"))
    styles = _docx_styles(archive)
    body = root.find(f"{_W}body")
    blocks: list[dict] = []
    truncated = False

    def walk(container):
        nonlocal truncated
        for child in container:
            if len(blocks) >= limits["blocks"]:
                truncated = True
                return
            if child.tag == f"{_W}p":
                blocks.append(_docx_paragraph(child, limits, styles))
            elif child.tag == f"{_W}tbl":
                blocks.append(_docx_table(child, limits))
            elif child.tag in (f"{_W}sdt", f"{_W}sdtContent", f"{_W}customXml"):
                walk(child)  # content controls (tables of contents, cover pages) hold real paragraphs
    if body is not None:
        walk(body)
    kept = [block for block in blocks if (block.get("text") or "").strip() or block.get("rows")]
    notes = _docx_notes(archive, limits)
    result = {"kind": "docx", "blocks": kept, "truncated": truncated or any(
        len(block.get("rows", [])) >= limits["table_rows"] for block in kept)}
    if notes:
        result["notes"] = notes
    return result


def _docx_notes(archive: zipfile.ZipFile, limits) -> list[str]:
    notes = []
    for part in ("word/footnotes.xml", "word/endnotes.xml"):
        if part not in archive.namelist():
            continue
        try:
            root = ElementTree.fromstring(archive.read(part))
        except ElementTree.ParseError:
            continue
        for note in root:
            text = _text(_docx_runs(note))
            if text:
                notes.append(_clamp(text, limits["block_chars"]))
    return notes[:2000]


# --- xlsx ------------------------------------------------------------------------

_SPREADSHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DATE_FORMATS = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}


def _xlsx_shared(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    shared = []
    for item in root.findall(f"{_SPREADSHEET}si"):
        # Phonetic runs (rPh) are reading aids, not cell text.
        phonetic = {id(t) for r in item.findall(f"{_SPREADSHEET}rPh") for t in r.iter(f"{_SPREADSHEET}t")}
        texts = [node.text or "" for node in item.iter(f"{_SPREADSHEET}t") if id(node) not in phonetic]
        shared.append("".join(texts))
    return shared


def _xlsx_date_styles(archive: zipfile.ZipFile) -> tuple[set[int], set[int]]:
    """Cell style indexes whose number format is a date, and those that also carry a time."""
    try:
        root = ElementTree.fromstring(archive.read("xl/styles.xml"))
    except (KeyError, ElementTree.ParseError):
        return set(), set()
    custom = {}
    for fmt in root.iter(f"{_SPREADSHEET}numFmt"):
        try:
            custom[int(fmt.get("numFmtId", "-1"))] = fmt.get("formatCode", "")
        except ValueError:
            continue
    dates, times = set(), set()
    xfs = root.find(f"{_SPREADSHEET}cellXfs")
    for index, xf in enumerate(xfs.findall(f"{_SPREADSHEET}xf") if xfs is not None else []):
        try:
            fmt_id = int(xf.get("numFmtId", "0"))
        except ValueError:
            continue
        code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", custom.get(fmt_id, "")).lower()
        if fmt_id in _DATE_FORMATS or (code and re.search(r"[dy]", code) and not re.search(r"[#0]", code)):
            dates.add(index)
            if fmt_id in {22, 45, 46, 47} or re.search(r"h|ss", code):
                times.add(index)
    return dates, times


def _excel_date(raw: str, with_time: bool) -> str:
    try:
        serial = float(raw)
    except ValueError:
        return raw
    if not 0 < serial < 2_958_466:
        return raw
    moment = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=serial)
    if with_time and serial % 1:
        return moment.strftime("%Y-%m-%d %H:%M:%S")
    return moment.strftime("%Y-%m-%d")


def _number_text(raw: str) -> str:
    """Excel stores 0.1 as 0.10000000000000001; show what the user typed."""
    try:
        value = float(raw)
    except ValueError:
        return raw
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(round(value, 10))


def _xlsx_rows(data: bytes, shared: list[str], dates: set[int], times: set[int], limits) -> dict:
    rows: list[list[str]] = []
    truncated = False
    root = ElementTree.fromstring(data)
    last_row = 0
    for row in root.iter(f"{_SPREADSHEET}row"):
        # Keep blank rows that sit between data rows, so row numbers still line up.
        try:
            number = int(row.get("r") or last_row + 1)
        except ValueError:
            number = last_row + 1
        while rows and last_row + 1 < number and len(rows) < limits["sheet_rows"]:
            rows.append([])
            last_row += 1
        last_row = number
        cells: dict[int, str] = {}
        for position, cell in enumerate(row.findall(f"{_SPREADSHEET}c")):
            reference = cell.get("r")
            index = _col_index(reference) if reference else position
            if index >= limits["sheet_cols"]:
                continue
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
                elif kind in ("", "n") and raw:
                    style = int(cell.get("s", "0") or 0)
                    value = _excel_date(raw, style in times) if style in dates else _number_text(raw)
                else:
                    value = raw
            cells[index] = value
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(column, "") for column in range(width)])
        elif rows:
            rows.append([])
        if len(rows) >= limits["sheet_rows"]:
            truncated = True
            break
    while rows and not rows[-1]:
        rows.pop()
    return {"rows": rows, "truncated": truncated}


def xlsx_sheet_parts(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(sheet name, part path) in workbook order, resolved through the workbook relationships."""
    names: list[tuple[str, str]] = []
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        targets = {}
        try:
            rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for rel in rels.iter(f"{_REL}Relationship"):
                target = rel.get("Target", "")
                target = target.lstrip("/") if target.startswith("/") else "xl/" + target
                targets[rel.get("Id")] = re.sub(r"/[^/]+/\.\./", "/", target)
        except (KeyError, ElementTree.ParseError):
            pass
        for sheet in workbook.iter(f"{_SPREADSHEET}sheet"):
            part = targets.get(sheet.get(f"{_OFFICE_REL}id"))
            if part and part in archive.namelist() and "worksheets/" in part:
                names.append((_text(sheet.get("name")) or f"Sheet {len(names) + 1}", part))
    except (KeyError, ElementTree.ParseError):
        pass
    if names:
        return names
    parts = sorted((name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
                   key=lambda name: int(re.search(r"(\d+)", name).group(1)))
    return [(f"Sheet {index + 1}", part) for index, part in enumerate(parts)]


def _extract_xlsx(archive: zipfile.ZipFile, limits) -> dict:
    shared = _xlsx_shared(archive)
    dates, times = _xlsx_date_styles(archive)
    sheets = []
    for name, part in xlsx_sheet_parts(archive)[: limits["sheets"]]:
        rows = _xlsx_rows(archive.read(part), shared, dates, times, limits)
        sheets.append({"name": name, "rows": rows["rows"], "truncated": rows["truncated"]})
    if not sheets:
        return {"kind": "xlsx", "sheets": [{"name": "Sheet 1", "rows": [], "truncated": False}]}
    return {"kind": "xlsx", "sheets": sheets}


# --- pptx ------------------------------------------------------------------------

_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _extract_pptx(archive: zipfile.ZipFile, limits) -> dict:
    slides = []
    parts = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=lambda name: int(re.search(r"(\d+)", name).group(1)))
    for part in parts[: limits["slides"]]:
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
        for frame in root.iter(f"{_A}tbl"):
            for row in frame.iter(f"{_A}tr"):
                cells = [_text("".join(n.text or "" for n in cell.iter(f"{_A}t"))) for cell in row.iter(f"{_A}tc")]
                if any(cells):
                    points.append(" | ".join(cells))
        number = re.search(r"(\d+)", part).group(1)
        notes_part = f"ppt/notesSlides/notesSlide{number}.xml"
        notes = ""
        if notes_part in archive.namelist():
            try:
                notes_root = ElementTree.fromstring(archive.read(notes_part))
                notes = _text(" ".join(node.text or "" for node in notes_root.iter(f"{_A}t")))
                notes = re.sub(r"\s*\d+\s*$", "", notes)  # the slide-number placeholder
            except ElementTree.ParseError:
                notes = ""
        slides.append({"title": _clamp(title, 400), "points": [_clamp(point, limits["block_chars"]) for point in points[:200]],
                       **({"notes": _clamp(notes, limits["block_chars"])} if notes else {})})
    return {"kind": "pptx", "slides": slides}


# --- OpenDocument (odt / ods / odp) ------------------------------------------------

_ODF_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODF_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODF_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_ODF_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def _odf_text(node) -> str:
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in node:
        if child.tag == f"{_ODF_TEXT}s":
            parts.append(" " * int(child.get(f"{_ODF_TEXT}c", "1") or 1))
        elif child.tag == f"{_ODF_TEXT}tab":
            parts.append("\t")
        elif child.tag == f"{_ODF_TEXT}line-break":
            parts.append("\n")
        elif child.tag not in (f"{_ODF_TEXT}note",):
            parts.append(_odf_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _odf_table_rows(table, limits) -> list[list[str]]:
    rows = []
    for row in table.iter(f"{_ODF_TABLE}table-row"):
        repeat = min(int(row.get(f"{_ODF_TABLE}number-rows-repeated", "1") or 1), 1000)
        cells: list[str] = []
        for cell in row:
            if cell.tag not in (f"{_ODF_TABLE}table-cell", f"{_ODF_TABLE}covered-table-cell"):
                continue
            span = min(int(cell.get(f"{_ODF_TABLE}number-columns-repeated", "1") or 1), limits["sheet_cols"])
            value = cell.get(f"{_ODF_OFFICE}date-value") or cell.get(f"{_ODF_OFFICE}value")
            text = " ".join(_text(_odf_text(p)) for p in cell.iter(f"{_ODF_TEXT}p"))
            cells.extend([text or (value or "")] * span)
            if len(cells) >= limits["sheet_cols"]:
                break
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            if rows:
                rows.extend([[]] * min(repeat, 1))
            continue
        rows.extend([cells[: limits["sheet_cols"]]] * repeat)
        if len(rows) >= limits["sheet_rows"]:
            break
    while rows and not rows[-1]:
        rows.pop()
    return rows[: limits["sheet_rows"]]


def _extract_odf(archive: zipfile.ZipFile, suffix: str, limits) -> dict:
    root = ElementTree.fromstring(archive.read("content.xml"))
    body = root.find(f"{_ODF_OFFICE}body")
    if suffix == ".ods":
        sheets = []
        for table in root.iter(f"{_ODF_TABLE}table"):
            sheets.append({"name": table.get(f"{_ODF_TABLE}name") or f"Sheet {len(sheets) + 1}",
                           "rows": _odf_table_rows(table, limits), "truncated": False})
            if len(sheets) >= limits["sheets"]:
                break
        return {"kind": "xlsx", "sheets": sheets or [{"name": "Sheet 1", "rows": [], "truncated": False}]}
    if suffix == ".odp":
        slides = []
        for page in root.iter(f"{_ODF_DRAW}page"):
            lines = [_text(_odf_text(p)) for p in page.iter(f"{_ODF_TEXT}p")]
            lines = [line for line in lines if line]
            slides.append({"title": lines[0] if lines else page.get(f"{_ODF_DRAW}name", ""), "points": lines[1:200]})
        return {"kind": "pptx", "slides": slides[: limits["slides"]]}
    blocks: list[dict] = []

    def walk(node):
        for child in node:
            if len(blocks) >= limits["blocks"]:
                return
            if child.tag == f"{_ODF_TEXT}h":
                level = int(child.get(f"{_ODF_TEXT}outline-level", "1") or 1)
                blocks.append({"type": "h", "level": min(level, 6), "text": _clamp(_odf_text(child), limits["block_chars"])})
            elif child.tag == f"{_ODF_TEXT}p":
                blocks.append({"type": "p", "text": _clamp(_odf_text(child), limits["block_chars"])})
            elif child.tag == f"{_ODF_TEXT}list":
                for item in child.iter(f"{_ODF_TEXT}list-item"):
                    text = " ".join(_text(_odf_text(p)) for p in item.findall(f"{_ODF_TEXT}p"))
                    blocks.append({"type": "li", "text": _clamp(text, limits["block_chars"])})
            elif child.tag == f"{_ODF_TABLE}table":
                blocks.append({"type": "table", "rows": _odf_table_rows(child, {**limits, "sheet_rows": limits["table_rows"]})})
            else:
                walk(child)
    walk(body if body is not None else root)
    kept = [block for block in blocks if (block.get("text") or "").strip() or block.get("rows")]
    return {"kind": "docx", "blocks": kept, "truncated": len(blocks) >= limits["blocks"]}


# --- pdf --------------------------------------------------------------------------
#
# A small reader for the PDFs people actually send: Word/LibreOffice/LaTeX exports
# with compressed content streams, object streams and Type0 fonts whose text is
# glyph ids mapped back to Unicode through a ToUnicode CMap. Scanned PDFs (images
# only) have no text layer; the result says so instead of returning noise.

_OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
_REF_RE = re.compile(rb"(\d+)\s+(\d+)\s+R\b")
_PDF_TOKEN = re.compile(
    rb"\((?:[^()\\]|\\.|\((?:[^()\\]|\\.)*\))*\)"   # literal string (one nesting level)
    rb"|<[0-9A-Fa-f\s]*>"                              # hex string
    rb"|\[|\]"                                           # array delimiters
    rb"|/[^\s/\[\]()<>{}%]+"                             # name
    rb"|[+-]?(?:\d+\.?\d*|\.\d+)"                        # number
    rb"|[A-Za-z'\"*]+"                                   # operator
    rb"|%[^\r\n]*", re.S)


class _Pdf:
    def __init__(self, data: bytes):
        self.data = data
        self.objects: dict[int, bytes] = {}
        self._parse()

    def _parse(self) -> None:
        data = self.data
        for match in _OBJ_RE.finditer(data):
            number = int(match.group(1))
            end = data.find(b"endobj", match.end())
            if end < 0:
                continue
            self.objects[number] = data[match.end():end]
        # Objects packed inside compressed object streams (PDF 1.5+).
        for number, body in list(self.objects.items()):
            if b"/ObjStm" not in body[:600]:
                continue
            stream = self.stream(body)
            head = self._dict_int(body, b"/First")
            count = self._dict_int(body, b"/N")
            if stream is None or head is None or count is None:
                continue
            numbers = stream[:head].split()
            for index in range(min(count, len(numbers) // 2)):
                try:
                    obj, offset = int(numbers[2 * index]), int(numbers[2 * index + 1])
                    stop = int(numbers[2 * index + 3]) if 2 * index + 3 < len(numbers) else len(stream) - head
                except (ValueError, IndexError):
                    continue
                self.objects.setdefault(obj, stream[head + offset: head + stop])

    @staticmethod
    def _dict_int(body: bytes, key: bytes) -> int | None:
        match = re.search(re.escape(key) + rb"\s+(\d+)", body)
        return int(match.group(1)) if match else None

    def resolve(self, value: bytes) -> bytes:
        match = _REF_RE.fullmatch(value.strip())
        return self.objects.get(int(match.group(1)), b"") if match else value

    def stream(self, body: bytes) -> bytes | None:
        start = re.search(rb"stream\r?\n", body)
        if not start:
            return None
        raw = body[start.end():]
        end = raw.rfind(b"endstream")
        raw = raw[:end] if end >= 0 else raw
        header = body[:start.start()]
        length = re.search(rb"/Length\s+(\d+)(\s+\d+\s+R)?", header)
        if length and not length.group(2):
            raw = raw[: int(length.group(1))]
        if b"/FlateDecode" in header or b"/Fl " in header or b"/Fl]" in header:
            try:
                raw = zlib.decompressobj().decompress(raw)
            except zlib.error:
                return None
        elif b"/Filter" in header and b"/FlateDecode" not in header:
            return None  # images and exotic filters carry no text we can read
        return raw

    def value(self, body: bytes, key: bytes) -> bytes:
        """The raw value after `key` in a dictionary: a reference, a name, a nested dict or an array."""
        position = body.find(key)
        if position < 0:
            return b""
        rest = body[position + len(key):].lstrip()
        ref = re.match(rb"\d+\s+\d+\s+R", rest)
        if ref:
            return ref.group(0)
        if rest.startswith(b"<<"):
            depth, index = 0, 0
            while index < len(rest) - 1:
                if rest[index:index + 2] == b"<<":
                    depth += 1
                    index += 2
                    continue
                if rest[index:index + 2] == b">>":
                    depth -= 1
                    index += 2
                    if depth == 0:
                        return rest[:index]
                    continue
                index += 1
            return rest
        if rest.startswith(b"["):
            end = rest.find(b"]")
            return rest[: end + 1] if end >= 0 else rest
        match = re.match(rb"/?[^\s/\[\]<>()]+", rest)
        return match.group(0) if match else b""

    def pages(self) -> list[bytes]:
        """Page dictionaries in document order, following the page tree from the catalog."""
        catalog = next((body for body in self.objects.values() if re.search(rb"/Type\s*/Catalog", body)), None)
        ordered: list[bytes] = []
        seen: set[int] = set()

        def visit(ref: bytes, inherited: bytes, depth: int) -> None:
            match = _REF_RE.fullmatch(ref.strip())
            if not match or depth > 40:
                return
            number = int(match.group(1))
            if number in seen:
                return
            seen.add(number)
            body = self.objects.get(number, b"")
            resources = self.value(body, b"/Resources") or inherited
            if re.search(rb"/Type\s*/Pages", body):
                for kid in _REF_RE.finditer(self.value(body, b"/Kids")):
                    visit(kid.group(0), resources, depth + 1)
            elif re.search(rb"/Type\s*/Page\b", body):
                ordered.append(body if self.value(body, b"/Resources") else body + b"/Resources " + resources)
        if catalog:
            visit(self.value(catalog, b"/Pages"), b"", 0)
        if not ordered:
            ordered = [body for body in self.objects.values() if re.search(rb"/Type\s*/Page\b", body)]
        return ordered

    def fonts(self, page: bytes) -> dict[bytes, dict]:
        resources = self.resolve(self.value(page, b"/Resources"))
        fonts = self.resolve(self.value(resources, b"/Font"))
        mapping = {}
        for match in re.finditer(rb"/([^\s/\[\]<>()]+)\s+(\d+\s+\d+\s+R)", fonts):
            font = self.objects.get(int(_REF_RE.fullmatch(match.group(2)).group(1)), b"")
            cmap_ref = self.value(font, b"/ToUnicode")
            cmap: dict[int, str] = {}
            width = 1
            if cmap_ref and _REF_RE.fullmatch(cmap_ref.strip()):
                stream = self.stream(self.resolve(cmap_ref))
                if stream:
                    cmap, width = _parse_cmap(stream)
            if b"/Type0" in font and width == 1:
                width = 2
            mapping[b"/" + match.group(1)] = {"cmap": cmap, "width": width}
        return mapping

    def contents(self, page: bytes) -> bytes:
        value = self.value(page, b"/Contents")
        refs = [m.group(0) for m in _REF_RE.finditer(value)]
        streams = []
        for ref in refs:
            body = self.resolve(ref)
            if body.lstrip().startswith(b"["):  # an indirect array of streams
                streams.extend(self.stream(self.resolve(r.group(0))) or b"" for r in _REF_RE.finditer(body))
            else:
                streams.append(self.stream(body) or b"")
        return b"\n".join(streams)


def _parse_cmap(stream: bytes) -> tuple[dict[int, str], int]:
    mapping: dict[int, str] = {}
    width = 1
    for block in re.finditer(rb"begincodespacerange(.*?)endcodespacerange", stream, re.S):
        first = re.search(rb"<([0-9A-Fa-f]+)>", block.group(1))
        if first:
            width = max(1, len(first.group(1)) // 2)

    def utf16(hexes: bytes) -> str:
        raw = bytes.fromhex(hexes.decode())
        try:
            return raw.decode("utf-16-be")
        except UnicodeDecodeError:
            return ""
    for block in re.finditer(rb"beginbfchar(.*?)endbfchar", stream, re.S):
        for source, target in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block.group(1)):
            mapping[int(source, 16)] = utf16(target)
    for block in re.finditer(rb"beginbfrange(.*?)endbfrange", stream, re.S):
        for low, high, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block.group(1)):
            start, stop = int(low, 16), int(high, 16)
            if stop - start > 65535:
                continue
            if rest.startswith(b"["):
                for offset, target in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", rest)):
                    mapping[start + offset] = utf16(target)
            else:
                base = bytes.fromhex(rest[1:-1].decode())
                for offset in range(stop - start + 1):
                    value = int.from_bytes(base, "big") + offset
                    try:
                        mapping[start + offset] = value.to_bytes(len(base), "big").decode("utf-16-be")
                    except (UnicodeDecodeError, OverflowError):
                        continue
    return mapping, width


def _pdf_literal(token: bytes) -> bytes:
    body = token[1:-1]
    out = bytearray()
    index = 0
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while index < len(body):
        char = body[index]
        if char == 0x5C and index + 1 < len(body):  # backslash
            nxt = body[index + 1]
            if nxt in escapes:
                out.append(escapes[nxt])
                index += 2
            elif 0x30 <= nxt <= 0x37:
                digits = re.match(rb"[0-7]{1,3}", body[index + 1:index + 4]).group(0)
                out.append(int(digits, 8) & 0xFF)
                index += 1 + len(digits)
            elif nxt in (0x0A, 0x0D):
                index += 2
            else:
                out.append(nxt)
                index += 2
        else:
            out.append(char)
            index += 1
    return bytes(out)


def _pdf_decode(raw: bytes, font: dict | None) -> str:
    if not font or not font["cmap"]:
        if font and font["width"] == 2:
            try:
                return raw.decode("utf-16-be", "ignore")
            except UnicodeDecodeError:
                return ""
        if raw.startswith(b"\xfe\xff"):
            return raw[2:].decode("utf-16-be", "ignore")
        return raw.decode("cp1252", "replace")
    width, cmap = font["width"], font["cmap"]
    out = []
    for index in range(0, len(raw) - width + 1, width):
        code = int.from_bytes(raw[index:index + width], "big")
        out.append(cmap.get(code, "" if width > 1 else chr(code) if 32 <= code < 127 else ""))
    return "".join(out)


def _pdf_page_text(content: bytes, fonts: dict) -> str:
    out: list[str] = []
    font = None
    operands: list[bytes] = []
    in_array = False
    array: list[bytes] = []
    last_y = None
    for match in _PDF_TOKEN.finditer(content):
        token = match.group(0)
        if token.startswith(b"%"):
            continue
        if token == b"[":
            in_array, array = True, []
            continue
        if token == b"]":
            in_array = False
            operands.append(b"\x00ARRAY")
            continue
        if in_array:
            array.append(token)
            continue
        if token[:1] in b"(<" or token[:1] == b"/" or re.fullmatch(rb"[+-]?(?:\d+\.?\d*|\.\d+)", token):
            operands.append(token)
            continue
        op = token
        if op == b"Tf" and len(operands) >= 2:
            font = fonts.get(operands[-2])
        elif op in (b"Tj", b"'", b'"') and operands:
            if op != b"Tj":
                out.append("\n")
            out.append(_pdf_decode(_pdf_string(operands[-1]), font))
        elif op == b"TJ":
            for item in array:
                if item[:1] in b"(<":
                    out.append(_pdf_decode(_pdf_string(item), font))
                else:
                    try:
                        if float(item) < -180:
                            out.append(" ")  # a wide negative kern is a word gap
                    except ValueError:
                        pass
        elif op in (b"Td", b"TD") and len(operands) >= 2:
            try:
                dy = float(operands[-1])
            except ValueError:
                dy = 0
            out.append("\n" if abs(dy) > 0.1 else " ")
        elif op == b"Tm" and len(operands) >= 6:
            try:
                y = float(operands[-1])
            except ValueError:
                y = last_y
            out.append("\n" if last_y is not None and y is not None and abs(y - last_y) > 1 else " ")
            last_y = y
        elif op == b"T*":
            out.append("\n")
        elif op == b"ET":
            out.append("\n")
        operands = []
    text = "".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf_string(token: bytes) -> bytes:
    if token.startswith(b"("):
        return _pdf_literal(token)
    hexes = re.sub(rb"\s", b"", token[1:-1])
    if len(hexes) % 2:
        hexes += b"0"
    try:
        return bytes.fromhex(hexes.decode())
    except ValueError:
        return b""


def _extract_pdf_pypdf(data: bytes, limits) -> list[str] | None:
    try:
        from pypdf import PdfReader  # optional: used only when the user already has it
    except Exception:  # noqa: BLE001 - absent or broken install falls back to the built-in reader
        return None
    try:
        reader = PdfReader(io.BytesIO(data))
        pages, total = [], 0
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
            total += len(text)
            if total >= limits["pdf_chars"]:
                break
        return pages
    except Exception:  # noqa: BLE001 - malformed PDFs fall back too
        return None


def _extract_pdf(data: bytes, limits=DOC_LIMITS) -> dict:
    pages = _extract_pdf_pypdf(data, limits)
    if pages is None:
        pages = []
        try:
            pdf = _Pdf(data)
            total = 0
            for page in pdf.pages():
                text = _pdf_page_text(pdf.contents(page), pdf.fonts(page))
                pages.append(text)
                total += len(text)
                if total >= limits["pdf_chars"]:
                    break
        except Exception:  # noqa: BLE001 - a damaged PDF yields what was read so far
            pass
        if not any(page.strip() for page in pages):
            pages = _pdf_loose_text(data)
    pages = [re.sub(r"\n{3,}", "\n\n", page).strip() for page in pages]
    text = "\n\n".join(page for page in pages if page)
    truncated = len(text) > limits["pdf_chars"]
    text = text[: limits["pdf_chars"]]
    view_pages = [_clamp(page, 3000) for page in pages[:40]] if limits is DOC_LIMITS else pages
    result = {"kind": "pdf", "text": text, "pages": view_pages, "page_count": len(pages), "truncated": truncated or (limits is DOC_LIMITS and len(pages) > 40)}
    if not text.strip():
        result["note"] = "This PDF has no text layer (it is probably scanned images); its text cannot be read without OCR."
    return result


def _pdf_loose_text(data: bytes) -> list[str]:
    """Last resort for PDFs whose structure did not parse: scan every stream for text operators."""
    pages = []
    for start in re.finditer(rb"stream\r?\n", data):
        offset = start.end()
        stop = data.find(b"endstream", offset)
        if stop <= offset:
            continue
        chunk = data[offset:stop]
        try:
            chunk = zlib.decompressobj().decompress(chunk)
        except zlib.error:
            pass
        if b"BT" in chunk and (b"Tj" in chunk or b"TJ" in chunk):
            pages.append(_pdf_page_text(chunk, {}))
    return pages


# --- rtf / html / epub / plain text ------------------------------------------------

def _rtf_text(raw: str) -> str:
    """Strip RTF control words; keeps paragraph breaks and \\'hh / \\uN characters."""
    out: list[str] = []
    stack: list[bool] = []
    skip = False
    index = 0
    destinations = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "themedata",
                    "colorschememapping", "latentstyles", "datastore", "xmlnstbl", "listtable", "listoverridetable",
                    "rsidtbl", "generator", "object", "fldinst"}
    while index < len(raw):
        char = raw[index]
        if char == "{":
            stack.append(skip)
            index += 1
        elif char == "}":
            skip = stack.pop() if stack else False
            index += 1
        elif char == "\\":
            match = re.match(r"\\([a-z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-z])", raw[index:])
            if not match:
                index += 1
                continue
            index += len(match.group(0))
            word, arg, hexed, symbol = match.groups()
            if hexed and not skip:
                out.append(bytes([int(hexed, 16)]).decode("cp1252", "replace"))
            elif symbol:
                if symbol == "*":
                    skip = True
                elif not skip and symbol in "\\{}":
                    out.append(symbol)
                elif not skip and symbol == "~":
                    out.append(" ")
            elif word in destinations:
                skip = True
            elif not skip:
                if word in ("par", "line", "row"):
                    out.append("\n")
                elif word in ("tab", "cell"):
                    out.append("\t")
                elif word == "u" and arg:
                    out.append(chr(int(arg) % 65536))
                    if index < len(raw) and raw[index] == "?":
                        index += 1
        else:
            if not skip and char not in "\r\n":
                out.append(char)
            index += 1
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def html_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|section|article|table|blockquote)>", "\n", markup)
    markup = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", markup)
    markup = re.sub(r"(?i)<li[^>]*>", "- ", markup)
    markup = re.sub(r"(?i)</t[dh]>", "\t", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _epub_text(archive: zipfile.ZipFile) -> str:
    parts = []
    for name in sorted(archive.namelist()):
        if name.lower().endswith((".xhtml", ".html", ".htm")):
            parts.append(html_text(archive.read(name).decode("utf-8", "replace")))
    return "\n\n".join(part for part in parts if part)


def _decode_text(raw: bytes) -> str:
    for bom, codec in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            return raw[len(bom):].decode(codec, "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


# --- plain delimited tables ----------------------------------------------------

def _extract_table(path: Path, suffix: str, limits=DOC_LIMITS) -> dict:
    text = _decode_text(path.read_bytes()[: 64 * 1024 * 1024])
    delimiter = "\t" if suffix == ".tsv" else ","
    if suffix == ".csv":
        try:
            delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = []
    for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
        rows.append([_clamp(cell, max(300, limits["block_chars"])) for cell in row[: limits["sheet_cols"]]])
        if len(rows) >= limits["sheet_rows"]:
            break
    has_header = bool(rows) and all(cell and not _looks_numeric(cell) for cell in rows[0]) and len(rows) > 1
    return {"kind": "table", "rows": rows, "delimiter": {"\t": "tab", ";": "semicolon", "|": "pipe"}.get(delimiter, "comma"),
            "has_header": has_header, "columns": rows[0] if has_header else []}


def _looks_numeric(value: str) -> bool:
    try:
        float(value.replace(",", ""))
        return True
    except ValueError:
        return False


# --- entry points --------------------------------------------------------------

# Documents the Files viewer renders as structure.
KINDS = {".docx": "docx", ".docm": "docx", ".dotx": "docx", ".odt": "docx",
         ".xlsx": "xlsx", ".xlsm": "xlsx", ".xltx": "xlsx", ".ods": "xlsx", ".xls": "xlsx",
         ".pptx": "pptx", ".pptm": "pptx", ".ppsx": "pptx", ".odp": "pptx",
         ".pdf": "pdf", ".csv": "table", ".tsv": "table", ".htm": "html", ".html": "html"}
# Further formats the model can read as text.
TEXT_KINDS = {".rtf": "rtf", ".epub": "epub"}
PLAIN_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".log", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".toml",
                  ".ini", ".cfg", ".conf", ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".c", ".cpp",
                  ".h", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".css", ".scss", ".sql", ".sh", ".ps1", ".bat",
                  ".r", ".rmd", ".m", ".tex", ".bib", ".srt", ".vtt", ".ipynb", ".sav.txt", ".do", ".sps"}
LEGACY = {".doc": ".docx", ".ppt": ".pptx"}


def kind_for(path: str | Path) -> str | None:
    return KINDS.get(Path(path).suffix.lower())


def readable(name: str | Path) -> bool:
    """True when read_document can turn this file into text."""
    suffix = Path(name).suffix.lower()
    return suffix in KINDS or suffix in TEXT_KINDS or suffix in PLAIN_SUFFIXES


def extract(path: str | Path, name: str | None = None, full: bool = False) -> dict:
    """Parse a document into displayable structure. Raises ValueError on unusable input."""
    target = Path(path)
    suffix = Path(name or target.name).suffix.lower()
    limits = FULL_LIMITS if full else DOC_LIMITS
    if suffix in LEGACY:
        raise ValueError(f"{suffix} is the old binary Office format; open it in Office or LibreOffice and save it as {LEGACY[suffix]}.")
    kind = KINDS.get(suffix) or TEXT_KINDS.get(suffix) or ("text" if suffix in PLAIN_SUFFIXES else None)
    if not kind:
        raise ValueError(f"No document reader for {suffix or 'files without an extension'}")
    if target.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError("The document is larger than 64 MB.")
    if kind == "table":
        return _extract_table(target, suffix, limits)
    if suffix == ".xls":
        from . import xls  # Excel 97-2003: OLE2 + BIFF8
        return xls.read(target, limits)
    if kind == "pdf":
        data = target.read_bytes()
        if not full and len(data) > 24 * 1024 * 1024:
            raise ValueError("PDF is too large to read in the workspace viewer.")
        return _extract_pdf(data, limits)
    if kind == "html":
        return {"kind": "text", "text": html_text(_decode_text(target.read_bytes()))}
    if kind == "rtf":
        return {"kind": "text", "text": _rtf_text(target.read_bytes().decode("latin-1"))}
    if kind == "text":
        return {"kind": "text", "text": _decode_text(target.read_bytes())}
    with zipfile.ZipFile(target) as archive:
        if kind == "epub":
            return {"kind": "text", "text": _epub_text(archive)}
        if suffix in {".odt", ".ods", ".odp"}:
            return _extract_odf(archive, suffix, limits)
        if kind == "docx":
            return _extract_docx(archive, limits)
        if kind == "xlsx":
            return _extract_xlsx(archive, limits)
        return _extract_pptx(archive, limits)


def document_text(path: str | Path, name: str | None = None) -> str:
    """The whole readable text of a document, Markdown-flavoured (headings, lists, pipe tables)."""
    return _document_text(extract(path, name, full=True), markdown=True)


def attachment_text(path: str | Path, mime: str = "", name: str | None = None, limit: int | None = None) -> str:
    """A bounded plain-text view of an attachment for the model's context."""
    target = Path(path)
    label = name or target.name
    limit = limit or DOC_LIMITS["text_chars"]
    try:
        text = document_text(target, label)
    except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as error:
        return f"(could not read {label}: {error})"
    text = text.strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n(…truncated)"
    return text or f"({label} had no readable text)"


def _cell_md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _md_table(rows: list[list[str]]) -> list[str]:
    rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if not rows:
        return []
    width = max(len(row) for row in rows)
    padded = [[_cell_md(row[i]) if i < len(row) else "" for i in range(width)] for row in rows]
    lines = ["| " + " | ".join(padded[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(row) + " |" for row in padded[1:]]
    return lines


def _document_text(document: dict, markdown: bool = False) -> str:
    kind = document.get("kind")
    if kind == "pdf":
        pages = document.get("pages") or []
        if markdown and len(pages) > 1:
            return "\n\n".join(f"[Page {index}]\n{page}" for index, page in enumerate(pages, start=1) if page.strip()) \
                or document.get("note", "")
        return str(document.get("text", "")) or document.get("note", "")
    if kind == "text":
        return str(document.get("text", ""))
    if kind == "docx":
        lines = []
        for block in document.get("blocks", []):
            if block["type"] == "table":
                if markdown:
                    lines.append("")
                    lines.extend(_md_table(block["rows"]))
                    lines.append("")
                else:
                    lines.extend(" | ".join(row) for row in block["rows"])
            else:
                prefix = "#" * int(block.get("level", 1)) + " " if block["type"] == "h" else ("- " if block["type"] == "li" else "")
                if block.get("text"):
                    lines.append(("\n" if markdown and block["type"] == "h" else "") + prefix + block["text"])
        if document.get("notes"):
            lines.append("\n## Footnotes and endnotes" if markdown else "Notes:")
            lines.extend(f"- {note}" for note in document["notes"])
        return "\n".join(lines)
    if kind == "xlsx":
        lines = []
        for sheet in document.get("sheets", []):
            rows = sheet["rows"]
            width = max((len(row) for row in rows), default=0)
            lines.append(f"[Sheet: {sheet['name']} — {max(len(rows) - 1, 0)} data rows × {width} columns]")
            if markdown:
                lines.extend(_md_table(rows))
            else:
                lines.extend("\t".join(row) for row in rows)
            lines.append("")
        return "\n".join(lines)
    if kind == "pptx":
        lines = []
        for index, slide in enumerate(document.get("slides", []), start=1):
            lines.append(f"[Slide {index}] {slide['title']}")
            lines.extend(f"- {point}" for point in slide["points"])
            if slide.get("notes"):
                lines.append(f"  Speaker notes: {slide['notes']}")
        return "\n".join(lines)
    if kind == "table":
        rows = document.get("rows", [])
        return "\n".join(_md_table(rows)) if markdown else "\n".join("\t".join(row) for row in rows)
    return ""


# --- rich preview (the in-app viewer): formatting, list markers, pictures, charts -----------

_DML_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_DML_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
MEDIA_PART = re.compile(r"(word|ppt|xl)/media/[^/]+\.(png|jpe?g|gif|bmp|webp|svg|tiff?|emf|wmf)", re.I)


def _rels(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Relationship id -> target part (or external URL) for one package part."""
    folder, name = part.rsplit("/", 1)
    try:
        root = ElementTree.fromstring(archive.read(f"{folder}/_rels/{name}.rels"))
    except (KeyError, ElementTree.ParseError):
        return {}
    out = {}
    for rel in root.iter(f"{_REL}Relationship"):
        target = rel.get("Target", "")
        if rel.get("TargetMode") == "External":
            out[rel.get("Id")] = target
            continue
        path = target.lstrip("/") if target.startswith("/") else f"{folder}/{target}"
        while "/../" in path:
            path = re.sub(r"[^/]+/\.\./", "", path, count=1)
        out[rel.get("Id")] = path
    return out


def chart_spec(archive: zipfile.ZipFile, part: str) -> dict | None:
    """A chart part's type, title and cached data: enough to draw it without Office."""
    try:
        root = ElementTree.fromstring(archive.read(part))
    except (KeyError, ElementTree.ParseError):
        return None
    plot = root.find(f".//{_DML_C}plotArea")
    if plot is None:
        return None
    kinds = {"barChart": "column", "bar3DChart": "column", "lineChart": "line", "line3DChart": "line", "pieChart": "pie",
             "pie3DChart": "pie", "doughnutChart": "doughnut", "areaChart": "line", "scatterChart": "line"}
    chart_node, kind = None, "column"
    for child in plot:
        local = child.tag.split("}")[-1]
        if local in kinds:
            chart_node, kind = child, kinds[local]
            break
    if chart_node is None:
        return None
    direction = chart_node.find(f"{_DML_C}barDir")
    if kind == "column" and direction is not None and direction.get("val") == "bar":
        kind = "bar"
    title = " ".join(t.text or "" for t in root.findall(f"{_DML_C}chart/{_DML_C}title//{_DML_A}t")).strip()

    def points(node) -> list:
        if node is None:
            return []
        values: dict[int, str] = {}
        for pt in node.iter(f"{_DML_C}pt"):
            v = pt.find(f"{_DML_C}v")
            values[int(pt.get("idx", "0"))] = v.text if v is not None and v.text is not None else ""
        count = node.find(f".//{_DML_C}ptCount")
        size = int(count.get("val")) if count is not None and (count.get("val") or "").isdigit() else (max(values) + 1 if values else 0)
        return [values.get(i, "") for i in range(min(size, 500))]
    series, categories = [], []
    for ser in chart_node.findall(f"{_DML_C}ser")[:12]:
        name = " ".join(v.text or "" for v in ser.findall(f"{_DML_C}tx//{_DML_C}v")).strip()
        cats = points(ser.find(f"{_DML_C}cat")) or points(ser.find(f"{_DML_C}xVal"))
        vals = points(ser.find(f"{_DML_C}val")) or points(ser.find(f"{_DML_C}yVal"))
        numbers = []
        for value in vals:
            try:
                numbers.append(float(value))
            except ValueError:
                numbers.append(None)
        categories = categories or cats
        series.append({"name": name or f"Series {len(series) + 1}", "values": numbers})
    if not series:
        return None
    return {"type": kind, "title": title, "categories": categories or [str(i + 1) for i in range(len(series[0]["values"]))],
            "series": series}


def _numbering_formats(archive: zipfile.ZipFile) -> dict[tuple[str, int], str]:
    """(numId, level) -> numFmt ("bullet", "decimal", "lowerLetter", ...)."""
    try:
        root = ElementTree.fromstring(archive.read("word/numbering.xml"))
    except (KeyError, ElementTree.ParseError):
        return {}
    abstract = {}
    for node in root.findall(f"{_W}abstractNum"):
        levels = {}
        for lvl in node.findall(f"{_W}lvl"):
            fmt = lvl.find(f"{_W}numFmt")
            levels[int(lvl.get(f"{_W}ilvl", "0"))] = fmt.get(f"{_W}val") if fmt is not None else "decimal"
        abstract[node.get(f"{_W}abstractNumId")] = levels
    out = {}
    for num in root.findall(f"{_W}num"):
        ref = num.find(f"{_W}abstractNumId")
        levels = abstract.get(ref.get(f"{_W}val") if ref is not None else "", {})
        for level, fmt in levels.items():
            out[(num.get(f"{_W}numId"), level)] = fmt
    return out


def _marker(fmt: str, count: int) -> str:
    if fmt == "bullet":
        return "•"
    if fmt in ("lowerLetter", "upperLetter"):
        letter = chr(96 + (count - 1) % 26 + 1)
        return (letter.upper() if fmt == "upperLetter" else letter) + ")"
    if fmt in ("lowerRoman", "upperRoman"):
        numerals = [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
        out, value = "", count
        for amount, symbol in numerals:
            while value >= amount:
                out += symbol
                value -= amount
        return (out.upper() if fmt == "upperRoman" else out) + "."
    return f"{count}."


def _run_props(run) -> dict:
    props = run.find(f"{_W}rPr")
    style = {}
    if props is None:
        return style
    for tag, key in (("b", "b"), ("i", "i"), ("u", "u"), ("strike", "s")):
        node = props.find(f"{_W}{tag}")
        if node is not None and node.get(f"{_W}val", "true") not in ("0", "false", "none"):
            style[key] = 1
    align = props.find(f"{_W}vertAlign")
    if align is not None and align.get(f"{_W}val") in ("superscript", "subscript"):
        style["sup" if align.get(f"{_W}val") == "superscript" else "sub"] = 1
    return style


def _rich_paragraph(paragraph, rels: dict, archive, charts: dict) -> tuple[list, list]:
    """Formatted runs of a paragraph, and the pictures/charts drawn inside it."""
    runs, drawings = [], []

    def add(text: str, style: dict, href: str = "") -> None:
        if not text:
            return
        item = {"t": text, **style, **({"href": href} if href else {})}
        if runs and {k: v for k, v in runs[-1].items() if k != "t"} == {k: v for k, v in item.items() if k != "t"}:
            runs[-1]["t"] += text
        else:
            runs.append(item)

    def visit_run(run, href=""):
        style = _run_props(run)
        for child in run:
            tag = child.tag
            if tag == f"{_W}t":
                add(child.text or "", style, href)
            elif tag == f"{_W}tab":
                add("\t", style, href)
            elif tag in (f"{_W}br", f"{_W}cr"):
                add("\n", style, href)
            elif tag in (f"{_W}drawing", f"{_W}pict"):
                for blip in child.iter(f"{_DML_A}blip"):
                    target = rels.get(blip.get(_R_EMBED, ""))
                    if target and MEDIA_PART.fullmatch(target):
                        drawings.append({"type": "image", "part": target})
                for chart in child.iter(f"{_DML_C}chart"):
                    target = rels.get(chart.get(_R_ID, ""))
                    if target:
                        if target not in charts:
                            charts[target] = chart_spec(archive, target)
                        if charts[target]:
                            drawings.append({"type": "chart", "chart": charts[target]})
                for image in child.iter("{urn:schemas-microsoft-com:vml}imagedata"):
                    target = rels.get(image.get(_R_ID, ""))
                    if target and MEDIA_PART.fullmatch(target):
                        drawings.append({"type": "image", "part": target})
    for child in paragraph:
        if child.tag == f"{_W}r":
            visit_run(child)
        elif child.tag == f"{_W}hyperlink":
            href = rels.get(child.get(_R_ID, ""), "")
            href = href if re.match(r"https?://|mailto:", href) else ""
            for run in child.iter(f"{_W}r"):
                visit_run(run, href)
        elif child.tag in (f"{_W}ins", f"{_W}smartTag", f"{_W}sdt", f"{_W}fldSimple"):
            for run in child.iter(f"{_W}r"):
                visit_run(run)
        elif child.tag in (f"{_MATH}oMath", f"{_MATH}oMathPara"):
            add("".join(node.text or "" for node in child.iter(f"{_MATH}t")), {"i": 1})  # an equation, as its text
    return runs, drawings


def docx_preview(archive: zipfile.ZipFile, limits=None) -> dict:
    """A .docx for the viewer: the text blocks plus formatting, list markers, pictures and charts."""
    limits = limits or {**DOC_LIMITS, "blocks": 6000, "block_chars": 20_000, "table_rows": 1000}
    root = ElementTree.fromstring(archive.read("word/document.xml"))
    styles = _docx_styles(archive)
    rels = _rels(archive, "word/document.xml")
    formats = _numbering_formats(archive)
    counters: dict[tuple[str, int], int] = {}
    charts: dict = {}
    blocks: list[dict] = []
    pictures = 0

    def walk(container):
        nonlocal pictures
        for child in container:
            if len(blocks) >= limits["blocks"]:
                return
            if child.tag == f"{_W}p":
                block = _docx_paragraph(child, limits, styles)
                style_node = child.find(f"{_W}pPr/{_W}pStyle")
                style_name = styles.get(style_node.get(f"{_W}val", ""), "").lower() if style_node is not None else ""
                if style_name in {"title", "subtitle", "caption", "quote", "intense quote"}:
                    block["style"] = style_name.replace("intense ", "")
                runs, drawings = _rich_paragraph(child, rels, archive, charts)
                if runs and any(len(run) > 1 for run in runs):
                    block["runs"] = runs
                justify = child.find(f"{_W}pPr/{_W}jc")
                if justify is not None and justify.get(f"{_W}val") in ("center", "right", "both", "end"):
                    block["align"] = {"both": "justify", "end": "right"}.get(justify.get(f"{_W}val"), justify.get(f"{_W}val"))
                numbering = child.find(f"{_W}pPr/{_W}numPr")
                if numbering is not None:
                    num = numbering.find(f"{_W}numId")
                    lvl = numbering.find(f"{_W}ilvl")
                    key = (num.get(f"{_W}val") if num is not None else "0", int(lvl.get(f"{_W}val", "0")) if lvl is not None else 0)
                    for other in [k for k in counters if k[0] == key[0] and k[1] > key[1]]:
                        counters.pop(other)  # a parent item restarts its children
                    counters[key] = counters.get(key, 0) + 1
                    block["type"], block["level"] = "li", key[1]
                    block["marker"] = _marker(formats.get(key, "decimal"), counters[key])
                if (child.find(f"{_W}r/{_W}br[@{_W}type='page']") is not None or child.find(f"{_W}pPr/{_W}pageBreakBefore") is not None)                         and blocks and blocks[-1]["type"] != "pagebreak":
                    blocks.append({"type": "pagebreak"})
                if (block.get("text") or "").strip():
                    blocks.append(block)
                for drawing in drawings:
                    pictures += drawing["type"] == "image"
                    blocks.append(drawing)
            elif child.tag == f"{_W}tbl":
                blocks.append(_docx_table(child, limits))
            elif child.tag in (f"{_W}sdt", f"{_W}sdtContent", f"{_W}customXml"):
                walk(child)
    body = root.find(f"{_W}body")
    if body is not None:
        walk(body)
    while blocks and blocks[0]["type"] == "pagebreak":
        blocks.pop(0)
    result = {"kind": "docx", "blocks": blocks, "truncated": len(blocks) >= limits["blocks"], "rich": True}
    notes = _docx_notes(archive, limits)
    if notes:
        result["notes"] = notes
    return result


def pptx_pictures(archive: zipfile.ZipFile) -> list[list[str]]:
    """Picture parts used on each slide, in slide order."""
    parts = sorted((name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                   key=lambda name: int(re.search(r"(\d+)", name).group(1)))
    out = []
    for part in parts:
        rels = _rels(archive, part)
        try:
            root = ElementTree.fromstring(archive.read(part))
        except ElementTree.ParseError:
            out.append([])
            continue
        out.append([rels[b.get(_R_EMBED)] for b in root.iter(f"{_DML_A}blip")
                    if rels.get(b.get(_R_EMBED, "")) and MEDIA_PART.fullmatch(rels[b.get(_R_EMBED)])][:8])
    return out


def preview(path: str | Path, name: str | None = None) -> dict:
    """What the in-app viewer shows: rich Word pages, full grids, slides with pictures, PDF/text as usual."""
    target = Path(path)
    suffix = Path(name or target.name).suffix.lower()
    if suffix in {".docx", ".docm", ".dotx"}:
        with zipfile.ZipFile(target) as archive:
            return docx_preview(archive)
    limits = {**DOC_LIMITS, "sheet_rows": 3000, "sheet_cols": 200, "sheets": 30, "slides": 300, "table_rows": 1000,
              "blocks": 6000, "block_chars": 20_000}
    if suffix in {".pptx", ".pptm", ".ppsx"}:
        with zipfile.ZipFile(target) as archive:
            result = _extract_pptx(archive, limits)
            for slide, pictures in zip(result["slides"], pptx_pictures(archive)):
                if pictures:
                    slide["images"] = pictures
            return result
    if suffix in {".xlsx", ".xlsm", ".xltx"}:
        with zipfile.ZipFile(target) as archive:
            return _extract_xlsx(archive, limits)
    if suffix == ".xls":
        from . import xls
        return xls.read(target, limits)
    if suffix in {".csv", ".tsv"}:
        return _extract_table(target, suffix, limits)
    return extract(target, name=name)


def media(path: str | Path, part: str) -> tuple[bytes, str]:
    """One embedded picture of an Office file, for the viewer. Only media parts are served."""
    if not isinstance(part, str) or not MEDIA_PART.fullmatch(part):
        raise KeyError(part)
    with zipfile.ZipFile(Path(path)) as archive:
        if part not in archive.namelist():
            raise KeyError(part)
        data = archive.read(part)
    kind = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
            "webp": "image/webp", "svg": "image/svg+xml"}.get(part.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    return data, kind
