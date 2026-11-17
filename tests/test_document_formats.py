"""Every document format the agent can read, built in memory (no fixtures, no network)."""

import base64
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from dikdik import docextract, documents, stats, vision

ODF = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"><office:body>{}</office:body></office:document-content>')


def zipped(path: Path, parts: dict) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return path


def pdf(objects: list[bytes]) -> bytes:
    out = b"%PDF-1.4\n"
    for number, body in enumerate(objects, start=1):
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    return out + b"trailer << /Root 1 0 R >>\n%%EOF"


def stream(data: bytes, extra: bytes = b"", compress: bool = True) -> bytes:
    body = zlib.compress(data) if compress else data
    return (b"<< /Length %d " % len(body) + (b"/Filter /FlateDecode " if compress else b"") + extra + b">>\nstream\n"
            + body + b"\nendstream")


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_opendocument_text_spreadsheet_and_slides(self):
        odt = zipped(self.root / "a.odt", {"content.xml": ODF.format(
            '<office:text><text:h text:outline-level="2">Methods</text:h><text:p>Sampling<text:s text:c="2"/>was random.</text:p>'
            '<text:list><text:list-item><text:p>Nurses</text:p></text:list-item></text:list>'
            '<table:table><table:table-row><table:table-cell><text:p>n</text:p></table:table-cell></table:table-row></table:table></office:text>')})
        text = docextract.document_text(odt)
        self.assertIn("## Methods", text)
        self.assertIn("Sampling was random.", text)  # whitespace is normalised
        self.assertIn("- Nurses", text)
        ods = zipped(self.root / "b.ods", {"content.xml": ODF.format(
            '<office:spreadsheet><table:table table:name="Data"><table:table-row><table:table-cell><text:p>Age</text:p></table:table-cell>'
            '<table:table-cell table:number-columns-repeated="2"><text:p>x</text:p></table:table-cell></table:table-row>'
            '<table:table-row table:number-rows-repeated="2"><table:table-cell office:value="30"><text:p>30</text:p></table:table-cell></table:table-row>'
            '</table:table></office:spreadsheet>')})
        sheet = docextract.extract(ods, full=True)["sheets"][0]
        self.assertEqual(sheet["name"], "Data")
        self.assertEqual(sheet["rows"], [["Age", "x", "x"], ["30"], ["30"]])
        self.assertEqual(stats.run(self.root, {"path": "b.ods", "analysis": "descriptives", "columns": ["Age"]})["descriptives"][0]["mean"], 30)
        odp = zipped(self.root / "c.odp", {"content.xml": ODF.format(
            '<office:presentation><draw:page draw:name="p1"><text:p>Title</text:p><text:p>Point</text:p></draw:page></office:presentation>')})
        self.assertEqual(docextract.extract(odp)["slides"][0], {"title": "Title", "points": ["Point"]})

    def test_rtf_html_epub_and_plain_text(self):
        (self.root / "a.rtf").write_bytes(rb"{\rtf1\ansi{\fonttbl{\f0 Arial;}}\f0 Caf\'e9 \b bold\b0\par Line " + b"\x5cu8364? two\x5ctab cell}")
        text = docextract.document_text(self.root / "a.rtf")
        self.assertIn("Café bold", text)
        self.assertIn("Line € two\tcell", text)  # 荤? is the euro sign; \tab is a tab
        (self.root / "a.html").write_text("<html><head><style>x{}</style></head><body><h2>Results</h2><p>A &amp; B</p>"
                                          "<ul><li>one</li></ul><script>bad()</script></body></html>", encoding="utf-8")
        html_text = docextract.document_text(self.root / "a.html")
        self.assertIn("## Results", html_text)
        self.assertIn("A & B", html_text)
        self.assertNotIn("bad()", html_text)
        zipped(self.root / "a.epub", {"OEBPS/ch1.xhtml": "<html><body><h1>Chapter</h1><p>Text.</p></body></html>"})
        self.assertIn("# Chapter", docextract.document_text(self.root / "a.epub"))
        (self.root / "notes.txt").write_bytes("﻿hello".encode("utf-8"))
        self.assertEqual(docextract.document_text(self.root / "notes.txt"), "hello")
        (self.root / "data.csv").write_text("a;b\n1;2\n", encoding="utf-8")
        self.assertEqual(docextract.extract(self.root / "data.csv")["delimiter"], "semicolon")
        with self.assertRaisesRegex(ValueError, "old binary Office format"):
            docextract.extract(self.root / "x.doc")
        with self.assertRaisesRegex(ValueError, "No document reader"):
            docextract.extract(self.root / "notes.txt", name="file.xyz")

    def test_pptx_slides_notes_tables_and_pictures(self):
        p = "http://schemas.openxmlformats.org/presentationml/2006/main"
        a = "http://schemas.openxmlformats.org/drawingml/2006/main"
        r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        slide = (f'<p:sld xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"><p:cSld><p:spTree>'
                 f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Findings</a:t></a:r></a:p></p:txBody></p:sp>'
                 f'<p:sp><p:txBody><a:p><a:r><a:t>Point one</a:t></a:r></a:p></p:txBody></p:sp>'
                 f'<a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>n</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>91</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl>'
                 f'<p:pic><p:blipFill><a:blip r:embed="rId2"/></p:blipFill></p:pic></p:spTree></p:cSld></p:sld>')
        rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId2" Type="image" Target="../media/image1.png"/></Relationships>')
        notes = f'<p:notes xmlns:p="{p}" xmlns:a="{a}"><a:p><a:r><a:t>Say this</a:t></a:r></a:p><a:p><a:r><a:t>1</a:t></a:r></a:p></p:notes>'
        path = zipped(self.root / "deck.pptx", {"ppt/slides/slide1.xml": slide, "ppt/slides/_rels/slide1.xml.rels": rels,
                                               "ppt/notesSlides/notesSlide1.xml": notes, "ppt/media/image1.png": b"\x89PNG\r\n\x1a\n" + b"0" * 20})
        view = docextract.preview(path)["slides"][0]
        self.assertEqual(view["title"], "Findings")
        self.assertIn("n | 91", view["points"])
        self.assertEqual(view["notes"], "Say this")
        self.assertEqual(view["images"], ["ppt/media/image1.png"])
        self.assertIn("Speaker notes: Say this", docextract.document_text(path))
        self.assertEqual(len(vision.document_images(path)), 1)

    def test_pdf_text_with_font_maps_object_streams_and_hex_strings(self):
        cmap = (b"/CIDInit /ProcSet findresource begin begincmap 1 begincodespacerange <0000> <FFFF> endcodespacerange "
                b"2 beginbfchar <0001> <0048> <0002> <0069> endbfchar 1 beginbfrange <0003> <0004> <0021> endbfrange endcmap")
        # A simple font (F3, no map), then two Type0 fonts whose glyph ids map back through a ToUnicode CMap.
        content = (b"BT /F3 12 Tf 72 720 Td [(Plain) -300 (text)] TJ T* (Next\\(line\\)) Tj "
                   b"/F1 12 Tf 0 -14 Td <00010002> Tj /F2 12 Tf 1 0 0 1 72 600 Tm <00030004> Tj ET")
        objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
                   b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                   b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R /F2 9 0 R >> >> /Contents 4 0 R >>",
                   stream(content),
                   b"<< /Type /Font /Subtype /Type0 /ToUnicode 6 0 R >>",
                   stream(cmap)]
        # Font 9 lives inside a compressed object stream (object 7).
        packed = b"9 0 " + b"<< /Type /Font /Subtype /Type0 /ToUnicode 6 0 R >>"
        objects.append(stream(packed, b"/Type /ObjStm /N 1 /First 4 "))
        data = pdf(objects)
        (self.root / "t.pdf").write_bytes(data)
        result = docextract.extract(self.root / "t.pdf", full=True)
        self.assertIn("Hi", result["text"])
        self.assertIn("Plain text", result["text"])
        self.assertIn("Next(line)", result["text"])
        self.assertIn("!\"", result["text"])
        self.assertEqual(result["page_count"], 1)

    def test_pdf_without_text_says_so(self):
        (self.root / "empty.pdf").write_bytes(pdf([b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [] /Count 0 >>"]))
        result = docextract.extract(self.root / "empty.pdf")
        self.assertIn("no text layer", result["note"])

    def test_docx_footnotes_styles_and_hyperlinks_in_preview(self):
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        document = (f'<w:document xmlns:w="{w}" xmlns:r="{r}"><w:body>'
                    f'<w:p><w:pPr><w:pStyle w:val="Kop1"/></w:pPr><w:r><w:t>Inleiding</w:t></w:r></w:p>'
                    f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:hyperlink r:id="rId5"><w:r><w:t>site</w:t></w:r></w:hyperlink>'
                    f'<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r></w:p>'
                    f'<w:sdt><w:sdtContent><w:p><w:r><w:t>In a content control</w:t></w:r></w:p></w:sdtContent></w:sdt>'
                    f'<w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr><w:r><w:t>Table 1: X</w:t></w:r></w:p>'
                    f'</w:body></w:document>')
        styles = (f'<w:styles xmlns:w="{w}"><w:style w:styleId="Kop1"><w:name w:val="heading 1"/></w:style>'
                  f'<w:style w:styleId="Caption"><w:name w:val="caption"/></w:style></w:styles>')
        rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId5" Type="hyperlink" Target="https://example.org" TargetMode="External"/></Relationships>')
        foot = f'<w:footnotes xmlns:w="{w}"><w:footnote><w:p><w:r><w:t>A note.</w:t></w:r></w:p></w:footnote></w:footnotes>'
        path = zipped(self.root / "d.docx", {"word/document.xml": document, "word/styles.xml": styles,
                                             "word/_rels/document.xml.rels": rels, "word/footnotes.xml": foot})
        text = docextract.document_text(path)
        self.assertIn("# Inleiding", text)
        self.assertIn("In a content control", text)
        self.assertIn("A note.", text)
        blocks = docextract.preview(path)["blocks"]
        linked = next(b for b in blocks if b.get("runs"))
        self.assertEqual(linked["align"], "center")
        self.assertEqual(linked["runs"], [{"t": "site", "href": "https://example.org"}, {"t": "2", "sup": 1}])
        self.assertEqual(next(b for b in blocks if b.get("style"))["style"], "caption")

    def test_xlsx_dates_booleans_inline_strings_and_sheet_order(self):
        m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        workbook = (f'<workbook xmlns="{m}" xmlns:r="{r}"><sheets><sheet name="Second" sheetId="2" r:id="rId2"/>'
                    f'<sheet name="First" sheetId="1" r:id="rId1"/></sheets></workbook>')
        rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>'
                '<Relationship Id="rId2" Type="worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        styles = (f'<styleSheet xmlns="{m}"><numFmts><numFmt numFmtId="164" formatCode="dd/mm/yyyy hh:mm"/></numFmts>'
                  f'<cellXfs><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/></cellXfs></styleSheet>')
        sheet2 = (f'<worksheet xmlns="{m}"><sheetData><row r="1"><c r="A1" s="1"><v>45000</v></c><c r="B1" s="2"><v>45000.5</v></c>'
                  f'<c r="C1" t="b"><v>1</v></c><c r="D1" t="inlineStr"><is><t>inline</t></is></c><c r="E1"><v>0.1</v></c></row>'
                  f'<row r="3"><c r="A3"><v>7</v></c></row></sheetData></worksheet>')
        sheet1 = f'<worksheet xmlns="{m}"><sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData></worksheet>'
        path = zipped(self.root / "w.xlsx", {"xl/workbook.xml": workbook, "xl/_rels/workbook.xml.rels": rels, "xl/styles.xml": styles,
                                             "xl/worksheets/sheet1.xml": sheet1, "xl/worksheets/sheet2.xml": sheet2})
        sheets = docextract.extract(path, full=True)["sheets"]
        self.assertEqual([s["name"] for s in sheets], ["Second", "First"])
        self.assertEqual(sheets[0]["rows"][0], ["2023-03-15", "2023-03-15 12:00:00", "TRUE", "inline", "0.1"])
        self.assertEqual(sheets[0]["rows"][1], [])  # the blank row between data rows is kept
        self.assertEqual(sheets[0]["rows"][2], ["7"])


class VisionHelperTests(unittest.TestCase):
    def test_image_headers(self):
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAAEUlEQVR4nGP4z8DwHwQYGBgAOOkF+0F6WJQAAAAASUVORK5CYII=")
        self.assertEqual(vision.image_info(png)["width"], 2)
        self.assertEqual(vision.image_info(b"GIF89a" + struct.pack("<HH", 5, 7))["height"], 7)
        self.assertEqual(vision.image_info(b"BM" + b"\0" * 16 + struct.pack("<ii", 3, -4))["height"], 4)
        jpeg = b"\xff\xd8\xff\xe0" + struct.pack(">H", 4) + b"ab" + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, 30, 40) + b"\0" * 6
        self.assertEqual((vision.image_info(jpeg)["width"], vision.image_info(jpeg)["height"]), (40, 30))
        self.assertEqual(vision.mime_for(png), "image/png")
        self.assertEqual(vision.decode_data_url(vision.data_url(png)), png)
        self.assertIsNone(vision.decode_data_url("https://example.org/x.png"))
        self.assertTrue(vision.looks_vision_capable("openai/gpt-4o-mini"))
        self.assertFalse(vision.looks_vision_capable("z-ai/glm-5.2"))

    def test_ocr_reports_unavailable_or_runs_the_engine(self):
        with patch("dikdik.vision.ocr_engine", return_value=None):
            self.assertIsNone(vision.ocr(b"data"))
        completed = type("Run", (), {"returncode": 0, "stdout": "Hello\r\n".encode(), "stderr": b""})()
        with patch("dikdik.vision.ocr_engine", return_value="tesseract"), patch("dikdik.vision.subprocess.run", return_value=completed) as run:
            self.assertEqual(vision.ocr(b"\x89PNG\r\n\x1a\n")["text"], "Hello")
        self.assertEqual(run.call_args.args[0][0], "tesseract")
        failed = type("Run", (), {"returncode": 1, "stdout": b"", "stderr": b"No OCR language is installed"})()
        with patch("dikdik.vision.ocr_engine", return_value="windows"), patch("dikdik.vision.subprocess.run", return_value=failed):
            self.assertIn("No OCR language", vision.ocr(b"\x89PNG\r\n\x1a\n")["error"])

    def test_pdf_page_images_are_found(self):
        jpeg = b"\xff\xd8\xff\xd9"
        data = pdf([b"<< /Type /Catalog >>", b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /Filter /DCTDecode /Length 4 >>\nstream\n"
                    + jpeg + b"\nendstream"])
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "s.pdf").write_bytes(data)
            self.assertEqual(vision.document_images(Path(temp) / "s.pdf"), [("page-image-1.jpg", jpeg)])

    def test_writer_and_reader_agree_on_xlsx_and_text_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            documents.create(root, {"path": "t.xlsx", "content": "a,b\n1,2\n"})
            self.assertEqual(docextract.extract(root / "t.xlsx", full=True)["sheets"][0]["rows"], [["a", "b"], ["1", "2"]])
            documents.create(root, {"path": "notes.md", "content": "one\n"})
            self.assertTrue(documents.create(root, {"path": "notes.md", "content": "two\n", "append": True})["appended"])
            self.assertEqual((root / "notes.md").read_text(), "one\ntwo\n")
            documents.create(root, {"path": "m.docx", "content": "```mermaid\ngraph TD; A-->B\n```\n\n```chart\nnot json\n```\n\n![x](missing.png)"})
            text = docextract.document_text(root / "m.docx")
            self.assertIn("graph TD", text)
            self.assertIn("Chart could not be drawn", text)


if __name__ == "__main__":
    unittest.main()


class LegacyExcelTests(unittest.TestCase):
    """mixed.xls was saved by Microsoft Excel in the 97-2003 format (BIFF8 in an OLE2 file)."""

    FIXTURE = Path(__file__).parent / "fixtures" / "documents" / "mixed.xls"

    def test_cells_dates_formulas_booleans_errors_and_unicode(self):
        sheets = docextract.extract(self.FIXTURE, full=True)["sheets"]
        self.assertEqual([s["name"] for s in sheets], ["Mixed", "Second"])
        self.assertEqual(sheets[0]["rows"], [
            ["Name", "Date", "Score", "Total", "Passed", "Label"],
            ["Wanjiru Kamau", "2026-11-06", "3.75", "7.5", "TRUE", "Wanjiru Kamau ok"],
            ["\u00d6tzi \u00d1and\u00fa \u6f22\u5b57", "", "1000000", "2000000", "", "#DIV/0!"]])
        self.assertEqual(sheets[1]["rows"], [["x"], ["42"]])

    def test_statistics_and_preview_read_xls(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "m.xls").write_bytes(self.FIXTURE.read_bytes())
            result = stats.run(temp, {"path": "m.xls", "analysis": "descriptives", "columns": ["Score"]})
            self.assertEqual(result["descriptives"][0]["n"], 2)
            self.assertEqual(docextract.preview(Path(temp) / "m.xls")["kind"], "xlsx")

    def test_files_that_are_not_ole2_are_refused_clearly(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "fake.xls").write_text("a,b\n1,2\n")
            with self.assertRaisesRegex(ValueError, "not an Excel 97-2003 file"):
                docextract.extract(Path(temp) / "fake.xls")
