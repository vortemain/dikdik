import unittest

from pricing import discount


class DiscountTests(unittest.TestCase):
    def test_ten_percent_off(self):
        self.assertEqual(discount(200, 10), 180)

    def test_no_discount(self):
        self.assertEqual(discount(99, 0), 99)


if __name__ == "__main__":
    unittest.main()
