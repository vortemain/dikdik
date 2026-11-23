"""Large tool results: a head and tail stay in the conversation, the full text goes to a file.

A long command log or web page is mostly read once. Keeping all of it in context makes every
later model call pay for it again; keeping only a preview loses it. Saving the full text by
content hash and leaving its path in the conversation costs one local read to go back to it.
"""

import hashlib
import json
from pathlib import Path
from typing import Callable

INLINE_LIMIT = 30 * 1024
HEAD_BYTES, TAIL_BYTES = 16000, 8000
FOLDER = "tool-output"


def saver(root: Path) -> Callable[[str], str]:
    """Save text under ``root/tool-output`` by content hash; return the path relative to root."""
    def save(text: str) -> str:
        data = text.encode("utf-8")
        relative = f"{FOLDER}/{hashlib.sha256(data).hexdigest()[:24]}.txt"
        target = Path(root) / relative
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
        return relative
    return save


def readable(result) -> str:
    """Long text fields as plain text after the rest as JSON, without JSON escaping."""
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True)
    long = {key: value for key, value in result.items() if isinstance(value, str) and len(value) > 500}
    rest = {key: value for key, value in result.items() if key not in long}
    return json.dumps(rest, ensure_ascii=False, indent=1, sort_keys=True) + "".join(f"\n\n--- {key} ---\n{value}" for key, value in sorted(long.items()))


def content(result, encoded: str, save: Callable[[str], str] | None = None) -> str:
    """The JSON observation when small; otherwise a bounded, readable form of the result."""
    return encoded if len(encoded.encode("utf-8")) <= INLINE_LIMIT else bounded(readable(result), save)


def bounded(text: str, save: Callable[[str], str] | None = None) -> str:
    """``text`` unchanged when small; otherwise a header, its head and its tail."""
    size = len(text.encode("utf-8"))
    if size <= INLINE_LIMIT:
        return text
    path = None
    if save is not None:
        try:
            path = save(text)
        except OSError:
            path = None
    where = (f" The full output is saved as `{path}`: open it with read_document (use find, or offset with "
             f"max_chars up to 20000) instead of running the tool again." if path else
             " The middle was not kept; narrow the request to see it.")
    data = text.encode("utf-8")
    head = data[:HEAD_BYTES].decode("utf-8", "ignore")
    tail = data[-TAIL_BYTES:].decode("utf-8", "ignore")
    header = (f"[Tool output was {size:,} bytes; showing the first {len(head):,} and last {len(tail):,} of its "
              f"{len(text):,} characters.{where}]")
    return f"{header}\n{head}\n[... {len(text) - len(head) - len(tail):,} characters omitted ...]\n{tail}"
