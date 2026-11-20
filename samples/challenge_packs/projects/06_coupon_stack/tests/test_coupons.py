from coupons import apply_codes

def test_percent_only():
    assert apply_codes(100, ["SAVE10"]) == 90
