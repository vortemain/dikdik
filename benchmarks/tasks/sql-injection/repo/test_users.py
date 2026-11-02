import unittest

from users import connect, find_user


class Users(unittest.TestCase):
    def test_finds_a_user(self):
        self.assertEqual(find_user(connect(), "bob"), ("bob", "user"))
