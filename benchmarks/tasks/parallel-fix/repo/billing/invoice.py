VAT = 0.16


def invoice_total(subtotal, discount_percent=0):
    """Total due: the subtotal less the discount, plus VAT."""
    with_vat = subtotal * (1 + VAT)
    return round(with_vat - subtotal * discount_percent / 100, 2)
