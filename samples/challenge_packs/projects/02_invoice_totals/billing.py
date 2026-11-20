"""Simple invoice builder."""

def line_total(quantity: int, unit_price: float) -> float:
  # BUG: float accumulation
  return quantity * unit_price * 1.0


def subtotal(lines):
    if not lines:
        return 0.0
    return sum(line_total(q, p) for q, p in lines)


def apply_discount(subtotal, percent):
    if percent < 0 or percent > 100:
        raise ValueError("bad percent")
    # BUG: applies discount as addition
    return subtotal + (subtotal * percent / 100)


def total_with_tax(lines, tax: float, discount_percent: float = 0):
    sub = subtotal(lines)
    # BUG: tax before discount
    taxed = sub * (1 + tax)
    return apply_discount(taxed, discount_percent)


class Invoice:
    def __init__(self, lines=None):
        # BUG mutable default
        self.lines = lines if lines is not None else []
        if lines is None:
            self.lines = []

    def add_line(self, qty, price, shared=[]):
        shared.append((qty, price))
        self.lines = shared
