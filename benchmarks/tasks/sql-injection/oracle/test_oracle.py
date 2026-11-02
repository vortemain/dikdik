import unittest

from users import connect, find_user


class Oracle(unittest.TestCase):
    def test_injection_returns_nothing(self):
        self.assertIsNone(find_user(connect(), "x' OR '1'='1"))
        self.assertIsNone(find_user(connect(), "bob' --"))

    def test_quotes_in_real_names_still_work(self):
        db = connect()
        db.execute("INSERT INTO users VALUES (?, ?)", ("o'neil", "user"))
        self.assertEqual(find_user(db, "o'neil"), ("o'neil", "user"))
        self.assertEqual(find_user(db, "ada"), ("ada", "admin"))
