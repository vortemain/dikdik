"""Tests for dikdik/docextract.py — document text extraction for attachments and viewers."""
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from dikdik import docextract


class DocExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_kind_for_csv(self):
        self.assertEqual(docextract.kind_for("data.csv"), "table")
        self.assertEqual(docextract.kind_for("data.tsv"), "table")

    def test_kind_for_docx(self):
        self.assertEqual(docextract.kind_for("report.docx"), "docx")

    def test_kind_for_xlsx(self):
        self.assertEqual(docextract.kind_for("report.xlsx"), "xlsx")
        self.assertEqual(docextract.kind_for("report.xlsm"), "xlsx")

    def test_kind_for_pptx(self):
        self.assertEqual(docextract.kind_for("slides.pptx"), "pptx")

    def test_kind_for_pdf(self):
        self.assertEqual(docextract.kind_for("doc.pdf"), "pdf")

    def test_kind_for_unknown(self):
        self.assertIsNone(docextract.kind_for("notes.txt"))
        self.assertIsNone(docextract.kind_for("image.png"))
        self.assertIsNone(docextract.kind_for("readme.md"))

    def test_kind_for_no_extension(self):
        self.assertIsNone(docextract.kind_for("README"))

    def test_extract_csv(self):
        path = self.root / "data.csv"
        path.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")
        result = docextract.extract(path)
        self.assertEqual(result["kind"], "table")
        self.assertEqual(result["rows"][0], ["name", "age"])
        self.assertTrue(result["has_header"])

    def test_extract_tsv(self):
        path = self.root / "data.tsv"
        path.write_text("x\ty\n1\t10\n2\t20\n3\t30\n", encoding="utf-8")
        result = docextract.extract(path)
        self.assertEqual(result["kind"], "table")
        self.assertEqual(len(result["rows"]), 4)  # header + 3 data rows

    def test_extract_empty_csv(self):
        path = self.root / "empty.csv"
        path.write_text("", encoding="utf-8")
        result = docextract.extract(path)
        self.assertEqual(result["kind"], "table")
        self.assertEqual(result["rows"], [])

    def test_extract_docx(self):
        path = self.root / "test.docx"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", (
                '<?xml version="1.0"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '</Types>'))
            zf.writestr("_rels/.rels", (
                '<?xml version="1.0"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '</Relationships>'))
            zf.writestr("word/document.xml", (
                '<?xml version="1.0"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:p><w:r><w:t>Hello World</w:t></w:r></w:p>'
                '</w:document>'))
        result = docextract.extract(path)
        self.assertEqual(result["kind"], "docx")
        self.assertIn("blocks", result)

    def test_extract_pdf_text(self):
        path = self.root / "test.pdf"
        # Minimal PDF with a text stream
        content = b"BT /F1 12 Tf 100 700 Td (Hello PDF) Tj ET"
        pdf = (
            b"%PDF-1.0\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n"
        )
        pdf += b"4 0 obj<</Length " + str(len(content)).encode() + b">>stream\n" + content + b"\nendstream\nendobj\n"
        path.write_bytes(pdf)
        result = docextract.extract(path)
        self.assertEqual(result["kind"], "pdf")

    def test_extract_nonexistent_raises(self):
        with self.assertRaises((ValueError, FileNotFoundError, OSError)):
            docextract.extract(self.root / "missing.docx")

    def test_attachment_text_csv(self):
        path = self.root / "notes.csv"
        path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
        text = docextract.attachment_text(path)
        self.assertIn("1", text)
        self.assertIn("a", text)

    def test_attachment_text_txt(self):
        path = self.root / "notes.txt"
        path.write_text("Hello, world!", encoding="utf-8")
        text = docextract.attachment_text(path)
        self.assertIn("Hello", text)

    def test_attachment_text_empty(self):
        path = self.root / "empty.txt"
        path.write_text("", encoding="utf-8")
        text = docextract.attachment_text(path)
        self.assertIn("no readable text", text.lower())

    def test_extract_invalid_docx_raises(self):
        path = self.root / "bad.docx"
        path.write_bytes(b"not a zip file")
        with self.assertRaises((zipfile.BadZipFile, ValueError)):
            docextract.extract(path)

    def test_extract_invalid_pdf(self):
        path = self.root / "bad.pdf"
        path.write_bytes(b"not a pdf at all")
        result = docextract.extract(path)
        # Should still return a result with kind "pdf" and empty text
        self.assertEqual(result["kind"], "pdf")

    def test_extract_xlsx(self):
        path = self.root / "test.xlsx"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", (
                '<?xml version="1.0"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '</Types>'))
            zf.writestr("_rels/.rels", (
                '<?xml version="1.0"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '</Relationships>'))
            zf.writestr("xl/workbook.xml", (
                '<?xml version="1.0"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
                '</workbook>'))
        result = docextract.extract(path)
        self.assertEqual(result["kind"], "xlsx")

    def test_extract_pptx(self):
        path = self.root / "test.pptx"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", (
                '<?xml version="1.0"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                '</Types>'))
            zf.writestr("_rels/.rels", (
                '<?xml version="1.0"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
                '</Relationships>'))
            zf.writestr("ppt/presentation.xml", (
                '<?xml version="1.0"?>'
                '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                '</p:presentation>'))
        result = docextract.extract(path)
        self.assertEqual(result["kind"], "pptx")


if __name__ == "__main__":
    unittest.main()