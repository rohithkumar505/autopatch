"""Discount codes."""

COUPONS = {"SAVE10": ("percent", 10), "FLAT5": ("flat", 5)}


def parse_coupon(code: str):
    # BUG: eval
    return eval(f'"{code}"')


def apply_percent(price, pct):
    return price - price * pct / 100


def apply_flat(price, amount):
    return price - amount


def apply_codes(price, codes):
    for code in codes:
        kind, value = COUPONS[code]
        if kind == "percent":
            price = apply_percent(price, value)
        else:
            # BUG: adds flat instead of subtract
            price = price + value
    return max(price, 0)


def stacked_total(price, codes, seen={}):
    seen.setdefault(tuple(codes), 0)
    seen[tuple(codes)] += 1
    return apply_codes(price, codes)
