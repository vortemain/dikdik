"""Read any document and write real Office files, stdlib only.

Two agent tools:

  read_document    the full text of a .docx/.pdf/.xlsx/.pptx/.odt/.rtf/.epub/.html/...
                   file, paged (offset/max_chars), with an outline of its headings and
                   an optional `find` that returns every passage containing a phrase.
  create_document  write a deliverable the user can open: .docx from Markdown (headings,
                   paragraphs with bold/italic/code/links, bullet and numbered lists,
                   pipe tables, block quotes, code, images, native editable charts from a
                   ```chart block, captions, a table of contents, page numbers, page
                   breaks), .xlsx from sheets of rows, or .md/.txt/.csv/.html/.json text.

Word files are built by hand from WordprocessingML so nothing has to be installed;
each chart carries an embedded workbook, so the user can right-click → Edit Data.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import re
import struct
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import docextract, omml

READ_LIMIT = 40_000

READ_DOCUMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "read_document",
        "description": (
            "Read the text of a document in the working folder: Word (.docx/.odt/.rtf), PDF, Excel (.xlsx/.ods/.csv, "
            "shown as Markdown tables), PowerPoint (.pptx/.odp), EPUB, HTML or any text file. Long files are paged: "
            "the first call returns the outline (all headings) and the first part; continue with offset=next_offset "
            "until done. Use find to jump to every passage that mentions a phrase (e.g. 'Specific Objectives')."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the document, relative to the working folder."},
                "offset": {"type": "integer", "description": "Character offset to start from (default 0)."},
                "max_chars": {"type": "integer", "description": f"How much to return (default and maximum {READ_LIMIT})."},
                "find": {"type": "string", "description": "Optional phrase: return the passages around each match instead."},
            },
            "required": ["path"],
        },
    },
}

CREATE_DOCUMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "create_document",
        "description": (
            "Create a file the user can download and open: a Word document (.docx) from Markdown, an Excel workbook "
            "(.xlsx) from sheets, or a text file (.md, .txt, .csv, .html, .json). For .docx write normal Markdown: "
            "# headings, paragraphs, **bold**, *italic*, lists, pipe tables, > quotes, ![Figure 1: caption](image.png), "
            "'Table 4.1: ...' / 'Figure 4.1: ...' lines become captions, a line '\\pagebreak' starts a new page, LaTeX "
            "maths ($...$ inline, $$...$$ or \\[...\\] or \\begin{align} on their own lines) becomes native, editable Word "
            "equations, and a "
            "fenced ```chart block holding JSON {\"type\": \"column\"|\"bar\"|\"line\"|\"pie\", \"title\": ..., "
            "\"categories\": [...], \"series\": [{\"name\": ..., \"values\": [...]}], \"y_title\": ...} becomes a "
            "native editable Word chart. Options: title page, table of contents, academic style (Times New Roman 12, "
            "1.5 spacing, justified) or modern. Overwrites the file if it exists; write the whole document in one call "
            "(or use append to add sections to an existing .md draft first)."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Where to save, e.g. 'Chapter_4_and_5.docx'."},
                "content": {"type": "string", "description": "Markdown for .docx/.md/.html; text for .txt/.csv/.json."},
                "sheets": {"type": "array", "items": {"type": "object"},
                           "description": ".xlsx only: [{\"name\": \"Results\", \"rows\": [[header...], [values...]]}]."},
                "title": {"type": "string", "description": ".docx: document title (metadata, and the title page if requested)."},
                "subtitle": {"type": "string", "description": ".docx title page: lines under the title (author, institution, date)."},
                "title_page": {"type": "boolean", "description": ".docx: start with a title page (default false)."},
                "toc": {"type": "boolean", "description": ".docx: insert a table of contents after the title page (default false)."},
                "style": {"type": "string", "enum": ["academic", "modern"], "description": ".docx look (default academic)."},
                "chapter_breaks": {"type": "boolean", "description": ".docx: start every level-1 heading on a new page (default true for academic)."},
                "append": {"type": "boolean", "description": "Text formats only: append to the file instead of replacing it."},
            },
            "required": ["path"],
        },
    },
}


# --- reading ----------------------------------------------------------------------

def _safe_target(root: Path, path: str, must_exist: bool = True) -> Path:
    if not isinstance(path, str) or not path.strip() or len(path) > 500:
        raise ValueError("Choose a file path")
    relative = Path(path.strip().replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or any(part.startswith(".") for part in relative.parts):
        raise ValueError("Path must stay inside the working folder (no '..', no hidden folders)")
    from .repository import EXCLUDED, SECRET, _ignore_rules, _ignored  # the same policy as the other file tools
    import fnmatch
    base = Path(root).resolve()
    current, rules = base, []
    for index, part in enumerate(relative.parts):
        if part in EXCLUDED or any(fnmatch.fnmatch(part.lower(), pattern) for pattern in SECRET):
            raise ValueError("Path is excluded by workspace policy (build output or a secrets file)")
        rules = _ignore_rules(current, base, rules)
        if _ignored("/".join(relative.parts[:index + 1]), rules):
            raise ValueError("Path is ignored by workspace policy")
        current = current / part
        if current.is_symlink():
            raise ValueError("Links are not followed")
    target = (base / relative).resolve()
    if base != target and base not in target.parents:
        raise ValueError("Path must stay inside the working folder")
    if must_exist and not target.is_file():
        raise ValueError(f"{path} does not exist. List the folder to find the exact file name.")
    return target


def read(root: Path, arguments: dict, cache: dict | None = None) -> dict:
    path = arguments.get("path")
    target = _safe_target(root, path)
    key = (str(target), target.stat().st_mtime_ns, target.stat().st_size)
    text = cache.get(key) if cache is not None else None
    if text is None:
        text = docextract.document_text(target)
        if target.suffix.lower() == ".pdf" and len(text.strip()) < 200:
            text = _ocr_scanned_pdf(target) or text
        if cache is not None:
            cache.clear()  # one document at a time is plenty; keeps memory flat
            cache[key] = text
    total = len(text)
    phrase = arguments.get("find")
    if isinstance(phrase, str) and phrase.strip():
        needle = phrase.strip().lower()
        lowered = text.lower()
        passages, start = [], 0
        while len(passages) < 12:
            hit = lowered.find(needle, start)
            if hit < 0:
                break
            begin = max(0, text.rfind("\n", 0, max(0, hit - 600)) + 1)
            end = min(total, hit + len(needle) + 1600)
            passages.append({"offset": begin, "text": text[begin:end]})
            start = end
        return {"path": path, "total_chars": total, "find": phrase, "matches": len(passages), "passages": passages,
                **({} if passages else {"note": "No match. Try a shorter phrase or read the outline."})}
    try:
        offset = max(0, int(arguments.get("offset") or 0))
        limit = min(READ_LIMIT, max(1000, int(arguments.get("max_chars") or READ_LIMIT)))
    except (TypeError, ValueError):
        raise ValueError("offset and max_chars must be numbers") from None
    end = min(total, offset + limit)
    if end < total:  # end on a line boundary so tables and sentences are not cut mid-way
        newline = text.rfind("\n", offset + limit // 2, end)
        end = newline + 1 if newline > 0 else end
    result = {"path": path, "total_chars": total, "offset": offset, "text": text[offset:end],
              "next_offset": end if end < total else None,
              "progress": f"{round(100 * end / total) if total else 100}% read"}
    if offset == 0:
        outline = [line.strip() for line in text.splitlines() if re.match(r"#{1,4} \S", line.strip())]
        if outline:
            result["outline"] = outline[:400]
        pictures = picture_count(target)
        if pictures:
            result["pictures"] = (f"{pictures} embedded picture(s) (figures, charts, photos). Their content is not in this text; "
                                  "read one with read_image(path, index).")
        sheets = re.findall(r"^\[Sheet: .+\]$", text, re.M)
        if sheets:
            result["sheets"] = sheets
            result["tip"] = "Use the statistics tool on this file for counts, percentages and tests instead of counting by hand."
    if not text.strip():
        result["note"] = "No readable text was found in this document."
    return result


def _ocr_scanned_pdf(target: Path, pages: int = 40) -> str:
    """A scanned PDF has pictures of pages, not text: read them with this computer's OCR."""
    from . import vision
    if vision.ocr_engine() is None:
        return ""
    parts = []
    for number, (_, data) in enumerate(vision.document_images(target, limit=pages), start=1):
        found = vision.ocr(data)
        if found and found.get("text"):
            parts.append(f"[Scanned page image {number} — text read by {found['engine']}]\n{found['text'].replace(chr(13), '')}")
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n(OCR text from a scanned PDF: check names and numbers that matter against the original.)"


def picture_count(target: Path) -> int:
    from . import vision
    try:
        return len(vision.document_images(target, limit=200)) if target.suffix.lower() in {".docx", ".pptx", ".xlsx", ".odt", ".odp"} else 0
    except (OSError, zipfile.BadZipFile):
        return 0


# --- writing: shared ------------------------------------------------------------------

def create(root: Path, arguments: dict) -> dict:
    path = arguments.get("path")
    target = _safe_target(root, path, must_exist=False)
    suffix = target.suffix.lower()
    target.parent.mkdir(parents=True, exist_ok=True)
    content = arguments.get("content")
    if suffix == ".docx":
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Write the document's Markdown in content")
        data, stats = build_docx(content, root=Path(root), title=arguments.get("title") or "",
                                 subtitle=arguments.get("subtitle") or "", title_page=arguments.get("title_page") is True,
                                 toc=arguments.get("toc") is True, style=arguments.get("style") or "academic",
                                 chapter_breaks=arguments.get("chapter_breaks"))
        target.write_bytes(data)
        return {"saved": path, "bytes": len(data), **stats,
                "note": "Saved. Word updates the table of contents when the user opens it and agrees to update fields."
                        if arguments.get("toc") else "Saved."}
    if suffix == ".xlsx":
        sheets = arguments.get("sheets")
        if not sheets and isinstance(content, str) and content.strip():
            sheets = [{"name": "Sheet1", "rows": list(csv.reader(io.StringIO(content)))}]
        if not isinstance(sheets, list) or not sheets:
            raise ValueError("Give sheets: [{name, rows}] for an .xlsx file")
        data = build_xlsx(sheets)
        target.write_bytes(data)
        return {"saved": path, "bytes": len(data), "sheets": [str(s.get("name", "")) for s in sheets if isinstance(s, dict)]}
    if suffix in {".md", ".markdown", ".txt", ".csv", ".tsv", ".html", ".htm", ".json", ".py", ".r", ".tex", ".bib", ".xml", ".yaml", ".yml", ".sql"}:
        if not isinstance(content, str):
            raise ValueError("Give the file's content")
        if suffix == ".json":
            json.loads(content)  # refuse invalid JSON instead of saving a broken file
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("Content is larger than 8 MB")
        mode = "a" if arguments.get("append") is True and target.exists() else "w"
        with target.open(mode, encoding="utf-8", newline="") as handle:
            handle.write(content)
        return {"saved": path, "bytes": target.stat().st_size, "appended": mode == "a"}
    raise ValueError("create_document writes .docx, .xlsx, .md, .txt, .csv, .html, .json and similar text files")


# --- Markdown → WordprocessingML ---------------------------------------------------------

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMU_PER_INCH = 914400
_INLINE = re.compile(r"(\$\$[^$\n]+?\$\$|\\\(.+?\\\)|(?<![\\$\w])\$(?=[^\s$\d.,])(?:[^$\n\\]|\\.)+?(?<=[^\s\\])\$(?![\w$])|(?<![\\$\w])\$(?=\d)(?:[^$\n\\]|\\.)*?[\\^_{}=+*/<>|](?:[^$\n\\]|\\.)*?(?<=[^\s\\])\$(?![\w$])|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|__[^_]+__|(?<![\w*])\*[^*\s][^*]*\*(?!\w)|(?<![\w_])_[^_\s][^_]*_(?![\w])"
                     r"|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\^[^^\s]+\^|~[^~\s]+~)")


def _inline_math(piece: str) -> str | None:
    """The LaTeX inside an inline-math token ($…$, $$…$$ or \\(…\\)), else None."""
    if piece.startswith("$$") and piece.endswith("$$") and len(piece) > 4:
        return piece[2:-2]
    if piece.startswith("\\(") and piece.endswith("\\)") and len(piece) > 4:
        return piece[2:-2]
    if piece.startswith("$") and piece.endswith("$") and len(piece) > 2 and not piece.startswith("$$"):
        return piece[1:-1]
    return None


_DISPLAY_OPEN = re.compile(r"^(\$\$|\\\[|\\begin\{(equation|align|gather|multline|alignat|eqnarray|flalign)\*?\})")


def _x(text: str) -> str:
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text)), {'"': "&quot;"})


class _Doc:
    def __init__(self, root: Path, style: str):
        self.root = root
        self.academic = style != "modern"
        self.body: list[str] = []
        self.rels: list[tuple[str, str, str, str]] = []  # id, type, target, mode
        self.media: dict[str, bytes] = {}
        self.charts: list[tuple[str, bytes, bytes]] = []  # chart xml, workbook
        self.numbering: list[tuple[int, int]] = []  # (num id, abstract id)
        self.drawing_id = 0
        self.counts = {"headings": 0, "tables": 0, "charts": 0, "images": 0, "paragraphs": 0}
        self.warnings: list[str] = []

    def rel(self, kind: str, target: str, external: bool = False) -> str:
        rid = f"rId{len(self.rels) + 10}"
        self.rels.append((rid, f"{REL}/{kind}", target, "External" if external else ""))
        return rid

    def new_list(self, ordered: bool) -> int:
        num = len(self.numbering) + 1
        self.numbering.append((num, 2 if ordered else 1))
        return num

    # inline runs
    def runs(self, text: str, base: str = "") -> str:
        out = []
        for piece in _INLINE.split(text):
            if not piece:
                continue
            math = _inline_math(piece)
            if math is not None:
                out.append(omml.inline(math))  # a real Word equation
                continue
            props, value, link = base, piece, None
            if piece.startswith("***") and piece.endswith("***") and len(piece) > 6:
                props, value = base + "<w:b/><w:i/>", piece[3:-3]
            elif (piece.startswith("**") and piece.endswith("**") or piece.startswith("__") and piece.endswith("__")) and len(piece) > 4:
                props, value = base + "<w:b/>", piece[2:-2]
            elif (piece.startswith("*") and piece.endswith("*") or piece.startswith("_") and piece.endswith("_")) and len(piece) > 2:
                props, value = base + "<w:i/>", piece[1:-1]
            elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
                props, value = base + '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="20"/>', piece[1:-1]
            elif piece.startswith("^") and piece.endswith("^") and len(piece) > 2:
                props, value = base + '<w:vertAlign w:val="superscript"/>', piece[1:-1]
            elif piece.startswith("~") and piece.endswith("~") and len(piece) > 2:
                props, value = base + '<w:vertAlign w:val="subscript"/>', piece[1:-1]
            elif piece.startswith("[") and (m := re.fullmatch(r"\[([^\]]+)\]\(([^)\s]+)\)", piece)):
                value, link = m.group(1), m.group(2)
            if link and re.match(r"https?://|mailto:", link):
                rid = self.rel("hyperlink", _x(link), external=True)
                out.append(f'<w:hyperlink r:id="{rid}"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/>{props}</w:rPr>'
                           f'<w:t xml:space="preserve">{_x(value)}</w:t></w:r></w:hyperlink>')
                continue
            out.append(self.run(value, props))
        return "".join(out)

    @staticmethod
    def run(text: str, props: str = "") -> str:
        parts = str(text).split("\n")
        inner = '<w:br/>'.join(f'<w:t xml:space="preserve">{_x(p)}</w:t>' for p in parts)
        inner = inner.replace("\t", '</w:t><w:tab/><w:t xml:space="preserve">')
        return f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}{inner}</w:r>"

    def paragraph(self, text: str, style: str = "", extra: str = "", run_props: str = "") -> None:
        ppr = (f'<w:pStyle w:val="{style}"/>' if style else "") + extra
        self.body.append(f"<w:p>{f'<w:pPr>{ppr}</w:pPr>' if ppr else ''}{self.runs(text, run_props)}</w:p>")
        self.counts["paragraphs"] += 1

    def page_break(self) -> None:
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    def heading(self, level: int, text: str, page_break: bool) -> None:
        extra = '<w:pageBreakBefore/>' if page_break else ""
        self.paragraph(text.strip(), f"Heading{min(level, 6)}", extra)
        self.counts["headings"] += 1

    def table(self, rows: list[list[str]], has_header: bool) -> None:
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        total = 9360 if self.academic else 9638  # text width in twentieths of a point
        lengths = [max(4, min(40, max(len(re.sub(r"[*_`]", "", row[c])) for row in rows))) for c in range(width)]
        widths = [int(total * length / sum(lengths)) for length in lengths]
        grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
        xml = [f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="5000" w:type="pct"/>'
               f'<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>'
               f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>']
        for index, row in enumerate(rows):
            header = has_header and index == 0
            trpr = '<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>' if header else '<w:trPr><w:cantSplit/></w:trPr>'
            cells = []
            for col, cell in enumerate(row):
                numeric = bool(re.fullmatch(r"[-+]?[\d,]*\.?\d+%?|[<>]?\s?\.?\d+(\.\d+)?(e-?\d+)?|\(\d+(\.\d+)?%?\)", cell.strip()))
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/>' if header else ""
                align = '<w:jc w:val="center"/>' if header or (numeric and col > 0) else ""
                cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{widths[col]}" w:type="dxa"/>{shade}<w:vAlign w:val="center"/></w:tcPr>'
                             f'<w:p><w:pPr><w:pStyle w:val="TableText"/>{align}</w:pPr>'
                             f'{self.runs(cell.strip(), "<w:b/>" if header else "")}</w:p></w:tc>')
            xml.append(f"<w:tr>{trpr}{''.join(cells)}</w:tr>")
        xml.append("</w:tbl>")
        self.body.append("".join(xml))
        self.body.append('<w:p><w:pPr><w:pStyle w:val="TableText"/></w:pPr></w:p>')  # breathing space after the table
        self.counts["tables"] += 1

    def list_item(self, text: str, num: int, level: int) -> None:
        self.paragraph(text, "ListParagraph", f'<w:numPr><w:ilvl w:val="{min(level, 8)}"/><w:numId w:val="{num}"/></w:numPr>')

    def equation(self, tex: str) -> None:
        """A display equation as native Word maths (centred; one paragraph per aligned line)."""
        if tex.strip():
            self.body.append(omml.display(tex))
            self.counts["equations"] = self.counts.get("equations", 0) + 1

    def code(self, text: str) -> None:
        for line in text.split("\n") or [""]:
            self.body.append(f'<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr>{self.run(line)}</w:p>')

    def _inline_drawing(self, cx: int, cy: int, name: str, graphic: str) -> str:
        self.drawing_id += 1
        return (f'<w:p><w:pPr><w:jc w:val="center"/><w:keepNext/></w:pPr><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
                f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
                f'<wp:docPr id="{self.drawing_id}" name="{_x(name)} {self.drawing_id}"/><wp:cNvGraphicFramePr/>'
                f'<a:graphic xmlns:a="{NS["a"]}">{graphic}</a:graphic></wp:inline></w:drawing></w:r></w:p>')

    def image(self, path: str, caption: str) -> None:
        try:
            target = _safe_target(self.root, path)
        except ValueError as error:
            self.warnings.append(f"Image {path}: {error}")
            self.paragraph(f"[Missing image: {path}]")
            return
        data = target.read_bytes()
        size = _image_size(data)
        if size is None:
            self.warnings.append(f"Image {path} is not a PNG, JPEG or GIF")
            return
        ext = {b"\x89P": "png", b"\xff\xd8": "jpeg", b"GI": "gif"}[data[:2]]
        name = f"image{len(self.media) + 1}.{ext}"
        self.media[name] = data
        rid = self.rel("image", f"media/{name}")
        width_px, height_px = size
        max_width = 6.0 * EMU_PER_INCH
        cx = min(max_width, width_px * EMU_PER_INCH / 96)
        cy = cx * height_px / width_px
        if cy > 8 * EMU_PER_INCH:
            cy, cx = 8 * EMU_PER_INCH, 8 * EMU_PER_INCH * width_px / height_px
        graphic = (f'<a:graphicData uri="{NS["pic"]}"><pic:pic xmlns:pic="{NS["pic"]}"><pic:nvPicPr><pic:cNvPr id="0" name="{name}"/>'
                   f'<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                   f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{int(cx)}" cy="{int(cy)}"/></a:xfrm>'
                   f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData>')
        self.body.append(self._inline_drawing(int(cx), int(cy), "Picture", graphic))
        if caption:
            self.paragraph(caption, "Caption")
        self.counts["images"] += 1

    def chart(self, spec_text: str) -> None:
        try:
            spec = json.loads(spec_text)
            xml, workbook = chart_part(spec, len(self.charts) + 1)
        except (ValueError, TypeError, KeyError) as error:
            self.warnings.append(f"Chart skipped: {error}")
            self.paragraph(f"[Chart could not be drawn: {error}]")
            return
        number = len(self.charts) + 1
        self.charts.append((f"chart{number}.xml", xml, workbook))
        rid = self.rel("chart", f"charts/chart{number}.xml")
        cx, cy = int(6 * EMU_PER_INCH), int((3.6 if spec.get("type") != "pie" else 3.8) * EMU_PER_INCH)
        graphic = (f'<a:graphicData uri="{NS["c"]}"><c:chart xmlns:c="{NS["c"]}" xmlns:r="{NS["r"]}" r:id="{rid}"/></a:graphicData>')
        self.body.append(self._inline_drawing(cx, cy, "Chart", graphic))
        self.counts["charts"] += 1


def _image_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", data[6:10])
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            length = struct.unpack(">H", data[index + 2:index + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[index + 5:index + 9])
                return width, height
            index += 2 + length
    return None


def _table_rows(lines: list[str]) -> tuple[list[list[str]], bool]:
    def cells(line: str) -> list[str]:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|") and not line.endswith("\\|"):
            line = line[:-1]
        return [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", line)]
    rows, has_header = [], False
    for index, line in enumerate(lines):
        if re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*", line):
            has_header = index == 1
            continue
        rows.append(cells(line))
    return rows, has_header


def _render_markdown(doc: _Doc, markdown: str, chapter_breaks: bool) -> None:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    paragraph: list[str] = []
    list_state: list[tuple[int, bool, int]] = []  # (indent, ordered, num id)
    first_heading = True

    def flush():
        nonlocal paragraph
        if paragraph:
            text = " ".join(part.strip() for part in paragraph).strip()
            caption = re.match(r"^\**((Table|Figure|Fig\.|Chart)\s+[\dA-Z]+(\.\d+)*[.:]?)\**", text)
            if caption:
                doc.paragraph(text.replace("**", ""), "Caption", '<w:keepNext/>' if caption.group(2) == "Table" else "")
            else:
                doc.paragraph(text, "BodyText")
            paragraph = []

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        fence = re.match(r"^(```|~~~)\s*([\w+-]*)\s*$", stripped)
        if fence:
            flush()
            list_state.clear()
            marker, language = fence.group(1), fence.group(2).lower()
            block = []
            index += 1
            while index < len(lines) and lines[index].strip() != marker:
                block.append(lines[index])
                index += 1
            index += 1
            if language in {"math", "latex-math", "katex"}:
                doc.equation("\n".join(block))
            elif language in {"chart", "graph"}:
                doc.chart("\n".join(block))
            elif language == "mermaid":
                doc.warnings.append("Mermaid diagrams cannot be drawn inside Word; it was kept as text.")
                doc.code("\n".join(block))
            else:
                doc.code("\n".join(block))
            continue
        opening = _DISPLAY_OPEN.match(stripped)
        if opening:
            # Display maths: $$ … $$, \[ … \] or \begin{align} … \end{align}, on one line or several.
            closer = {"$$": "$$", "\\[": "\\]"}.get(opening.group(1))
            end_env = None if closer else f"\\end{{{opening.group(1)[7:-1]}}}"
            mark = closer or end_env
            block, cursor, rest = [], index, ""
            text = stripped[len(opening.group(1)):] if closer else stripped
            while True:
                at = text.find(mark)
                if at >= 0:  # closed on this line; text after it (e.g. "✓") stays a paragraph
                    block.append(text[:at] if closer else text[:at + len(mark)])
                    rest = text[at + len(mark):].strip()
                    break
                block.append(text)
                if cursor + 1 >= len(lines):
                    block = []
                    break
                cursor += 1
                text = lines[cursor].strip()
            if block:
                flush()
                list_state.clear()
                doc.equation("\n".join(block))
                if rest:
                    doc.paragraph(rest, "BodyText")
                index = cursor + 1
                continue
        if stripped in {"\\pagebreak", "\\newpage", "<!-- pagebreak -->", "<div style=\"page-break-after: always\"></div>"}:
            flush()
            list_state.clear()
            doc.page_break()
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if heading:
            flush()
            list_state.clear()
            level = len(heading.group(1))
            doc.heading(level, heading.group(2), page_break=chapter_breaks and level == 1 and not first_heading)
            first_heading = False
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and re.fullmatch(r"\s*\|?\s*:?-{2,}:?.*", lines[index + 1]):
            flush()
            list_state.clear()
            block = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index])
                index += 1
            rows, has_header = _table_rows(block)
            if rows:
                doc.table(rows, has_header)
            continue
        image = re.fullmatch(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", stripped)
        if image:
            flush()
            list_state.clear()
            doc.image(image.group(2), image.group(1))
            index += 1
            continue
        item = re.match(r"^(\s*)([-*+•]|\d+[.)]|[a-z][.)])\s+(.*)$", line)
        if item and (not re.match(r"^\s*[a-z][.)]", line) or list_state):
            flush()
            indent = len(item.group(1).replace("\t", "    "))
            ordered = item.group(2) not in "-*+•"
            while list_state and indent < list_state[-1][0]:
                list_state.pop()
            if list_state and indent == list_state[-1][0] and list_state[-1][1] != ordered:
                list_state.pop()  # same depth, other kind: a new list starts here
            if not list_state or indent > list_state[-1][0]:
                list_state.append((indent, ordered, doc.new_list(ordered)))
            doc.list_item(item.group(3), list_state[-1][2], len(list_state) - 1)
            index += 1
            continue
        if stripped.startswith(">"):
            flush()
            list_state.clear()
            quote = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip()[1:].strip())
                index += 1
            doc.paragraph(" ".join(quote), "Quote")
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush()
            list_state.clear()
            index += 1
            continue
        if not stripped:
            flush()
            if list_state and index + 1 < len(lines) and not re.match(r"^\s*([-*+•]|\d+[.)])\s+", lines[index + 1]):
                list_state.clear()
            index += 1
            continue
        if list_state and line.startswith((" ", "\t")) and not paragraph:
            doc.paragraph(stripped, "ListParagraph", f'<w:ind w:left="{720 * len(list_state)}"/>')
            index += 1
            continue
        list_state.clear()
        paragraph.append(line)
        if line.endswith("  "):
            paragraph[-1] = line.rstrip() + "\n"
        index += 1
    flush()


def _styles(academic: bool) -> str:
    font = "Times New Roman" if academic else "Calibri"
    heading_font = "Times New Roman" if academic else "Calibri Light"
    size = 24 if academic else 22
    line = 360 if academic else 276
    justify = '<w:jc w:val="both"/>' if academic else ""
    color = "000000" if academic else "1F3864"
    sizes = {1: 32 if academic else 36, 2: 28, 3: 26 if academic else 24, 4: 24, 5: 24, 6: 24}

    def heading(level: int) -> str:
        caps = '<w:caps/>' if academic and level == 1 else ""
        center = '<w:jc w:val="center"/>' if academic and level == 1 else ""
        italic = '<w:i/>' if level >= 4 else ""
        return (f'<w:style w:type="paragraph" w:styleId="Heading{level}"><w:name w:val="heading {level}"/><w:basedOn w:val="Normal"/>'
                f'<w:next w:val="BodyText"/><w:uiPriority w:val="9"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/>'
                f'<w:spacing w:before="{360 if level == 1 else 240}" w:after="{240 if level == 1 else 120}" w:line="276" w:lineRule="auto"/>{center}'
                f'<w:outlineLvl w:val="{level - 1}"/></w:pPr><w:rPr><w:rFonts w:ascii="{heading_font}" w:hAnsi="{heading_font}" w:cs="{heading_font}"/>'
                f'<w:b/>{italic}{caps}<w:color w:val="{color}"/><w:sz w:val="{sizes[level]}"/><w:szCs w:val="{sizes[level]}"/></w:rPr></w:style>')
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:styles xmlns:w="{NS["w"]}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}" w:cs="{font}"/>'
            f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>'
            f'<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="{line}" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
            f'<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
            f'<w:style w:type="paragraph" w:styleId="BodyText"><w:name w:val="Body Text"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr>{justify}<w:spacing w:after="160"/></w:pPr></w:style>'
            + "".join(heading(level) for level in range(1, 7)) +
            f'<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:jc w:val="center"/><w:spacing w:before="2400" w:after="480"/></w:pPr>'
            f'<w:rPr><w:rFonts w:ascii="{heading_font}" w:hAnsi="{heading_font}"/><w:b/><w:caps/><w:sz w:val="36"/></w:rPr></w:style>'
            f'<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:jc w:val="center"/><w:spacing w:after="120"/></w:pPr><w:rPr><w:sz w:val="26"/></w:rPr></w:style>'
            f'<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:next w:val="BodyText"/><w:qFormat/>'
            f'<w:pPr><w:spacing w:before="120" w:after="120" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:b/><w:sz w:val="{size - 2}"/></w:rPr></w:style>'
            f'<w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="20" w:after="20" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="{size - 2}"/></w:rPr></w:style>'
            f'<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="60"/>{justify}</w:pPr></w:style>'
            f'<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="720" w:right="720"/></w:pPr><w:rPr><w:i/></w:rPr></w:style>'
            f'<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:pPr>'
            f'<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="19"/></w:rPr></w:style>'
            f'<w:style w:type="paragraph" w:styleId="TOCHeading"><w:name w:val="TOC Heading"/><w:basedOn w:val="Heading1"/><w:next w:val="Normal"/><w:pPr><w:outlineLvl w:val="9"/></w:pPr></w:style>'
            + "".join(f'<w:style w:type="paragraph" w:styleId="TOC{i}"><w:name w:val="toc {i}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/></w:tabs><w:spacing w:after="60"/><w:ind w:left="{(i - 1) * 240}"/></w:pPr></w:style>' for i in (1, 2, 3)) +
            f'<w:style w:type="paragraph" w:styleId="Footer"><w:name w:val="footer"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:after="0"/></w:pPr></w:style>'
            f'<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'
            f'<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
            f'<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:basedOn w:val="TableNormal"/><w:tblPr><w:tblBorders>'
            + "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="808080"/>' for side in ("top", "left", "bottom", "right", "insideH", "insideV")) +
            '</w:tblBorders></w:tblPr></w:style></w:styles>')


def _numbering(doc: _Doc) -> str:
    def levels(ordered: bool) -> str:
        out = []
        for level in range(9):
            if ordered:
                fmt, text = (("decimal", f"%{level + 1}."), ("lowerLetter", f"%{level + 1})"), ("lowerRoman", f"%{level + 1}."))[level % 3]
            else:
                fmt, text = "bullet", ("•", "◦", "▪")[level % 3]
            out.append(f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/>'
                       f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{720 * (level + 1)}" w:hanging="360"/></w:pPr></w:lvl>')
        return "".join(out)
    nums = "".join(f'<w:num w:numId="{num}"><w:abstractNumId w:val="{abstract}"/>'
                   f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>' for num, abstract in doc.numbering)
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering xmlns:w="{NS["w"]}">'
            f'<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>{levels(False)}</w:abstractNum>'
            f'<w:abstractNum w:abstractNumId="2"><w:multiLevelType w:val="hybridMultilevel"/>{levels(True)}</w:abstractNum>{nums}</w:numbering>')


def build_docx(markdown: str, root: Path, title: str = "", subtitle: str = "", title_page: bool = False,
               toc: bool = False, style: str = "academic", chapter_breaks=None) -> tuple[bytes, dict]:
    doc = _Doc(root, style)
    if chapter_breaks is None:
        chapter_breaks = doc.academic
    if title_page and title:
        doc.paragraph(title, "Title")
        for line in str(subtitle).split("\n"):
            if line.strip():
                doc.paragraph(line.strip(), "Subtitle")
        doc.page_break()
    if toc:
        doc.body.append('<w:p><w:pPr><w:pStyle w:val="TOCHeading"/></w:pPr><w:r><w:t>Table of Contents</w:t></w:r></w:p>')
        doc.body.append('<w:p><w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r><w:r><w:instrText xml:space="preserve"> TOC \\o "1-3" \\h \\z \\u </w:instrText></w:r>'
                        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>Right-click here and choose Update Field to build the table of contents.</w:t></w:r>'
                        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
        doc.page_break()
    _render_markdown(doc, markdown, chapter_breaks)
    footer_rid = doc.rel("footer", "footer1.xml")
    for rid_kind in ("styles", "numbering", "settings"):
        doc.rel(rid_kind, f"{rid_kind}.xml")
    margins = '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>'
    sect = (f'<w:sectPr><w:footerReference w:type="default" r:id="{footer_rid}"/><w:pgSz w:w="11906" w:h="16838"/>{margins}'
            f'<w:pgNumType w:start="1"/><w:cols w:space="708"/></w:sectPr>')
    namespaces = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items() if k in ("w", "r", "wp", "a", "pic", "m"))
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {namespaces}><w:body>'
                + "".join(doc.body) + sect + "</w:body></w:document>")
    footer = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="{NS["w"]}" xmlns:r="{NS["r"]}"><w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr>'
              '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
              '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')
    settings = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings xmlns:w="{NS["w"]}">'
                + ('<w:updateFields w:val="true"/>' if toc else "") +
                '<w:defaultTabStop w:val="720"/><w:characterSpacingControl w:val="doNotCompress"/><w:compat>'
                '<w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="{rid}" Type="{kind}" Target="{target}"{f" TargetMode=\"{mode}\"" if mode else ""}/>'
                      for rid, kind, target, mode in doc.rels) + "</Relationships>")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{_x(title)}</dc:title><dc:creator>Dikdik</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           '<Application>Microsoft Office Word</Application></Properties>')
    overrides = [("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
                 ("/word/styles.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"),
                 ("/word/numbering.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"),
                 ("/word/settings.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"),
                 ("/word/footer1.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"),
                 ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                 ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml")]
    overrides += [(f"/word/charts/{name}", "application/vnd.openxmlformats-officedocument.drawingml.chart+xml") for name, _, _ in doc.charts]
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                     '<Default Extension="jpeg" ContentType="image/jpeg"/><Default Extension="gif" ContentType="image/gif"/>'
                     '<Default Extension="xlsx" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"/>'
                     + "".join(f'<Override PartName="{part}" ContentType="{kind}"/>' for part, kind in overrides) + "</Types>")
    package_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="word/document.xml"/>'
                    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                    f'<Relationship Id="rId3" Type="{REL}/extended-properties" Target="docProps/app.xml"/></Relationships>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package_rels)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _styles(doc.academic))
        archive.writestr("word/numbering.xml", _numbering(doc))
        archive.writestr("word/settings.xml", settings)
        archive.writestr("word/footer1.xml", footer)
        archive.writestr("word/_rels/document.xml.rels", rels)
        for name, data in doc.media.items():
            archive.writestr(f"word/media/{name}", data)
        for number, (name, xml, workbook) in enumerate(doc.charts, start=1):
            archive.writestr(f"word/charts/{name}", xml)
            archive.writestr(f"word/embeddings/Microsoft_Excel_Worksheet{number}.xlsx", workbook)
            archive.writestr(f"word/charts/_rels/{name}.rels",
                             '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                             f'<Relationship Id="rId1" Type="{REL}/package" Target="../embeddings/Microsoft_Excel_Worksheet{number}.xlsx"/></Relationships>')
    stats = {key: value for key, value in doc.counts.items() if value}
    if doc.warnings:
        stats["warnings"] = doc.warnings[:20]
    return buffer.getvalue(), stats


# --- charts ---------------------------------------------------------------------------

def _col_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def chart_part(spec: dict, number: int) -> tuple[bytes, bytes]:
    if not isinstance(spec, dict):
        raise ValueError("chart JSON must be an object")
    kind = str(spec.get("type") or "column").lower()
    if kind not in {"column", "bar", "line", "pie", "doughnut", "stacked_column", "stacked_bar"}:
        raise ValueError(f"unknown chart type {kind}")
    categories = [str(c) for c in spec.get("categories") or []]
    series = spec.get("series") or ([{"name": spec.get("name") or "Value", "values": spec["values"]}] if spec.get("values") else [])
    if not categories or not series:
        raise ValueError("a chart needs categories and series")
    clean = []
    for item in series[:12]:
        values = []
        for value in (item.get("values") or [])[: len(categories)]:
            try:
                values.append(float(str(value).replace("%", "").replace(",", "")))
            except ValueError:
                values.append(None)
        clean.append({"name": str(item.get("name") or f"Series {len(clean) + 1}"), "values": values})
    if kind in {"pie", "doughnut"}:
        clean = clean[:1]
    rows = [[""] + [s["name"] for s in clean]] + [[cat] + [s["values"][i] if i < len(s["values"]) else None for s in clean]
                                                    for i, cat in enumerate(categories)]
    workbook = build_xlsx([{"name": "Sheet1", "rows": rows}])
    count = len(categories)
    cat_ref = f"Sheet1!$A$2:$A${count + 1}"
    str_cache = lambda values: f'<c:ptCount val="{len(values)}"/>' + "".join(f'<c:pt idx="{i}"><c:v>{_x(v)}</c:v></c:pt>' for i, v in enumerate(values))
    palette = ["4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47", "264478", "9E480E", "636363", "997300"]
    show_values = spec.get("data_labels", True)
    sers = []
    for index, item in enumerate(clean):
        col = _col_letter(index + 1)
        values = item["values"]
        num_cache = (f'<c:formatCode>General</c:formatCode><c:ptCount val="{count}"/>'
                     + "".join(f'<c:pt idx="{i}"><c:v>{v:g}</c:v></c:pt>' for i, v in enumerate(values) if v is not None))
        fill = "" if kind in {"pie", "doughnut"} else (
            f'<c:spPr><a:ln w="28575" cap="rnd"><a:solidFill><a:srgbClr val="{palette[index % len(palette)]}"/></a:solidFill><a:round/></a:ln></c:spPr><c:marker><c:symbol val="circle"/><c:size val="6"/></c:marker>'
            if kind == "line" else f'<c:spPr><a:solidFill><a:srgbClr val="{palette[index % len(palette)]}"/></a:solidFill></c:spPr><c:invertIfNegative val="0"/>')
        points = ""
        if kind in {"pie", "doughnut"}:
            points = "".join(f'<c:dPt><c:idx val="{i}"/><c:bubble3D val="0"/><c:spPr><a:solidFill><a:srgbClr val="{palette[i % len(palette)]}"/></a:solidFill>'
                             f'<a:ln w="12700"><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:ln></c:spPr></c:dPt>' for i in range(count))
        labels = (f'<c:dLbls><c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr><c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="900"/></a:pPr><a:endParaRPr lang="en-US"/></a:p></c:txPr>'
                  f'{"<c:dLblPos val=\"outEnd\"/>" if kind in {"column", "bar"} else ""}<c:showLegendKey val="0"/><c:showVal val="{0 if kind in {"pie", "doughnut"} else 1}"/><c:showCatName val="0"/>'
                  f'<c:showSerName val="0"/><c:showPercent val="{1 if kind in {"pie", "doughnut"} else 0}"/><c:showBubbleSize val="0"/></c:dLbls>') if show_values else ""
        sers.append(f'<c:ser><c:idx val="{index}"/><c:order val="{index}"/><c:tx><c:strRef><c:f>Sheet1!${col}$1</c:f><c:strCache>{str_cache([item["name"]])}</c:strCache></c:strRef></c:tx>'
                    f'{fill}{points}{labels}<c:cat><c:strRef><c:f>{cat_ref}</c:f><c:strCache>{str_cache(categories)}</c:strCache></c:strRef></c:cat>'
                    f'<c:val><c:numRef><c:f>Sheet1!${col}$2:${col}${count + 1}</c:f><c:numCache>{num_cache}</c:numCache></c:numRef></c:val>'
                    f'{"<c:smooth val=\"0\"/>" if kind == "line" else ""}</c:ser>')
    axes_ids = '<c:axId val="111111111"/><c:axId val="222222222"/>'
    if kind in {"pie", "doughnut"}:
        tag = "c:doughnutChart" if kind == "doughnut" else "c:pieChart"
        plot = (f'<{tag}><c:varyColors val="1"/>{"".join(sers)}<c:firstSliceAng val="0"/>'
                f'{"<c:holeSize val=\"55\"/>" if kind == "doughnut" else ""}</{tag}>')
        axes = ""
    else:
        horizontal = kind in {"bar", "stacked_bar"}
        stacked = kind.startswith("stacked")
        if kind == "line":
            plot = f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>{"".join(sers)}<c:marker val="1"/>{axes_ids}</c:lineChart>'
        else:
            plot = (f'<c:barChart><c:barDir val="{"bar" if horizontal else "col"}"/><c:grouping val="{"stacked" if stacked else "clustered"}"/>'
                    f'<c:varyColors val="0"/>{"".join(sers)}<c:gapWidth val="{60 if len(clean) > 1 else 80}"/>'
                    f'{"<c:overlap val=\"100\"/>" if stacked else ""}{axes_ids}</c:barChart>')

        def title(text: str) -> str:
            return (f'<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="1000" b="0"/></a:pPr><a:r><a:rPr lang="en-US" sz="1000" b="0"/>'
                    f'<a:t>{_x(text)}</a:t></a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title>') if text else ""
        text_props = '<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="900"/></a:pPr><a:endParaRPr lang="en-US"/></a:p></c:txPr>'
        axes = (f'<c:catAx><c:axId val="111111111"/><c:scaling><c:orientation val="{"maxMin" if horizontal else "minMax"}"/></c:scaling><c:delete val="0"/>'
                f'<c:axPos val="{"l" if horizontal else "b"}"/>{title(str(spec.get("x_title") or ""))}<c:numFmt formatCode="General" sourceLinked="0"/>'
                f'<c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>{text_props}<c:crossAx val="222222222"/><c:crosses val="autoZero"/>'
                f'<c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>'
                f'<c:valAx><c:axId val="222222222"/><c:scaling><c:orientation val="minMax"/>'
                + (f'<c:max val="{float(spec["y_max"]):g}"/>' if isinstance(spec.get("y_max"), (int, float)) else "")
                + (f'<c:min val="{float(spec["y_min"]):g}"/>' if isinstance(spec.get("y_min"), (int, float)) else "")
                + f'</c:scaling><c:delete val="0"/><c:axPos val="{"b" if horizontal else "l"}"/>'
                f'<c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="D9D9D9"/></a:solidFill></a:ln></c:spPr></c:majorGridlines>'
                f'{title(str(spec.get("y_title") or ""))}<c:numFmt formatCode="General" sourceLinked="0"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/>'
                f'<c:tickLblPos val="nextTo"/>{text_props}<c:crossAx val="111111111"/><c:crosses val="{"max" if horizontal else "autoZero"}"/><c:crossBetween val="between"/></c:valAx>')
    chart_title = str(spec.get("title") or "")
    title_xml = (f'<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="1200" b="1"/></a:pPr><a:r><a:rPr lang="en-US" sz="1200" b="1"/>'
                 f'<a:t>{_x(chart_title)}</a:t></a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/>'
                 if chart_title else '<c:autoTitleDeleted val="1"/>')
    legend = ('<c:legend><c:legendPos val="b"/><c:overlay val="0"/><c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="900"/></a:pPr><a:endParaRPr lang="en-US"/></a:p></c:txPr></c:legend>'
              if len(clean) > 1 or kind in {"pie", "doughnut"} else "")
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><c:chartSpace xmlns:c="{NS["c"]}" xmlns:a="{NS["a"]}" xmlns:r="{NS["r"]}">'
           f'<c:date1904 val="0"/><c:lang val="en-US"/><c:roundedCorners val="0"/><c:chart>{title_xml}<c:plotArea><c:layout/>{plot}{axes}</c:plotArea>'
           f'{legend}<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>'
           f'<c:spPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:ln><a:noFill/></a:ln></c:spPr>'
           f'<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr><a:latin typeface="Calibri"/></a:defRPr></a:pPr><a:endParaRPr lang="en-US"/></a:p></c:txPr>'
           f'<c:externalData r:id="rId1"><c:autoUpdate val="0"/></c:externalData></c:chartSpace>')
    return xml.encode("utf-8"), workbook


# --- xlsx --------------------------------------------------------------------------

def build_xlsx(sheets: list[dict]) -> bytes:
    clean = []
    used = set()
    for index, sheet in enumerate(sheets[:50]):
        if not isinstance(sheet, dict):
            raise ValueError("Each sheet is {name, rows}")
        name = re.sub(r"[\[\]:*?/\\]", " ", str(sheet.get("name") or f"Sheet{index + 1}")).strip()[:31] or f"Sheet{index + 1}"
        while name.lower() in used:
            name = (name[:28] + f" {index + 1}")[:31]
        used.add(name.lower())
        rows = sheet.get("rows") or []
        if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
            raise ValueError(f"Sheet {name}: rows must be a list of lists")
        clean.append((name, rows[:200_000]))
    sheet_xml = []
    for name, rows in clean:
        width = max((len(r) for r in rows), default=0)
        widths = [min(60, max(8, max((len(str(r[c])) for r in rows[:500] if c < len(r) and r[c] is not None), default=8) + 2)) for c in range(width)]
        cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
        out = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, value in enumerate(row):
                if value is None or value == "":
                    continue
                ref = f"{_col_letter(c)}{r}"
                style = ' s="1"' if r == 1 else ""
                if isinstance(value, bool):
                    cells.append(f'<c r="{ref}" t="b"{style}><v>{int(value)}</v></c>')
                elif isinstance(value, (int, float)) and value == value and abs(value) != float("inf"):
                    cells.append(f'<c r="{ref}"{style}><v>{value:.15g}</v></c>')
                elif isinstance(value, str) and value.startswith("=") and len(value) > 1:
                    cells.append(f'<c r="{ref}"{style}><f>{_x(value[1:])}</f></c>')
                else:
                    text = str(value)
                    if r > 1 and re.fullmatch(r"-?\d+(\.\d+)?", text) and len(text) < 16 and not re.fullmatch(r"0\d+", text):
                        cells.append(f'<c r="{ref}"{style}><v>{text}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{_x(text[:32767])}</t></is></c>')
            out.append(f'<row r="{r}">{"".join(cells)}</row>')
        pane = '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>' if len(rows) > 1 else ""
        sheet_xml.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                         f'{pane}{f"<cols>{cols}</cols>" if cols else ""}<sheetData>{"".join(out)}</sheetData></worksheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                f'xmlns:r="{REL}"><sheets>' + "".join(f'<sheet name="{_x(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, (name, _) in enumerate(clean))
                + "</sheets></workbook>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i + 1}" Type="{REL}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(clean)))
            + f'<Relationship Id="rId{len(clean) + 1}" Type="{REL}/styles" Target="styles.xml"/></Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E2F3"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
             + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(clean)))
             + "</Types>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                        f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/styles.xml", styles)
        for index, xml in enumerate(sheet_xml):
            archive.writestr(f"xl/worksheets/sheet{index + 1}.xml", xml)
    return buffer.getvalue()
