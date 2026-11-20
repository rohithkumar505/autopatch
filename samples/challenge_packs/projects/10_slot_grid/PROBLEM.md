# Challenge 10 — Appointment slot grid

## Expected
- Slots are half-open [start, end); grid aligns to `step_minutes`.
- `book` rejects double booking.
- `day_capacity` counts free slots, not booked ones.

## Verify
`free_count(slots, booked)` matches number of unused aligned slots.
