"""Let every model use images, including text-only ones.

Many good models (GLM, Nemotron, DeepSeek, most local models) cannot take image
input. Instead of dropping the user's screenshot, scanned page or chart, Dikdik
turns it into text the model can reason over, in layers that need nothing
installed:

1. OCR on this computer: the OCR engine built into Windows 10/11
   (Windows.Media.Ocr, called through PowerShell), or Tesseract when it is on PATH
   (macOS/Linux). Reads printed text in screenshots, photos and scanned pages.
2. A vision helper: any configured model profile that does accept images is asked
   to transcribe and describe the image (text, tables, chart values, diagrams).
3. Plain facts: format and pixel size, so the model at least knows what it got.

`image_info`, `ocr` and `describe_prompt` are pure helpers; the engine owns which
model profile serves as the helper.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

OCR_TIMEOUT = 60
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                  ".webp": "image/webp", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff"}
# Model names that usually accept images; tried first when choosing a helper automatically.
VISION_HINT = re.compile(r"gpt-4o|gpt-4\.1|gpt-5|o3|o4|claude|gemini|gemma-3|vision|-vl|vl-|qwen.*vl|llava|pixtral|"
                         r"llama-3\.2-(11|90)b|llama-4|grok-(2-vision|4)|mistral-(medium|small)-3|kimi-vl|internvl|minicpm-v|phi-4-multimodal", re.I)

DESCRIBE_PROMPT = (
    "You are the eyes for a colleague who cannot see this image. Report everything useful, faithfully and without "
    "guessing:\n"
    "1. What it is (screenshot, photo, scanned page, chart, table, diagram, handwriting, form...).\n"
    "2. ALL text in it, transcribed exactly, in reading order (keep numbers, units and punctuation).\n"
    "3. Tables: every row as a Markdown table. Charts: type, title, axes, every category with its value, and the trend.\n"
    "4. Diagrams/UI: the elements and how they connect, or what the screen shows and any error messages.\n"
    "5. Anything uncertain or unreadable, said plainly.\n"
    "Be complete and structured; do not add opinions.")

READ_IMAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_image",
        "description": (
            "Read an image as text, for models that cannot see images (and to double-check ones that can): an image file "
            "(PNG, JPEG, GIF, WebP, BMP, TIFF) or a picture inside a document (.docx, .pptx, .xlsx, or the page images of "
            "a scanned PDF; call without index to list them). Returns the text in it read by OCR on this computer and, "
            "when a model that can see is set up, a full description: tables, chart values, diagrams, screenshots."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Image or document path, relative to the working folder."},
                "index": {"type": "integer", "description": "For a document: which embedded picture (1-based)."},
                "question": {"type": "string", "description": "Optional: what you need to know from the image."},
            },
            "required": ["path"],
        },
    },
}

_OCR_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) { $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:DIKDIK_OCR_FILE)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($engine -eq $null) { throw 'No OCR language is installed' }
if ($bitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or $bitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) {
  throw 'Image is larger than the Windows OCR limit'
}
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
foreach ($line in $result.Lines) { [Console]::WriteLine($line.Text) }
"""


def image_info(data: bytes) -> dict:
    """Format and pixel size from the file header (PNG, JPEG, GIF, WebP, BMP, TIFF)."""
    info: dict = {"bytes": len(data)}
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        info.update(format="png", width=struct.unpack(">I", data[16:20])[0], height=struct.unpack(">I", data[20:24])[0])
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        info.update(format="gif", width=width, height=height)
    elif data[:2] == b"BM" and len(data) >= 26:
        width, height = struct.unpack("<ii", data[18:26])
        info.update(format="bmp", width=width, height=abs(height))
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        info["format"] = "webp"
        if data[12:16] == b"VP8X" and len(data) >= 30:
            info.update(width=1 + int.from_bytes(data[24:27], "little"), height=1 + int.from_bytes(data[27:30], "little"))
    elif data[:2] == b"\xff\xd8":
        info["format"] = "jpeg"
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            length = struct.unpack(">H", data[index + 2:index + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[index + 5:index + 9])
                info.update(width=width, height=height)
                break
            index += 2 + length
    elif data[:4] in (b"II*\x00", b"MM\x00*"):
        info["format"] = "tiff"
    return info


def mime_for(data: bytes) -> str:
    return {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
            "tiff": "image/tiff"}.get(image_info(data).get("format", ""), "application/octet-stream")


def data_url(data: bytes) -> str:
    return f"data:{mime_for(data)};base64,{base64.b64encode(data).decode()}"


def decode_data_url(url: str) -> bytes | None:
    match = re.match(r"data:image/[\w.+-]+;base64,(.+)$", url or "", re.S)
    if not match:
        return None
    try:
        return base64.b64decode(match.group(1))
    except ValueError:
        return None


def ocr_engine() -> str | None:
    """Which OCR engine this computer has, without running it."""
    if sys.platform == "win32" and shutil.which("powershell.exe"):
        return "windows"
    if shutil.which("tesseract"):
        return "tesseract"
    return None


def ocr(data: bytes, timeout: int = OCR_TIMEOUT) -> dict | None:
    """Printed text in an image, read on this computer. None when no OCR engine is available."""
    engine = ocr_engine()
    if engine is None or not data or len(data) > MAX_IMAGE_BYTES:
        return None
    fmt = image_info(data).get("format") or "png"
    handle, name = tempfile.mkstemp(suffix="." + {"jpeg": "jpg"}.get(fmt, fmt), prefix="dikdik-ocr-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        if engine == "windows":
            env = {**os.environ, "DIKDIK_OCR_FILE": name}
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            run = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _OCR_PS],
                                 capture_output=True, timeout=timeout, env=env, creationflags=flags)
        else:
            run = subprocess.run(["tesseract", name, "stdout"], capture_output=True, timeout=timeout)
        if run.returncode != 0:
            return {"engine": engine, "text": "", "error": run.stderr.decode("utf-8", "replace").strip().splitlines()[-1:][0][:300]
                    if run.stderr.strip() else "OCR failed"}
        text = run.stdout.decode("utf-8", "replace").strip()
        return {"engine": "Windows OCR" if engine == "windows" else "Tesseract", "text": text[:60_000]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"engine": engine, "text": "", "error": str(error)[:300]}
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def looks_vision_capable(model: str) -> bool:
    return bool(VISION_HINT.search(model or ""))


# --- images inside documents --------------------------------------------------------

def document_images(path: Path, limit: int = 60) -> list[tuple[str, bytes]]:
    """Embedded pictures of a .docx/.pptx/.xlsx (media parts) or a PDF (JPEG and Flate images), in order."""
    suffix = path.suffix.lower()
    found: list[tuple[str, bytes]] = []
    if suffix in {".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".epub"}:
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist(), key=_natural):
                if re.search(r"(^|/)(media|Pictures|images?)/[^/]+\.(png|jpe?g|gif|bmp|tiff?|webp)$", name, re.I):
                    found.append((name.rsplit("/", 1)[-1], archive.read(name)))
                    if len(found) >= limit:
                        break
        return found
    if suffix == ".pdf":
        data = path.read_bytes()
        for index, match in enumerate(re.finditer(rb"/Subtype\s*/Image", data)):
            start = data.rfind(b"obj", 0, match.start())
            stream = data.find(b"stream", match.end())
            if start < 0 or stream < 0:
                continue
            header = data[start:stream]
            body_start = stream + len(b"stream")
            body_start += 2 if data[body_start:body_start + 2] == b"\r\n" else 1
            end = data.find(b"endstream", body_start)
            raw = data[body_start:end]
            if b"/DCTDecode" in header:
                found.append((f"page-image-{len(found) + 1}.jpg", raw.rstrip(b"\r\n")))
            elif b"/FlateDecode" in header and b"/DeviceRGB" in header or b"/DeviceGray" in header and b"/FlateDecode" in header:
                png = _flate_image_to_png(header, raw)
                if png:
                    found.append((f"page-image-{len(found) + 1}.png", png))
            if len(found) >= limit:
                break
    return found


def _natural(name: str):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def _flate_image_to_png(header: bytes, raw: bytes) -> bytes | None:
    """A PDF Flate image (8-bit RGB or gray, no predictor) as a PNG, so OCR can read it."""
    try:
        width = int(re.search(rb"/Width\s+(\d+)", header).group(1))
        height = int(re.search(rb"/Height\s+(\d+)", header).group(1))
        bits = int(re.search(rb"/BitsPerComponent\s+(\d+)", header).group(1))
    except (AttributeError, ValueError):
        return None
    if bits != 8 or b"/DecodeParms" in header or width * height > 40_000_000:
        return None
    channels = 3 if b"/DeviceRGB" in header else 1
    try:
        pixels = zlib.decompress(raw)
    except zlib.error:
        return None
    stride = width * channels
    if len(pixels) < stride * height:
        return None
    rows = b"".join(b"\x00" + pixels[row * stride:(row + 1) * stride] for row in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    color = 2 if channels == 3 else 0
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))
