VAT = 0.16


def invoice_total(subtotal, discount_percent=0):
    """Total due: the subtotal less the discount, plus VAT."""
    discounted = subtotal * (1 - discount_percent / 100)
    return round(discounted * (1 + VAT), 2)
