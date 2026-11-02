"""Structured, redacted operational logs: one JSON object per line in `<data>/logs/dikdik.jsonl`.

Logs are diagnostics for the owner, never an API: no route serves them. Every message, field and
traceback passes through `redact`, which masks known credential shapes (the same rules as the
security scanner), values registered at runtime (the session token, revealed provider keys)
and any field whose name says it holds a secret. Files rotate at 5 MB, three kept.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import threading
import time
import traceback
from pathlib import Path

from .security_scan import SECRET_RULES

LOGGER = "dikdik"
# "token" names a credential; "tokens" (input_tokens, total_tokens) counts usage and stays visible.
SECRET_FIELD = re.compile(r"(?i)authorization|password|passwd|secret|token(?!s)|api_?key|cookie|credential|private_?key")
_known: set[str] = set()
_lock = threading.Lock()
logging.getLogger(LOGGER).addHandler(logging.NullHandler())  # silent until configure(); never Python's stderr fallback


def register_secret(value) -> None:
    """Mask this exact value wherever it would appear in a log line."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str) and len(value) >= 8:
        with _lock:
            _known.add(value)


def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if isinstance(k, str) and SECRET_FIELD.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    with _lock:
        known = sorted(_known, key=len, reverse=True)
    for secret in known:
        value = value.replace(secret, "[REDACTED]")
    for rule, _, _, pattern in SECRET_RULES:
        value = pattern.sub(f"[REDACTED:{rule}]", value)
    return re.sub(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}", r"\1 [REDACTED]", value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
                 "level": record.levelname.lower(), "logger": record.name, "event": record.getMessage()}
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update({k: v for k, v in fields.items() if k not in entry})
        if record.exc_info:
            entry["exception"] = "".join(traceback.format_exception(*record.exc_info))[-8000:]
        return json.dumps(redact(entry), default=str, ensure_ascii=False)


def configure(data: Path, level: int = logging.INFO) -> Path:
    """Attach the rotating JSON file handler once per data directory. Returns the log path."""
    folder = Path(data) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "dikdik.jsonl"
    logger = logging.getLogger(LOGGER)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, "dikdik_path", None) == str(path):
            return path
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8", delay=True)
    handler.dikdik_path = str(path)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return path


def close(path: Path) -> None:
    """Detach and close the handler for `path` (Windows keeps open log files locked)."""
    logger = logging.getLogger(LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, "dikdik_path", None) == str(path):
            logger.removeHandler(handler)
            handler.close()


def log(event: str, level: int = logging.INFO, exc_info=None, **fields) -> None:
    logging.getLogger(LOGGER).log(level, event, exc_info=exc_info, extra={"fields": fields})
