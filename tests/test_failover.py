"""Provider failover follows the fallback of the profile that will actually run."""

import tempfile
import time
import unittest
from pathlib import Path

from dikdik.engine import Engine
from dikdik.profiles import ProfileStore


class FailoverLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        (base / "repo").mkdir()
        self.engine = Engine(base / "repo", base / "data")
        self.addCleanup(self.engine.close)
        ProfileStore(self.engine.store)  # creates the versioned profiles schema
        with self.engine.store.connect() as db:
            for ident, default, fallback, created in [("default", 1, "backup", 1), ("chosen", 0, "chosen-backup", 2),
                                                      ("backup", 0, None, 3), ("chosen-backup", 0, None, 4)]:
                db.execute("INSERT INTO profiles(id,name,provider,model,endpoint,is_default,created,fallback_profile_id) "
                           "VALUES(?,?,?,?,?,?,?,?)", (ident, ident, "local", "m", "http://127.0.0.1:11434/v1", default, created, fallback))

    def test_the_default_profile_fails_over(self):
        # Regression: the query bound no parameter when no profile was chosen, raised, and the
        # error was silently swallowed, so the default profile never failed over.
        self.assertEqual(self.engine._fallback_profile(None), "backup")

    def test_a_chosen_profile_uses_its_own_fallback(self):
        # Regression: "id=? OR is_default=1 ORDER BY created" returned the older default profile's fallback.
        self.assertEqual(self.engine._fallback_profile("chosen"), "chosen-backup")

    def test_no_fallback_is_none(self):
        self.assertIsNone(self.engine._fallback_profile("backup"))


if __name__ == "__main__":
    unittest.main()
