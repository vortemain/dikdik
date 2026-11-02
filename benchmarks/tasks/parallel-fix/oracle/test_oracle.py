import unittest

from billing.invoice import invoice_total
from text.slug import slugify


class Oracle(unittest.TestCase):
    def test_discount_comes_before_vat(self):
        self.assertEqual(invoice_total(100, 10), 104.4)
        self.assertEqual(invoice_total(250, 0), 290.0)
        self.assertEqual(invoice_total(19.99, 15), 19.71)
        self.assertEqual(invoice_total(0, 50), 0.0)

    def test_slugs(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertEqual(slugify("  Many   spaces -- and___underscores "), "many-spaces-and-underscores")
        self.assertEqual(slugify("Version 2.0 Release"), "version-2-0-release")
        self.assertEqual(slugify("--already-slugged--"), "already-slugged")
        self.assertEqual(slugify("Hello World"), "hello-world")
