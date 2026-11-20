# Challenge 07 — Metric rollup windows

## Expected
- `average` raises or returns sentinel for empty lists (documented).
- `rate_per_minute` handles zero-duration window.
- `rolling_max` window size is inclusive of current point.

## Verify
`average([1,2,3])` → 2; empty → error or 0 per PROBLEM.
