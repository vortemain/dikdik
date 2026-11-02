import unittest

from billing.invoice import invoice_total


class InvoiceTests(unittest.TestCase):
    def test_no_discount(self):
        self.assertEqual(invoice_total(100), 116.0)
