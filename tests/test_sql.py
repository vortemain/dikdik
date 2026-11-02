"""The SQL identifier helpers accept exactly their allowlists and boundaries (mutation-tested)."""

import sqlite3
import unittest
from contextlib import closing

from dikdik import sql


class SqlHelperTests(unittest.TestCase):
    def test_ident(self):
        self.assertEqual(sql.ident("name", {"name"}), "name")
        for bad, allowed in (("name; DROP TABLE t", {"name; DROP TABLE t"}), ("id", {"name"}), (3, {3}), ("9a", {"9a"})):
            with self.assertRaises(ValueError):
                sql.ident(bad, allowed)

    def test_columns_and_assign(self):
        self.assertEqual(sql.columns(["a", "b"], {"a", "b"}), "a,b")
        self.assertEqual(sql.assign(["a", "b"], {"a", "b"}), "a=?, b=?")
        for helper in (sql.columns, sql.assign):
            with self.assertRaises(ValueError):
                helper([], {"a"})
            with self.assertRaises(ValueError):
                helper(["a", "c"], {"a"})

    def test_marks_boundaries(self):
        self.assertEqual(sql.marks(1), "?")
        self.assertEqual(sql.marks(999).count("?"), 999)
        for bad in (0, 1000, True, 2.0):
            with self.assertRaises(ValueError):
                sql.marks(bad)

    def test_clause_and_clauses(self):
        self.assertEqual(sql.clause("state=?", {"state=?"}), "state=?")
        self.assertEqual(sql.clauses(["a=?", "b=?"], {"a=?", "b=?"}), "a=? AND b=?")
        self.assertEqual(sql.clauses(["a=?", "b=?"], {"a=?", "b=?"}, " OR "), "a=? OR b=?")
        for bad in (lambda: sql.clause("1=1 OR 1", {"1=1"}), lambda: sql.clause(None, {None}),
                    lambda: sql.clauses([], {"a=?"}), lambda: sql.clauses(["a=?"], {"a=?"}, "; ")):
            with self.assertRaises(ValueError):
                bad()

    def test_table_columns_reads_the_real_schema(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE t (id TEXT PRIMARY KEY, created REAL, name TEXT, enabled INTEGER)")
            self.assertEqual(sql.table_columns(db, "t"), {"id", "created", "name", "enabled"})
            self.assertEqual(sql.writable_columns(db, "t"), {"name", "enabled"})
            self.assertEqual(sql.writable_columns(db, "t", protected=("name",)), {"id", "created", "enabled"})
            with self.assertRaises(ValueError):
                sql.table_columns(db, "missing")


if __name__ == "__main__":
    unittest.main()
