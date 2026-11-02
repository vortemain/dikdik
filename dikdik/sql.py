"""The only sanctioned way to put identifiers or fragments into SQL text.

Values always travel as `?` parameters. Column names, table names and fixed WHERE fragments
cannot be parameters, so they go through these helpers, which accept only names from an
explicit allowlist and raise otherwise. The security scanner treats an f-string or
concatenation as safe SQL only when every interpolated piece is a direct call to one of
these functions (`sql.ident`, `sql.assign`, `sql.columns`, `sql.marks`, `sql.clause`,
`sql.clauses`); anything else is reported as PY008.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def ident(name: str, allowed: Collection[str]) -> str:
    """One identifier from `allowed`."""
    if not isinstance(name, str) or name not in allowed or not IDENTIFIER.fullmatch(name):
        raise ValueError(f"Unsupported SQL identifier: {name!r}")
    return name


def columns(names: Iterable[str], allowed: Collection[str]) -> str:
    """`a,b,c` for an INSERT column list."""
    names = [ident(name, allowed) for name in names]
    if not names:
        raise ValueError("At least one column is required")
    return ",".join(names)


def assign(names: Iterable[str], allowed: Collection[str]) -> str:
    """`a=?, b=?` for an UPDATE SET list."""
    names = [ident(name, allowed) for name in names]
    if not names:
        raise ValueError("At least one column is required")
    return ", ".join(f"{name}=?" for name in names)


def marks(count: int) -> str:
    """`?,?,?` for `count` parameters."""
    if type(count) is not int or not 1 <= count <= 999:
        raise ValueError("Parameter count must be 1-999")
    return ",".join("?" * count)


def clause(text: str, allowed: Collection[str]) -> str:
    """One fixed WHERE fragment from `allowed` (its values are still `?` parameters)."""
    if not isinstance(text, str) or text not in allowed:
        raise ValueError(f"Unsupported SQL fragment: {text!r}")
    return text


def table_columns(db, table: str) -> frozenset[str]:
    """Every column the table really has (its schema is trusted; the name is a parameter)."""
    names = frozenset(row[0] for row in db.execute("SELECT name FROM pragma_table_info(?)", (table,)))
    if not names:
        raise ValueError(f"Unknown table: {table!r}")
    return names


def writable_columns(db, table: str, protected: Collection[str] = ("id", "created")) -> frozenset[str]:
    """The table's columns minus identity/creation columns that updates must never touch."""
    return table_columns(db, table) - set(protected)


def clauses(texts: Iterable[str], allowed: Collection[str], joiner: str = " AND ") -> str:
    if joiner not in (" AND ", " OR "):
        raise ValueError("Join SQL fragments with AND or OR")
    texts = [clause(text, allowed) for text in texts]
    if not texts:
        raise ValueError("At least one fragment is required")
    return joiner.join(texts)
