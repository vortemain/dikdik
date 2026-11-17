"""Read legacy Excel 97-2003 workbooks (.xls) with the standard library only.

An .xls file is an OLE2 compound file holding a "Workbook" stream of BIFF8
records. This reader walks the container, collects the shared strings, number
formats and cell records (text, numbers, RK numbers, booleans, errors and the
cached results of formulas), and returns the same shape as the .xlsx reader:
{"kind": "xlsx", "sheets": [{"name", "rows", "truncated"}]}. Dates come back as
ISO text, like the .xlsx reader. Encrypted workbooks are refused with a clear note.
"""

from __future__ import annotations

import datetime
import re
import struct
from pathlib import Path

FREE, END_OF_CHAIN = 0xFFFFFFFF, 0xFFFFFFFE
DATE_FORMATS = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}
ERRORS = {0x00: "#NULL!", 0x07: "#DIV/0!", 0x0F: "#VALUE!", 0x17: "#REF!", 0x1D: "#NAME?", 0x24: "#NUM!", 0x2A: "#N/A"}


class _Compound:
    """Just enough of the OLE2 / CFB format to read a named stream."""

    def __init__(self, data: bytes):
        if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError("This is not an Excel 97-2003 file (no OLE2 header). If it is really .xlsx or .csv, rename it.")
        self.data = data
        self.sector = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_sector = 1 << struct.unpack_from("<H", data, 0x20)[0]
        fat_count = struct.unpack_from("<I", data, 0x2C)[0]
        directory_start = struct.unpack_from("<I", data, 0x30)[0]
        self.cutoff = struct.unpack_from("<I", data, 0x38)[0]
        mini_fat_start = struct.unpack_from("<I", data, 0x3C)[0]
        difat_start, difat_count = struct.unpack_from("<II", data, 0x44)
        fat_sectors = [s for s in struct.unpack_from("<109I", data, 0x4C) if s not in (FREE, END_OF_CHAIN)]
        next_difat, seen = difat_start, 0
        while next_difat not in (FREE, END_OF_CHAIN) and seen < difat_count + 1 and seen < 10_000:
            block = self._raw(next_difat)
            entries = struct.unpack_from(f"<{self.sector // 4}I", block)
            fat_sectors += [s for s in entries[:-1] if s not in (FREE, END_OF_CHAIN)]
            next_difat = entries[-1]
            seen += 1
        fat_sectors = fat_sectors[:fat_count] if fat_count else fat_sectors
        self.fat: list[int] = []
        for number in fat_sectors:
            self.fat.extend(struct.unpack_from(f"<{self.sector // 4}I", self._raw(number)))
        directory = self._chain(directory_start)
        self.entries = []
        for offset in range(0, len(directory) - 127, 128):
            entry = directory[offset:offset + 128]
            length = struct.unpack_from("<H", entry, 64)[0]
            name = entry[:max(length - 2, 0)].decode("utf-16-le", "ignore")
            kind = entry[66]
            start, size = struct.unpack_from("<IQ", entry, 116)
            self.entries.append({"name": name, "type": kind, "start": start, "size": size & 0xFFFFFFFF})
        root = next((e for e in self.entries if e["type"] == 5), None)
        self.mini_stream = self._chain(root["start"]) if root and root["start"] not in (FREE, END_OF_CHAIN) else b""
        self.mini_fat: list[int] = []
        if mini_fat_start not in (FREE, END_OF_CHAIN):
            chain = self._chain(mini_fat_start)
            self.mini_fat = list(struct.unpack_from(f"<{len(chain) // 4}I", chain))

    def _raw(self, number: int) -> bytes:
        start = (number + 1) * self.sector
        return self.data[start:start + self.sector]

    def _chain(self, start: int) -> bytes:
        out, current, guard = [], start, 0
        while current not in (FREE, END_OF_CHAIN) and current < len(self.fat) and guard < 1_000_000:
            out.append(self._raw(current))
            current = self.fat[current]
            guard += 1
        return b"".join(out)

    def stream(self, *names: str) -> bytes:
        wanted = {n.lower() for n in names}
        entry = next((e for e in self.entries if e["type"] == 2 and e["name"].lower() in wanted), None)
        if entry is None:
            raise ValueError("No workbook stream was found in this file.")
        if entry["size"] < self.cutoff:
            out, current, guard = [], entry["start"], 0
            while current not in (FREE, END_OF_CHAIN) and current < len(self.mini_fat) and guard < 1_000_000:
                start = current * self.mini_sector
                out.append(self.mini_stream[start:start + self.mini_sector])
                current = self.mini_fat[current]
                guard += 1
            return b"".join(out)[:entry["size"]]
        return self._chain(entry["start"])[:entry["size"]]


def _records(stream: bytes, start: int = 0):
    offset = start
    while offset + 4 <= len(stream):
        kind, length = struct.unpack_from("<HH", stream, offset)
        yield offset, kind, stream[offset + 4:offset + 4 + length]
        offset += 4 + length


class _Pieces:
    """Reads Unicode strings that run across CONTINUE records (each one restarts the high-byte flag)."""

    def __init__(self, chunks: list[bytes]):
        self.chunks, self.index, self.pos = chunks, 0, 0

    def _advance(self) -> None:
        if self.pos >= len(self.chunks[self.index]) and self.index + 1 < len(self.chunks):
            self.index += 1
            self.pos = 0

    def take(self, size: int) -> bytes:
        out = b""
        while size > 0:
            self._advance()
            chunk = self.chunks[self.index]
            part = chunk[self.pos:self.pos + size]
            if not part:
                break
            out += part
            self.pos += len(part)
            size -= len(part)
        return out

    def string(self) -> str:
        count = struct.unpack("<H", self.take(2))[0]
        flags = self.take(1)[0]
        rich = struct.unpack("<H", self.take(2))[0] if flags & 0x08 else 0
        extended = struct.unpack("<I", self.take(4))[0] if flags & 0x04 else 0
        wide = flags & 0x01
        out = []
        remaining = count
        while remaining > 0:
            self._advance()
            available = len(self.chunks[self.index]) - self.pos
            if available <= 0:
                break
            per = 2 if wide else 1
            n = min(remaining, available // per) if available >= per else 0
            if n == 0:
                break
            raw = self.take(n * per)
            out.append(raw.decode("utf-16-le", "replace") if wide else raw.decode("latin-1"))
            remaining -= n
            if remaining > 0:  # the string continues in the next CONTINUE record, which starts with a new flags byte
                self.index += 1
                self.pos = 0
                if self.index >= len(self.chunks):
                    break
                wide = self.take(1)[0] & 0x01
        self.take(rich * 4 + extended)
        return "".join(out)


def _short_string(data: bytes, offset: int, length_bytes: int = 1) -> tuple[str, int]:
    count = data[offset] if length_bytes == 1 else struct.unpack_from("<H", data, offset)[0]
    offset += length_bytes
    flags = data[offset]
    offset += 1
    if flags & 0x01:
        return data[offset:offset + count * 2].decode("utf-16-le", "replace"), offset + count * 2
    return data[offset:offset + count].decode("latin-1"), offset + count


def _rk(value: int) -> float:
    if value & 0x02:
        number = float(value >> 2 if not value & 0x80000000 else (value >> 2) - (1 << 30))
    else:
        number = struct.unpack("<d", struct.pack("<Q", (value & 0xFFFFFFFC) << 32))[0]
    return number / 100 if value & 0x01 else number


def _number(value: float, is_date: bool, with_time: bool, date1904: bool) -> str:
    if is_date and 0 < value < 2_958_466:
        moment = datetime.datetime(1904 if date1904 else 1899, 1 if date1904 else 12, 1 if date1904 else 30) + datetime.timedelta(days=value)
        return moment.strftime("%Y-%m-%d %H:%M:%S" if with_time and value % 1 else "%Y-%m-%d")
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(round(value, 10))


def read(path: str | Path, limits: dict) -> dict:
    data = Path(path).read_bytes()
    book = _Compound(data).stream("Workbook", "Book")
    sheets_meta, chunks, formats, xf_formats = [], [], {}, []
    date1904 = False
    globals_done = False
    for offset, kind, body in _records(book):
        if kind == 0x2F:
            raise ValueError("This workbook is password-protected; open it in Excel, remove the password and save it again.")
        if kind == 0x85 and len(body) >= 8:  # BOUNDSHEET
            position = struct.unpack_from("<I", body, 0)[0]
            name, _ = _short_string(body, 6)
            sheets_meta.append({"name": name, "offset": position, "visible": body[4] == 0, "type": body[5]})
        elif kind == 0xFC:  # SST
            chunks = [body[8:]]
        elif kind == 0x3C and chunks and not globals_done:  # CONTINUE of the SST
            chunks.append(body)
        elif kind in (0x41E,) and len(body) >= 5:  # FORMAT
            code, _ = _short_string(body, 2, 2)
            formats[struct.unpack_from("<H", body, 0)[0]] = code
        elif kind == 0xE0 and len(body) >= 4:  # XF
            xf_formats.append(struct.unpack_from("<H", body, 2)[0])
        elif kind == 0x22 and len(body) >= 2:
            date1904 = struct.unpack_from("<H", body, 0)[0] == 1
        elif kind not in (0x3C,) and chunks:
            globals_done = True
        if kind == 0x0A and offset > 0:  # EOF of the globals substream
            break
    shared: list[str] = []
    if chunks:
        pieces = _Pieces(chunks)
        try:
            total = struct.unpack_from("<I", book, next(o for o, k, _ in _records(book) if k == 0xFC) + 8)[0]
            for _ in range(total):
                shared.append(pieces.string())
        except (struct.error, IndexError, StopIteration):
            pass

    def date_style(xf: int) -> tuple[bool, bool]:
        if xf >= len(xf_formats):
            return False, False
        fmt = xf_formats[xf]
        code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", formats.get(fmt, "")).lower()
        is_date = fmt in DATE_FORMATS or bool(code and re.search(r"[dy]", code) and not re.search(r"[#0]", code))
        return is_date, is_date and (fmt in {22, 45, 46, 47} or bool(re.search(r"h|ss", code)))

    sheets = []
    for meta in sheets_meta[: limits["sheets"]]:
        if meta["type"] != 0:  # chart sheets and macro sheets hold no cells
            continue
        cells: dict[int, dict[int, str]] = {}
        pending_formula = None
        truncated = False

        def put(row: int, col: int, value: str) -> None:
            nonlocal truncated
            if row >= limits["sheet_rows"] or col >= limits["sheet_cols"]:
                truncated = True
                return
            cells.setdefault(row, {})[col] = value
        for offset, kind, body in _records(book, meta["offset"]):
            if kind == 0x0A:
                break
            if kind == 0xFD and len(body) >= 10:  # LABELSST
                row, col, _, index = struct.unpack_from("<HHHI", body)
                put(row, col, shared[index] if index < len(shared) else "")
            elif kind == 0x203 and len(body) >= 14:  # NUMBER
                row, col, xf = struct.unpack_from("<HHH", body)
                put(row, col, _number(struct.unpack_from("<d", body, 6)[0], *date_style(xf), date1904))
            elif kind == 0x27E and len(body) >= 10:  # RK
                row, col, xf, value = struct.unpack_from("<HHHI", body)
                put(row, col, _number(_rk(value), *date_style(xf), date1904))
            elif kind == 0xBD and len(body) >= 6:  # MULRK
                row, first = struct.unpack_from("<HH", body)
                count = (len(body) - 6) // 6
                for i in range(count):
                    xf, value = struct.unpack_from("<HI", body, 4 + i * 6)
                    put(row, first + i, _number(_rk(value), *date_style(xf), date1904))
            elif kind in (0x204, 0xD6) and len(body) >= 8:  # LABEL / RSTRING
                row, col = struct.unpack_from("<HH", body)
                text, _ = _short_string(body, 6, 2)
                put(row, col, text)
            elif kind == 0x205 and len(body) >= 8:  # BOOLERR
                row, col, _, value, is_error = struct.unpack_from("<HHHBB", body)
                put(row, col, ERRORS.get(value, "#ERROR") if is_error else ("TRUE" if value else "FALSE"))
            elif kind == 0x06 and len(body) >= 20:  # FORMULA: its cached result
                row, col, xf = struct.unpack_from("<HHH", body)
                result = body[6:14]
                if result[6:8] == b"\xff\xff":
                    if result[0] == 0:
                        pending_formula = (row, col)
                    elif result[0] == 1:
                        put(row, col, "TRUE" if result[2] else "FALSE")
                    elif result[0] == 2:
                        put(row, col, ERRORS.get(result[2], "#ERROR"))
                else:
                    put(row, col, _number(struct.unpack("<d", result)[0], *date_style(xf), date1904))
            elif kind == 0x207 and pending_formula and len(body) >= 3:  # STRING after a text formula
                text, _ = _short_string(body, 0, 2)
                put(*pending_formula, text)
                pending_formula = None
        rows: list[list[str]] = []
        if cells:
            last = max(cells)
            for row in range(last + 1):
                values = cells.get(row, {})
                rows.append([values.get(col, "") for col in range(max(values) + 1)] if values else [])
        sheets.append({"name": meta["name"], "rows": rows, "truncated": truncated})
    return {"kind": "xlsx", "sheets": sheets or [{"name": "Sheet 1", "rows": [], "truncated": False}]}
