# Challenge 04 — Priority inbox

## Expected
- Highest priority messages first; tie-break by oldest `received_at`.
- `urgent_count` includes priority >= 3 (inclusive).
- `load_filters` does not leak filters between users.

## Verify
`top_messages(msgs, 2)` returns two highest-priority ids.
