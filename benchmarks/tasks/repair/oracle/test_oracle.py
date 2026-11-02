import unittest

from calculator import add


class Oracle(unittest.TestCase):
    def test_signs_and_zero(self):
        self.assertEqual(add(-1, 1), 0)
        self.assertEqual(add(0, 0), 0)

    def test_floats(self):
        self.assertAlmostEqual(add(0.5, 0.25), 0.75)
