"""Accessibility checks find known WCAG failures in static HTML and in a real rendered preview."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik import accessibility
from dikdik.interactive_browser import available

BAD = """<!doctype html><html><head></head><body>
<img src="logo.png">
<input id="email" placeholder="Email">
<button></button>
<a href="/next"></a>
<h1>Title</h1><h3>Skipped a level</h3>
<p id="dup" style="color:#aaaaaa;background:#ffffff">Low contrast text</p><span id="dup">again</span>
<button style="width:10px;height:10px;padding:0;border:0;outline:none" aria-label="tiny">x</button>
</body></html>"""

GOOD = """<!doctype html><html lang="en"><head><title>Sign in</title></head><body>
<main><h1>Sign in</h1>
<img src="logo.png" alt="Dikdik">
<label for="email">Email</label><input id="email" type="email" style="min-height:32px">
<button style="min-width:48px;min-height:32px">Continue</button>
<p>Read the <a href="/terms">terms</a>.</p>
<a href="/home" style="display:inline-block;min-height:32px"><img src="home.png" alt="Home"></a>
</main></body></html>"""


PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                      "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def rules(violations, impact=None):
    return {v["rule"] for v in violations if impact is None or v["impact"] == impact}


class StaticHtmlTests(unittest.TestCase):
    def test_known_failures_are_found(self):
        found = accessibility.check_html(BAD)
        self.assertEqual(rules(found, "serious"), {"image-alt", "control-label", "button-name", "link-name", "html-lang", "document-title"})
        self.assertEqual(rules(found, "moderate"), {"heading-order", "duplicate-id"})

    def test_an_accessible_page_passes(self):
        self.assertEqual(accessibility.check_html(GOOD), [])

    def test_names_from_images_labels_and_aria(self):
        page = ('<html lang="en"><title>t</title><a href="/"><span><img alt="Home"></span></a>'
                '<button aria-label="Close">×</button><label>Name <input></label><input aria-label="Search">')
        self.assertEqual(accessibility.check_html(page), [])

    def test_fragments_do_not_require_document_level_elements(self):
        self.assertEqual(accessibility.check_html('<div><img src="x.png" alt=""></div>'), [])

    def test_summary_fails_only_on_high_confidence_rules(self):
        summary = accessibility.summarize({"violations": [
            {"rule": "image-alt", "impact": "serious", "count": 2}, {"rule": "color-contrast", "impact": "moderate", "count": 3}]})
        self.assertEqual(summary, {"serious": 2, "moderate": 3, "failing_rules": ["image-alt"]})


@unittest.skipUnless(available(), "Playwright is not installed")
class RenderedPreviewTests(unittest.TestCase):
    """The real preview path: a supervised local server, Chromium, the in-page audit."""

    def run_preview(self, html):
        from dikdik.local_preview import preview
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "page.html").write_text(html, encoding="utf-8")
            for name in ("logo.png", "home.png"):  # real 1x1 images, so no resource fails to load
                (root / name).write_bytes(PIXEL)
            command = f'"{Path(sys.executable).as_posix()}" -m http.server {{port}} --bind 127.0.0.1'
            return preview(root, command, threading.Event(), path="/page.html")

    def test_failures_are_measured_in_the_rendered_page(self):
        result = self.run_preview(BAD)
        self.assertEqual(result["status"], "failed", result.get("error"))
        desktop = next(a for a in result["accessibility"] if a["label"] == "desktop")
        found = rules(desktop["violations"])
        self.assertTrue({"image-alt", "control-label", "button-name", "link-name", "html-lang", "document-title"} <= found, found)
        self.assertTrue({"color-contrast", "heading-order", "duplicate-id", "target-size", "focus-visible"} <= found, found)
        contrast = next(v for v in desktop["violations"] if v["rule"] == "color-contrast")
        self.assertIn("2.32:1", contrast["examples"][0]["detail"])  # #aaa on white, computed in the browser
        self.assertTrue(any("accessibility failures" in issue for issue in result["issues"]))

    def test_an_accessible_page_passes_the_preview(self):
        result = self.run_preview(GOOD)
        self.assertEqual(result["status"], "passed", result.get("issues") or result.get("error"))
        for audit in result["accessibility"]:
            self.assertEqual(audit["serious"], 0, audit)


if __name__ == "__main__":
    unittest.main()
