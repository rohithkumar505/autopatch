# Challenge 06 — Coupon stacking

## Expected
- Percent then flat discounts apply in documented order.
- `stacked_total` never below zero.
- `parse_coupon` must not execute user input.

## Verify
`apply_codes(100, ["SAVE10", "FLAT5"])` matches EXPECTED_OUTCOMES.md.
