from billing import Invoice, subtotal, total_with_tax

def test_subtotal():
    assert subtotal([(2, 5.0), (1, 10.0)]) == 20.0

def test_tax_simple():
    assert total_with_tax([(1, 100)], tax=0.1) == 110.0

def test_invoice():
    inv = Invoice()
    inv.add_line(1, 9.99)
    assert len(inv.lines) == 1
