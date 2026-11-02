import unittest

from ages import parse_age


class Oracle(unittest.TestCase):
    def test_accepts_the_range_and_spaces(self):
        self.assertEqual(parse_age(" 7 "), 7)
        self.assertEqual(parse_age("0"), 0)
        self.assertEqual(parse_age("150"), 150)

    def test_rejects_everything_else(self):
        for bad in ("-1", "151", "12.5", "abc", "", "1e2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_age(bad)
