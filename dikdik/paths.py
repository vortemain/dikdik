"""Keeping a user-given path inside a folder.

Normalised with realpath (links resolved) and checked with a plain prefix test: the form
static analysers recognise as a path-traversal guard, used by every file tool.
"""
import os
from pathlib import Path


def contained(base: str | Path, relative: str | Path) -> Path:
    """``relative`` resolved inside ``base``; ValueError if the result would leave it."""
    root = os.path.realpath(base)
    full = os.path.realpath(os.path.join(root, relative))
    if not full.startswith(root):
        raise ValueError("Path must stay inside the working folder")
    if len(full) > len(root) and full[len(root)] != os.sep and not root.endswith(os.sep):
        raise ValueError("Path must stay inside the working folder")  # a sibling such as work-other beside work
    return Path(full)
