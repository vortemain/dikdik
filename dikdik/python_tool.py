"""run_python: the Python case of run_code, kept so earlier transcripts and callers keep working."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from .code_tool import (CALLABLE, MAX_CALLS, OUTPUT_CAP, PYTHON_HELPER as HELPER, ToolBridge,  # noqa: F401
                        check_python as check_code, python_interpreter as interpreter)
from . import code_tool


def run(root: Path, code: str, cancel: threading.Event, *, timeout: float = 120, workdir=None,
        call_tool: Callable[[str, dict], dict] | None = None, allowed=(), on_chunk=None,
        env: dict[str, str] | None = None, limits: dict | None = None) -> dict:
    """Run Python ``code`` in ``root`` (or its ``workdir``); ``call_tool(name, arguments)`` serves the allowed tools."""
    return code_tool.run(root, "python", code, cancel, timeout=timeout, workdir=workdir, call_tool=call_tool,
                         allowed=allowed, on_chunk=on_chunk, env=env, limits=limits)
