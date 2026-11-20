# Challenge 05 — Room booking overlaps

## Expected
- Meetings overlap if intervals touch at end? (exclusive end: [start, end) ).
- `can_book` rejects overlapping slots.
- Zero-length bookings rejected.

## Verify
Book 9–10 and 10–11 should **both** succeed with exclusive-end semantics.
