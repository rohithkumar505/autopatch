# Challenge 01 — Token bucket rate limiter

## Situation
A small API gateway uses a token bucket to cap requests per client.

## Broken behaviour
- Bursts may be rejected when tokens should still be available.
- Refill math does not match the documented rate.
- Negative or zero limits are not rejected.

## Expected after a correct fix
- `consume("a", 3)` with capacity 10 and rate 2/s refills predictably.
- Request is allowed iff enough tokens exist after refill (inclusive boundary).
- Invalid `capacity` or `rate` raises `ValueError`.

## Verify
```bash
pytest -q
```
Sample: after `refill_all(1.0)`, client with 8 tokens can `consume(3)` → `True` and 5 left.
