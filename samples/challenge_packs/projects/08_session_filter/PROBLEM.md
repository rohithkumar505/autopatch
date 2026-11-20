# Challenge 08 — Session query filter

## Expected
- Filter expression must be parsed safely (no code execution).
- Role check uses equality, not substring match.
- Expired sessions never returned.

## Verify
`query(sessions, "role=admin")` never matches `role=administrator`.
