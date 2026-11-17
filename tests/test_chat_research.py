"""Chat conversations: no project, attachments as real files, document and statistics tools, deliverables.

These run the real engine with a scripted model (no network): the scripted turns
are the tool calls a model makes when asked to analyze a thesis and its survey.
"""

import base64
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from dikdik import docextract, documents, stats
from dikdik.agent import ModelResponse, ToolCall
from dikdik.control import Attachments, attachment_file_name
from dikdik.engine import Engine, TERMINAL
from dikdik.store import CHAT_SCOPE


LIKERT = ["1 = Strongly Disagree", "2 = Disagree", "3 = Neutral", "4 = Agree", "5 = Strongly Agree"]


def survey_rows():
    rows = [["Timestamp", "1. Gender", "2. Trained?", "3. Staff have adequate skills", "4. Staff know safe practice",
             "5. Blood is available", "6. Services are timely"]]
    for i in range(40):
        trained = "Yes" if i % 3 else "No"
        skill = 4 if trained == "Yes" else 2
        rows.append([f"2026-11-0{1 + i % 9} 10:00:00", "Female" if i % 2 else "Male", trained,
                     LIKERT[min(4, skill + i % 2) - 1], LIKERT[min(4, skill + (i // 2) % 2) - 1],
                     LIKERT[(i % 5)], LIKERT[min(4, skill + i % 3 - 1)]])
    return rows


THESIS = """# CHAPTER ONE INTRODUCTION
## 1.4 Objectives of the Study
### 1.4.2 Specific Objectives
1. To assess staff knowledge and skills.
2. To assess availability of blood.

# CHAPTER THREE METHODOLOGY
## 3.9 Data Analysis
Descriptive statistics, chi-square and Pearson correlation were planned.
"""


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "survey.xlsx").write_bytes(documents.build_xlsx([{"name": "Form Responses 1", "rows": survey_rows()}]))

    def run_stats(self, **arguments):
        return stats.run(self.root, {"path": "survey.xlsx", **arguments})

    def test_overview_codes_likert_yes_no_and_keeps_timestamps_categorical(self):
        overview = {c["name"]: c for c in self.run_stats(analysis="overview")["columns"]}
        self.assertEqual(overview["3. Staff have adequate skills"]["type"], "ordinal")
        self.assertEqual(overview["3. Staff have adequate skills"]["coding"]["4 = Agree"], 4)
        self.assertEqual(overview["2. Trained?"]["type"], "binary")
        self.assertNotIn(overview["Timestamp"]["type"], {"ordinal", "numeric"})

    def test_columns_resolve_by_item_number_prefix_and_position(self):
        table = stats.load(self.root, {"path": "survey.xlsx"})
        self.assertEqual(table.header[table.index("3")], "3. Staff have adequate skills")
        self.assertEqual(table.header[table.index("#2")], "1. Gender")
        with self.assertRaisesRegex(ValueError, "No column matches"):
            table.index("nonexistent column")

    def test_likert_reports_items_composite_and_alpha(self):
        result = self.run_stats(analysis="likert", columns=["3", "4"])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["n"], 40)
        self.assertIsNotNone(result["reliability"]["alpha"])
        self.assertIn("interpretation", result["composite"])

    def test_crosstab_has_chi_square_and_fisher_for_two_by_two(self):
        result = self.run_stats(analysis="crosstab", row="1.", column="2.")
        self.assertEqual(result["n"], 40)
        self.assertEqual(result["chi_square"]["df"], 1)
        self.assertIn("fisher_exact_p", result)

    def test_distribution_functions_match_reference_values(self):
        # Reference values from SciPy: 2*t.sf(2.1, 17), chi2.sf(7.3, 3), f.sf(3.2, 2, 40).
        self.assertAlmostEqual(stats.p_t(2.1, 17), 0.050959847229134, places=9)
        self.assertAlmostEqual(stats.p_chi2(7.3, 3), 0.062926236459043, places=9)
        self.assertAlmostEqual(stats.p_f(3.2, 2, 40), 0.051385456071628, places=9)

    def test_compare_regression_and_correlation_run_on_composites(self):
        skills = {"mean_of": ["3", "4"], "name": "Skills"}
        compare = self.run_stats(analysis="compare", outcome=skills, group="2.")
        self.assertIn("welch_t_test", compare)
        self.assertIn("mann_whitney_u", compare)
        correlation = self.run_stats(analysis="correlation", columns=[skills, "6."])
        self.assertIn("p", correlation["pearson"]["Skills"]["6. Services are timely"])
        regression = self.run_stats(analysis="regression", outcome="6.", predictors=[skills, "5."])
        self.assertEqual(regression["n"], 40)
        self.assertEqual([c["term"] for c in regression["coefficients"]], ["(Intercept)", "Skills", "5. Blood is available"])
        logistic = self.run_stats(analysis="regression", model="logistic", outcome="2.", predictors=["5.", "1."])
        self.assertEqual(logistic["outcome_coding"]["positive"], "Yes")
        self.assertTrue(logistic["converged"])
        separated = self.run_stats(analysis="regression", model="logistic", outcome="2.", predictors=[skills])
        self.assertTrue(separated["separation"])  # reported as a finding, not a crash

    def test_filters_keep_matching_rows(self):
        result = self.run_stats(analysis="frequencies", columns=["1."], where=[{"column": "2.", "equals": "Yes"}])
        self.assertEqual(result["frequencies"][0]["valid_n"], 26)
        self.assertIn("filters", result)

    def test_paths_cannot_leave_the_folder_or_open_secrets(self):
        for path in ["../x.csv", ".env", "secrets.csv"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                stats.run(self.root, {"path": path, "analysis": "overview"})


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_docx_round_trip_keeps_headings_tables_lists_and_charts(self):
        markdown = THESIS + """
Table 4.1: Respondents

| Gender | n | % |
|---|---:|---:|
| Female | 20 | 50.0 |

```chart
{"type": "column", "title": "Gender", "categories": ["Female", "Male"], "series": [{"name": "n", "values": [20, 20]}]}
```
Figure 4.1: Gender of respondents

- point one
  - nested
"""
        result = documents.create(self.root, {"path": "out/report.docx", "content": markdown, "title": "Report", "toc": True, "title_page": True})
        self.assertEqual(result["charts"], 1)
        self.assertEqual(result["tables"], 1)
        with zipfile.ZipFile(self.root / "out" / "report.docx") as archive:
            names = archive.namelist()
            self.assertIn("word/charts/chart1.xml", names)
            self.assertIn("word/embeddings/Microsoft_Excel_Worksheet1.xlsx", names)
            document = archive.read("word/document.xml").decode()
        self.assertIn('w:val="Caption"', document)
        self.assertIn("TOC \\o", document)
        text = docextract.document_text(self.root / "out" / "report.docx")
        self.assertIn("## 1.4 Objectives of the Study", text)
        self.assertIn("| Female | 20 | 50.0 |", text)

    def test_read_document_pages_finds_and_outlines(self):
        documents.create(self.root, {"path": "thesis.docx", "content": THESIS + ("\nFiller paragraph. " * 3000)})
        first = documents.read(self.root, {"path": "thesis.docx", "max_chars": 2000})
        self.assertIn("# CHAPTER ONE INTRODUCTION", first["outline"])
        self.assertIsNotNone(first["next_offset"])
        found = documents.read(self.root, {"path": "thesis.docx", "find": "specific objectives"})
        self.assertGreaterEqual(found["matches"], 1)
        self.assertIn("To assess staff knowledge", found["passages"][0]["text"])

    def test_xlsx_writer_reads_back_with_types_and_all_columns(self):
        wide = [[f"Q{i}" for i in range(60)], list(range(60))]
        documents.create(self.root, {"path": "wide.xlsx", "sheets": [{"name": "Data", "rows": wide}]})
        sheet = docextract.extract(self.root / "wide.xlsx", full=True)["sheets"][0]
        self.assertEqual(len(sheet["rows"][0]), 60)  # the viewer limit (40 columns) no longer cuts the model's view
        self.assertEqual(sheet["rows"][1][59], "59")

    def test_text_formats_and_refusals(self):
        self.assertEqual(documents.create(self.root, {"path": "notes.md", "content": "# Hi"})["saved"], "notes.md")
        with self.assertRaises(ValueError):
            documents.create(self.root, {"path": "data.json", "content": "{not json"})
        with self.assertRaises(ValueError):
            documents.create(self.root, {"path": "../escape.md", "content": "x"})


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        from dikdik.store import Store
        self.store = Store(self.base / "state.sqlite3")
        self.attachments = Attachments(self.store, self.base / "attachments")

    def add(self, name, data, kind="application/octet-stream"):
        return self.attachments.add({"name": name, "type": kind, "data": base64.b64encode(data).decode()})["id"]

    def test_documents_stored_by_id_are_read_by_their_real_name(self):
        # Regression: files are stored without an extension, which used to make every
        # .docx/.xlsx attachment fail with "No document reader".
        docx, _ = documents.build_docx(THESIS, root=self.base)
        ident = self.add("Research_Project.docx", docx)
        _, _, text = self.attachments.resolve([ident])
        self.assertIn("Specific Objectives", text)
        self.assertNotIn("could not read", text)

    def test_materialize_copies_with_safe_names(self):
        ident = self.add("_Survey – Responses (5).xlsx", documents.build_xlsx([{"name": "S", "rows": [["a"], ["1"]]}]))
        placed = self.attachments.materialize([ident], self.base / "chat" / "attachments")
        self.assertEqual(placed[0]["path"], "_Survey – Responses (5).xlsx")
        self.assertTrue((self.base / "chat" / "attachments" / placed[0]["path"]).is_file())
        self.assertEqual(attachment_file_name("../evil:name?.pdf"), "_evil_name_.pdf")
        self.assertEqual(attachment_file_name(".env"), "env")


class ChatConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "mobile-app"
        self.project.mkdir()
        (self.project / "app.json").write_text("{}")
        self.engine = Engine(self.project, self.base / "data")
        self.addCleanup(self.engine.close)

    def wait(self, task):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            data = self.engine.store.get(task)
            if data["state"] in TERMINAL and not self.engine.busy(task):
                return data
            time.sleep(0.02)
        self.fail("chat did not finish")

    def attach(self, name, data):
        return self.engine.attachments.add({"name": name, "type": "application/octet-stream",
                                            "data": base64.b64encode(data).decode()})["id"]

    def test_thesis_analysis_runs_in_the_chats_own_folder_and_delivers_a_word_file(self):
        thesis, _ = documents.build_docx(THESIS, root=self.base)
        ids = [self.attach("Research_Project.docx", thesis),
               self.attach("Responses.xlsx", documents.build_xlsx([{"name": "Form Responses 1", "rows": survey_rows()}]))]
        observations = []
        turns = iter([
            ModelResponse("Reading your files.", [ToolCall("1", "read_document", {"path": "attachments/Research_Project.docx"}),
                                                  ToolCall("2", "statistics", {"path": "attachments/Responses.xlsx", "analysis": "overview"})]),
            ModelResponse("", [ToolCall("3", "statistics", {"path": "attachments/Responses.xlsx", "analysis": "crosstab", "row": "1.", "column": "2."}),
                               ToolCall("4", "statistics", {"path": "attachments/Responses.xlsx", "analysis": "likert", "columns": ["nope"]})]),
            ModelResponse("", [ToolCall("5", "create_document", {"path": "Chapter_4_and_5.docx", "content": "# CHAPTER FOUR\n## 4.1 Results\nText."})]),
            ModelResponse("Done: Chapter_4_and_5.docx", []),
        ])

        def respond(messages, tools):
            observations.append([m for m in messages if m.get("role") == "tool"])
            names = {tool["function"]["name"] for tool in tools}
            self.assertTrue({"read_document", "statistics", "create_document"} <= names)
            return next(turns)
        model = Mock()
        model.respond.side_effect = respond
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            task = self.engine.submit({"objective": "Analyze my thesis and write chapters 4 and 5", "mode": "chat",
                                       "verification": "syntax", "approve_external": True, "attachments": ids})
            result = self.wait(task)
        self.assertEqual(result["state"], "completed", result.get("result"))
        folder = self.base / "data" / "chats" / task
        self.assertTrue((folder / "attachments" / "Responses.xlsx").is_file())
        self.assertTrue((folder / "Chapter_4_and_5.docx").is_file())
        self.assertFalse((self.project / "Chapter_4_and_5.docx").exists())  # the open project is never touched
        tool_text = json.dumps(observations[-1])
        self.assertIn("Specific Objectives", tool_text)            # the document was read in full
        self.assertIn("chi_square", tool_text)                     # statistics ran
        self.assertIn("No column matches", tool_text)              # a bad call explains itself to the model
        first_prompt = json.dumps(model.respond.call_args_list[0].args[0])
        self.assertIn("attachments/Responses.xlsx", first_prompt)
        self.assertNotIn("could not read", first_prompt)
        kinds = [event["kind"] for event in result["events"]]
        self.assertIn("file_created", kinds)
        self.assertEqual([f["path"] for f in result["result"]["chat_files"]], ["Chapter_4_and_5.docx"])
        self.assertEqual(self.engine.chat_file(task, "Chapter_4_and_5.docx").name, "Chapter_4_and_5.docx")
        with self.assertRaises(KeyError):
            self.engine.chat_file(task, "../../state.sqlite3")
        self.assertEqual(result["result"]["turn_changed"], False)  # a chat never changes the project
        self.engine.delete(task)
        self.assertFalse(folder.exists())  # deleting the chat deletes its files too

    def test_chats_belong_to_no_project_and_are_listed_from_every_project(self):
        model = Mock()
        model.respond.return_value = ModelResponse("Hello!", [])
        with patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            chat = self.engine.submit({"objective": "Explain p-values", "mode": "chat", "verification": "syntax", "approve_external": True})
            self.wait(chat)
        with self.engine.store.connect() as db:
            self.assertEqual(db.execute("SELECT workspace FROM tasks WHERE id=?", (chat,)).fetchone()[0], CHAT_SCOPE)
        other = self.base / "other-project"
        other.mkdir()
        self.engine.store.bind_workspace(other)
        listed = {task["id"]: task for task in self.engine.store.list()}
        self.assertIn(chat, listed)
        self.assertEqual(listed[chat]["scope"], "chat")


if __name__ == "__main__":
    unittest.main()


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_word_preview_keeps_formatting_lists_charts_and_pictures(self):
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAAEUlEQVR4nGP4z8DwHwQYGBgAOOkF+0F6WJQAAAAASUVORK5CYII=")
        (self.root / "fig.png").write_bytes(png)
        markdown = ("# Results\nSome **bold** and *italic* text.\n\n1. first\n2. second\n\n- point\n\n"
                    "```chart\n{\"type\": \"bar\", \"title\": \"Means\", \"categories\": [\"A\", \"B\"], \"series\": [{\"name\": \"m\", \"values\": [3.5, 4]}]}\n```\n\n"
                    "![Figure 1: picture](fig.png)\n")
        documents.create(self.root, {"path": "r.docx", "content": markdown})
        view = docextract.preview(self.root / "r.docx")
        blocks = view["blocks"]
        bold = next(b for b in blocks if b.get("runs"))
        self.assertIn({"t": "bold", "b": 1}, bold["runs"])
        self.assertEqual([b["marker"] for b in blocks if b["type"] == "li"], ["1.", "2.", "•"])
        chart = next(b for b in blocks if b["type"] == "chart")["chart"]
        self.assertEqual(chart, {"type": "bar", "title": "Means", "categories": ["A", "B"], "series": [{"name": "m", "values": [3.5, 4.0]}]})
        part = next(b for b in blocks if b["type"] == "image")["part"]
        data, kind = docextract.media(self.root / "r.docx", part)
        self.assertEqual((data, kind), (png, "image/png"))
        for bad in ["word/document.xml", "../secret.png", "word/media/../../x.png"]:
            with self.subTest(part=bad), self.assertRaises(KeyError):
                docextract.media(self.root / "r.docx", bad)


class ImagesForTextOnlyModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        (self.base / "p").mkdir()
        self.engine = Engine(self.base / "p", self.base / "data")
        self.addCleanup(self.engine.close)
        self.png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAAEUlEQVR4nGP4z8DwHwQYGBgAOOkF+0F6WJQAAAAASUVORK5CYII=")

    def test_rejected_images_become_text_readings_in_the_agent_loop(self):
        from dikdik.agent import _drop_images
        messages = [{"role": "user", "content": [{"type": "text", "text": "What does it say?"},
                                                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]
        self.assertTrue(_drop_images(messages, describe=lambda urls: [f"[Image 1] TEXT {len(urls)}"]))
        self.assertEqual(messages[0]["content"][-1]["text"], "[Image 1] TEXT 1")
        self.assertNotIn("image_url", json.dumps(messages))

    def test_image_reading_uses_on_device_ocr_and_remembers_text_only_models(self):
        with patch("dikdik.engine.vision.ocr", return_value={"engine": "Windows OCR", "text": "Table 4.2\r\nTrained: 52"}):
            readings = self.engine.describe_image_urls(["data:image/png;base64," + base64.b64encode(self.png).decode()], "glm-5.2")
        self.assertIn("Trained: 52", readings[0])
        self.assertIn("2×1 px", readings[0])
        self.assertIn("glm-5.2", self.engine._text_only_models())

    def test_chat_converts_images_up_front_for_a_known_text_only_model(self):
        self.engine._remember_text_only("text-only-model")
        ident = self.engine.attachments.add({"name": "shot.png", "type": "image/png", "data": base64.b64encode(self.png).decode()})["id"]
        model = Mock()
        model.respond.return_value = ModelResponse("It says 52.", [])
        with patch("dikdik.engine.vision.ocr", return_value={"engine": "Windows OCR", "text": "Trained: 52"}), \
                patch.object(self.engine.models, "describe", return_value={"configured": True, "model": "text-only-model"}), \
                patch("dikdik.engine.CompatibleAgentModel", return_value=model):
            task = self.engine.submit({"objective": "Read my screenshot", "mode": "chat", "verification": "syntax",
                                       "approve_external": True, "attachments": [ident]})
            deadline = time.monotonic() + 30
            while self.engine.store.get(task)["state"] not in TERMINAL or self.engine.busy(task):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
        sent = json.dumps(model.respond.call_args_list[0].args[0])
        self.assertIn("Trained: 52", sent)
        self.assertNotIn("image_url", sent)  # no failed request with an image first

    def test_read_image_tool_reads_files_and_document_pictures(self):
        folder = self.base / "chat"
        folder.mkdir()
        (folder / "fig.png").write_bytes(self.png)
        documents.create(folder, {"path": "r.docx", "content": "# Hi\n\n![Figure 1](fig.png)\n"})
        with patch("dikdik.engine.vision.ocr", return_value={"engine": "Tesseract", "text": "hello"}):
            direct = self.engine.read_image_tool(folder, {"path": "fig.png"})
            listed = self.engine.read_image_tool(folder, {"path": "r.docx"})
            inside = self.engine.read_image_tool(folder, {"path": "r.docx", "index": 1})
        self.assertEqual(direct["ocr_text"], "hello")
        self.assertEqual(listed["images"], 1)
        self.assertEqual(inside["width"], 2)

    def test_scanned_pdf_is_read_with_ocr(self):
        import zlib
        pixels = zlib.compress(b"\xff\xff\xff" * 4)
        pdf = (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
               b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /XObject << /Im1 4 0 R >> >> >>\nendobj\n"
               b"4 0 obj\n<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length "
               + str(len(pixels)).encode() + b" >>\nstream\n" + pixels + b"\nendstream\nendobj\ntrailer << /Root 1 0 R >>\n%%EOF")
        (self.base / "scan.pdf").write_bytes(pdf)
        with patch("dikdik.vision.ocr_engine", return_value="windows"), \
                patch("dikdik.vision.ocr", return_value={"engine": "Windows OCR", "text": "Response rate 91%"}):
            result = documents.read(self.base, {"path": "scan.pdf"})
        self.assertIn("Response rate 91%", result["text"])
