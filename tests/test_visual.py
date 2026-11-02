"""Visual regression: PNG codec, comparison thresholds, owner-accepted baselines, the gate, a real preview."""

import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

from dikdik import engineering, local_preview, visual
from dikdik.interactive_browser import available


def image(width, height, paint=None):
    rgb = bytearray(b"\xf0\xf0\xf0" * width * height)
    for x, y, color in (paint or []):
        rgb[(y * width + x) * 3:(y * width + x) * 3 + 3] = color
    return bytes(rgb)


def block(x0, y0, size, color=b"\x10\x20\x30"):
    return [(x, y, color) for y in range(y0, y0 + size) for x in range(x0, x0 + size)]


def filtered_png(width, height, pixels, channels):
    """A reference encoder that uses every PNG filter type (row y uses filter y % 5)."""
    stride = width * channels
    rows, previous = [], bytes(stride)
    for y in range(height):
        row = pixels[y * stride:(y + 1) * stride]
        kind = y % 5
        out = bytearray()
        for i, value in enumerate(row):
            left = row[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            predictor = [0, left, up, (left + up) // 2, visual._paeth(left, up, corner)][kind]
            out.append((value - predictor) & 255)
        rows.append(bytes([kind]) + bytes(out))
        previous = row

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)
    return visual.SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")


class CodecTests(unittest.TestCase):
    def test_round_trip(self):
        pixels = bytes(range(256)) * 36  # 64 x 48 RGB of varied bytes
        self.assertEqual(visual.decode(visual.encode(64, 48, pixels)), (64, 48, pixels))

    def test_every_filter_type_and_rgba(self):
        width, height = 13, 11
        rgb = bytes((x * 7 + y * 13 + c * 29) % 256 for y in range(height) for x in range(width) for c in range(3))
        self.assertEqual(visual.decode(filtered_png(width, height, rgb, 3)), (width, height, rgb))
        rgba = bytes((x * 5 + y * 11 + c * 31) % 256 for y in range(height) for x in range(width) for c in range(4))
        expected = bytes(v for i, v in enumerate(rgba) if i % 4 != 3)
        self.assertEqual(visual.decode(filtered_png(width, height, rgba, 4)), (width, height, expected))

    def test_rejects_what_it_cannot_read(self):
        for data in (b"GIF89a", visual.SIGNATURE, visual.encode(2, 2, image(2, 2))[:-30]):
            with self.assertRaises(visual.PNGError):
                visual.decode(data)


class CompareTests(unittest.TestCase):
    base = visual.encode(200, 120, image(200, 120))

    def test_identical(self):
        self.assertEqual(visual.compare(self.base, self.base)["verdict"], "MATCH")

    def test_a_glyph_sized_change_is_caught_with_its_region(self):
        result = visual.compare(self.base, visual.encode(200, 120, image(200, 120, block(50, 40, 12))))
        self.assertEqual((result["verdict"], result["changed_pixels"]), ("CHANGED", 144))
        self.assertEqual(result["changed_region"], {"x": 50, "y": 40, "width": 12, "height": 12})
        width, height, diff = visual.decode(result["diff_png"])
        self.assertEqual(diff[(40 * width + 50) * 3:(40 * width + 50) * 3 + 3], b"\xff\x00\xff")

    def test_tiny_noise_and_subtle_shading_are_tolerated(self):
        # Screenshot-sized: SSIM is a mean over windows, so a page has thousands of them.
        noise = [(5, 5, b"\x00\x00\x00"), (350, 250, b"\x00\x00\x00")] + [(x, 10, b"\xe8\xe8\xe8") for x in range(400)]
        result = visual.compare(visual.encode(400, 300, image(400, 300)), visual.encode(400, 300, image(400, 300, noise)))
        self.assertEqual((result["verdict"], result["changed_pixels"]), ("MATCH", 2), result)

    def test_size_change(self):
        result = visual.compare(self.base, visual.encode(200, 121, image(200, 121)))
        self.assertEqual(result["verdict"], "CHANGED")
        self.assertIn("size changed", result["reason"])

    def test_a_layout_shift_lowers_ssim(self):
        shifted = visual.compare(visual.encode(200, 120, image(200, 120, block(20, 20, 60))),
                                 visual.encode(200, 120, image(200, 120, block(26, 20, 60))))
        self.assertEqual(shifted["verdict"], "CHANGED")
        self.assertLess(shifted["ssim"], 1.0)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "state.sqlite3"
        self.baselines = visual.Baselines(self.db, "/project")

    def tearDown(self):
        self.temp.cleanup()

    def test_owner_accepts_and_changes_are_held_for_review(self):
        first = visual.encode(40, 30, image(40, 30))
        second = visual.encode(40, 30, image(40, 30, block(5, 5, 10)))
        self.assertEqual(self.baselines.check("/#desktop", first)["verdict"], "NO_BASELINE")
        self.assertEqual([(b["view"], b["state"]) for b in self.baselines.list()], [("/#desktop", "pending")])
        self.assertTrue(self.baselines.accept("/#desktop"))
        self.assertFalse(self.baselines.accept("/#desktop"))  # nothing pending any more
        self.assertEqual(self.baselines.check("/#desktop", first)["verdict"], "MATCH")
        changed = self.baselines.check("/#desktop", second, task_id="t1")
        self.assertEqual(changed["verdict"], "CHANGED")
        self.assertEqual(self.baselines._get("/#desktop", "accepted"), first)  # the accepted look is untouched
        self.assertTrue(self.baselines.accept("/#desktop"))
        self.assertEqual(self.baselines.check("/#desktop", second)["verdict"], "MATCH")
        self.assertEqual(visual.Baselines(self.db, "/other").list(), [])  # per project


class GateTests(unittest.TestCase):
    def gate(self, verdicts):
        preview = {"visual": [{"view": f"/#{i}", "verdict": v} for i, v in enumerate(verdicts)], "accessibility": []}
        result = engineering.release_gate([], [], True, True, {"a.py": "x = 1\n"}, {"a.py": "x = 0\n"}, preview=preview)
        return next(g for g in result["gates"] if g["gate"] == "visual"), result["decision"]

    def test_statuses(self):
        self.assertEqual(self.gate(["MATCH", "MATCH"])[0]["status"], "PASS")
        gate, decision = self.gate(["MATCH", "CHANGED", "NO_BASELINE"])
        self.assertEqual((gate["status"], gate["views"], decision), ("RISK", ["/#1", "/#2"], "APPROVED_WITH_DOCUMENTED_RISK"))
        self.assertEqual(self.gate(["NO_BASELINE"])[0]["status"], "NOT_APPLICABLE")


PAGE_SERVER = '''
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Quiet).serve_forever()
'''


@unittest.skipUnless(available(), "Playwright is not installed")
class RealPreviewTests(unittest.TestCase):
    def test_preview_compares_real_screenshots_with_the_accepted_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "serve.py").write_text(PAGE_SERVER, encoding="utf-8")
            page = '<!doctype html><html lang="en"><title>Shop</title><main><h1 style="font:32px serif">Total: 40 KES</h1></main></html>'
            (root / "index.html").write_text(page, encoding="utf-8")
            baselines = visual.Baselines(root / "state.sqlite3", str(root))
            command = f'"{Path(sys.executable).as_posix()}" serve.py {{port}}'

            def run():
                return local_preview.preview(root, command, threading.Event(), visual=lambda view, png: baselines.check(view, png))

            first = run()
            self.assertEqual([v["verdict"] for v in first["visual"]], ["NO_BASELINE", "NO_BASELINE"], first)
            for view in ("/#desktop", "/#mobile"):
                baselines.accept(view)
            self.assertEqual([v["verdict"] for v in run()["visual"]], ["MATCH", "MATCH"])
            (root / "index.html").write_text(page.replace("40", "48"), encoding="utf-8")
            changed = run()["visual"]
            self.assertEqual([v["verdict"] for v in changed], ["CHANGED", "CHANGED"])
            self.assertLess(changed[0]["changed_region"]["width"], 100)  # only the changed digit, not the page


if __name__ == "__main__":
    unittest.main()
