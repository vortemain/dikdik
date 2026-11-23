"""Keeping a user-given path inside a folder.

Normalised with realpath (links resolved) and checked with a prefix test: the form
static analysers recognise as a path-traversal guard, used by every file tool.
"""
import os
from pathlib import Path


def contained(base: str | Path, relative: str | Path) -> Path:
    """``relative`` resolved inside ``base``; ValueError if the result would leave it."""
    root = os.path.realpath(base)
    full = os.path.realpath(os.path.join(root, relative))
    prefix = root if root.endswith(os.sep) else root + os.sep
    if full != root and not full.startswith(prefix):
        raise ValueError("Path must stay inside the working folder")
    return Path(full)
