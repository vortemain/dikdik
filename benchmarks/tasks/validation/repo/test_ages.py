import unittest

from ages import parse_age


class Ages(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(parse_age("42"), 42)
