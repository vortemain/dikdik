import unittest

from text.slug import slugify


class SlugTests(unittest.TestCase):
    def test_simple_words(self):
        self.assertEqual(slugify("Hello World"), "hello-world")
