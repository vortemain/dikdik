"""Visual regression: compare preview screenshots with accepted baselines.

PNG decoding and encoding use only the standard library (8-bit RGB/RGBA, the format Chromium
writes). Two measures are reported: the share of pixels whose largest channel difference exceeds
a tolerance (catches small exact changes), and a structural-similarity score (SSIM) over 8x8
windows of a 2x-downscaled grayscale copy (tolerates anti-aliasing noise, drops for layout and
content changes). Baselines are accepted by the owner, never by the agent: the first capture of a
view is recorded as a pending baseline, and later captures that differ are reported for review.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
import sys
import time
import zlib
from contextlib import closing
from pathlib import Path

from . import migrations

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PIXELS = 4096 * 4096
PIXEL_TOLERANCE = 24          # largest channel difference (0-255) still counted as unchanged
MAX_CHANGED_PIXELS = 24       # more changed pixels than this is a change (one changed glyph is ~150)
MIN_SSIM = 0.985              # below this structural similarity is a change
FADE = bytes(255 - (255 - v) // 4 for v in range(256))


class PNGError(ValueError):
    pass


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode(data: bytes) -> tuple[int, int, bytes]:
    """Return (width, height, RGB bytes) for an 8-bit, non-interlaced RGB or RGBA PNG."""
    if not data.startswith(SIGNATURE):
        raise PNGError("Not a PNG image")
    offset, header, chunks = len(SIGNATURE), None, []
    while offset + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        if len(body) != length:
            raise PNGError("Truncated PNG chunk")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            chunks.append(body)
        elif kind == b"IEND":
            break
        offset += 12 + length
    if header is None:
        raise PNGError("PNG has no header")
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or color not in (2, 6) or interlace:
        raise PNGError("Only 8-bit, non-interlaced RGB or RGBA PNGs are supported")
    if not 0 < width * height <= MAX_PIXELS:
        raise PNGError("PNG dimensions are out of range")
    bpp = 3 if color == 2 else 4
    stride = width * bpp
    raw = zlib.decompress(b"".join(chunks))
    if len(raw) != height * (stride + 1):
        raise PNGError("PNG image data has the wrong size")
    out = bytearray(height * stride)
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        if kind == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 255
        elif kind == 2:
            row = bytearray(bytes((r + p) & 255 for r, p in zip(row, previous)))
        elif kind == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + previous[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                if i >= bpp:
                    row[i] = (row[i] + _paeth(row[i - bpp], previous[i], previous[i - bpp])) & 255
                else:
                    row[i] = (row[i] + previous[i]) & 255
        elif kind:
            raise PNGError("Unknown PNG filter type")
        out[y * stride:(y + 1) * stride] = row
        previous = row
    if bpp == 4:
        rgb = bytearray(width * height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = out[0::4], out[1::4], out[2::4]
        return width, height, bytes(rgb)
    return width, height, bytes(out)


def encode(width: int, height: int, rgb: bytes) -> bytes:
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def _gray_half(width: int, height: int, rgb: bytes) -> tuple[int, int, list[float]]:
    """2x2 box-downscaled luma, which keeps text strokes while halving SSIM work."""
    w, h = width // 2, height // 2
    r, g, b = rgb[0::3], rgb[1::3], rgb[2::3]
    luma = [0.299 * x + 0.587 * y + 0.114 * z for x, y, z in zip(r, g, b)]
    out = []
    for y in range(h):
        top, bottom = 2 * y * width, (2 * y + 1) * width
        out.extend((luma[top + 2 * x] + luma[top + 2 * x + 1] + luma[bottom + 2 * x] + luma[bottom + 2 * x + 1]) / 4 for x in range(w))
    return w, h, out


def ssim(width: int, height: int, a: bytes, b: bytes, window: int = 8) -> float:
    """Mean SSIM over non-overlapping windows of the downscaled luma images."""
    w, h, x = _gray_half(width, height, a)
    _, _, y = _gray_half(width, height, b)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    scores = []
    n = window * window
    for top in range(0, h - window + 1, window):
        for left in range(0, w - window + 1, window):
            xs = [x[(top + j) * w + left + i] for j in range(window) for i in range(window)]
            ys = [y[(top + j) * w + left + i] for j in range(window) for i in range(window)]
            mx, my = sum(xs) / n, sum(ys) / n
            vx = sum((v - mx) ** 2 for v in xs) / (n - 1)
            vy = sum((v - my) ** 2 for v in ys) / (n - 1)
            cov = sum((p - mx) * (q - my) for p, q in zip(xs, ys)) / (n - 1)
            scores.append(((2 * mx * my + c1) * (2 * cov + c2)) / ((mx * mx + my * my + c1) * (vx + vy + c2)))
    return round(sum(scores) / len(scores), 5) if scores else 1.0


def compare(baseline_png: bytes, candidate_png: bytes, *, tolerance: int = PIXEL_TOLERANCE,
            max_changed_pixels: int = MAX_CHANGED_PIXELS, min_ssim: float = MIN_SSIM) -> dict:
    """Compare two screenshots; returns the verdict, measures, changed region and a diff image."""
    bw, bh, base = decode(baseline_png)
    cw, ch, cand = decode(candidate_png)
    if (bw, bh) != (cw, ch):
        return {"verdict": "CHANGED", "reason": f"size changed from {bw}x{bh} to {cw}x{ch}",
                "changed_ratio": 1.0, "ssim": 0.0, "size": [cw, ch]}
    stride = cw * 3
    changed = 0
    differs = False
    rows = []
    box = [cw, ch, -1, -1]
    for y in range(ch):
        before, after = base[y * stride:(y + 1) * stride], cand[y * stride:(y + 1) * stride]
        # Fade unchanged pixels so the highlighted changes stand out.
        row = bytearray(after.translate(FADE))
        if before != after:
            differs = True
            for index in range(0, stride, 3):
                if max(abs(before[index] - after[index]), abs(before[index + 1] - after[index + 1]), abs(before[index + 2] - after[index + 2])) > tolerance:
                    changed += 1
                    row[index:index + 3] = b"\xff\x00\xff"
                    x = index // 3
                    box = [min(box[0], x), min(box[1], y), max(box[2], x), max(box[3], y)]
        rows.append(bytes(row))
    score = ssim(cw, ch, base, cand) if differs else 1.0  # also sees widespread changes below the pixel tolerance
    verdict = "CHANGED" if changed > max_changed_pixels or score < min_ssim else "MATCH"
    result = {"verdict": verdict, "changed_pixels": changed, "changed_ratio": round(changed / (cw * ch), 6), "ssim": score, "size": [cw, ch],
              "thresholds": {"pixel_tolerance": tolerance, "max_changed_pixels": max_changed_pixels, "min_ssim": min_ssim}}
    if changed:
        result["changed_region"] = {"x": box[0], "y": box[1], "width": box[2] - box[0] + 1, "height": box[3] - box[1] + 1}
        result["diff_png"] = encode(cw, ch, b"".join(rows))
    return result


def _schema(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS visual_baselines(
        workspace TEXT NOT NULL, view TEXT NOT NULL, state TEXT NOT NULL, png BLOB NOT NULL,
        sha256 TEXT NOT NULL, updated REAL NOT NULL, task_id TEXT,
        PRIMARY KEY(workspace, view, state))""")


STEPS = [(1, "visual baselines", _schema)]


class Baselines:
    """Accepted and pending screenshots per workspace and view (``path#label``)."""

    def __init__(self, path: Path, workspace: str):
        self.path, self.workspace = Path(path), workspace
        migrations.ensure(self.path, "visual", STEPS)

    def _connect(self):
        return closing(sqlite3.connect(self.path, timeout=30))

    def _get(self, view: str, state: str) -> bytes | None:
        with self._connect() as db:
            row = db.execute("SELECT png FROM visual_baselines WHERE workspace=? AND view=? AND state=?", (self.workspace, view, state)).fetchone()
        return row[0] if row else None

    def _put(self, view: str, state: str, png: bytes, task_id: str | None) -> None:
        with self._connect() as db, db:
            db.execute("INSERT OR REPLACE INTO visual_baselines VALUES(?,?,?,?,?,?,?)",
                       (self.workspace, view, state, png, hashlib.sha256(png).hexdigest(), time.time(), task_id))

    def check(self, view: str, png: bytes, task_id: str | None = None) -> dict:
        """Compare against the accepted baseline; store the capture as pending when it differs."""
        accepted = self._get(view, "accepted")
        if accepted is None:
            self._put(view, "pending", png, task_id)
            return {"view": view, "verdict": "NO_BASELINE", "detail": "Recorded as a pending baseline for the owner to accept."}
        if hashlib.sha256(accepted).digest() == hashlib.sha256(png).digest():
            return {"view": view, "verdict": "MATCH", "changed_ratio": 0.0, "ssim": 1.0}
        result = compare(accepted, png)
        if result["verdict"] == "CHANGED":
            self._put(view, "pending", png, task_id)
        return {"view": view, **result}

    def accept(self, view: str) -> bool:
        """Owner action: make the pending capture the accepted baseline."""
        with self._connect() as db, db:
            row = db.execute("SELECT png, task_id FROM visual_baselines WHERE workspace=? AND view=? AND state='pending'", (self.workspace, view)).fetchone()
            if row is None:
                return False
            db.execute("INSERT OR REPLACE INTO visual_baselines VALUES(?,?,?,?,?,?,?)",
                       (self.workspace, view, "accepted", row[0], hashlib.sha256(row[0]).hexdigest(), time.time(), row[1]))
            db.execute("DELETE FROM visual_baselines WHERE workspace=? AND view=? AND state='pending'", (self.workspace, view))
        return True

    def list(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT view, state, sha256, updated, task_id FROM visual_baselines WHERE workspace=? ORDER BY view, state", (self.workspace,)).fetchall()
        return [{"view": v, "state": s, "sha256": h, "updated": u, "task_id": t} for v, s, h, u, t in rows]


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    parser = argparse.ArgumentParser(prog="python -m dikdik.visual", description="Compare two PNG screenshots.")
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--diff", help="write a diff image here")
    args = parser.parse_args(argv)
    result = compare(Path(args.baseline).read_bytes(), Path(args.candidate).read_bytes())
    diff = result.pop("diff_png", None)
    if diff and args.diff:
        Path(args.diff).write_bytes(diff)
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "MATCH" else 1


if __name__ == "__main__":
    sys.exit(main())
