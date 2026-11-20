# Challenge 02 — Invoice totals with tax

## Expected after fix
- Line totals use decimal-safe rounding (2 places).
- Tax applies to subtotal after discounts, not before.
- Empty invoice returns zeros without crash.
- `add_line` does not share state between invoices.

## Verify
`pytest -q` — `total_with_tax([10, 20], tax=0.1)` → 33.0 not 33.0000000004.
