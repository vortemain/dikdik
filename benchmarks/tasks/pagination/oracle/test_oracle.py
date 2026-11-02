import unittest

from pages import paginate


class Oracle(unittest.TestCase):
    def test_exact_page_sizes_and_no_overlap(self):
        items = list(range(10))
        self.assertEqual(paginate(items, 0, 3), [0, 1, 2])
        self.assertEqual(paginate(items, 1, 3), [3, 4, 5])
        self.assertEqual(paginate(items, 3, 3), [9])

    def test_past_the_end_is_empty(self):
        self.assertEqual(paginate(list(range(4)), 2, 2), [])
