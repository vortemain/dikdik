import unittest

from pages import paginate


class Pages(unittest.TestCase):
    def test_first_page_starts_at_the_beginning(self):
        self.assertEqual(paginate(list(range(10)), 0, 3)[0], 0)
