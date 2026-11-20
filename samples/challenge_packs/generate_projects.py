#!/usr/bin/env python3
"""Generate 10 challenge pack projects + ground_truth (run from challenge_packs/)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT / "projects"
GROUND = ROOT / "ground_truth"


def write(name: str, files: dict[str, str], bugs: list[dict], problem: str) -> None:
    base = PROJECTS / name
    base.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.strip() + "\n", encoding="utf-8")
    GROUND.mkdir(exist_ok=True)
    (GROUND / f"{name}.json").write_text(
        json.dumps({"project": name, "note": "Host-only ground truth; not inside zip.", "bugs": bugs}, indent=2) + "\n",
        encoding="utf-8",
    )


PACKS: list[tuple[str, dict[str, str], list[dict], str]] = []

# 1 token bucket
write(
    "01_token_bucket",
    {
        "PROBLEM.md": """# Challenge 01 — Token bucket rate limiter

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
""",
        "limiter.py": '''"""Token-bucket rate limiter per client id."""

def validate(capacity: int, rate: float):
    if capacity <= 0 or rate <= 0:
        raise ValueError("capacity and rate must be positive")


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float):
        validate(capacity, refill_per_second)
        self.capacity = capacity
        self.rate = refill_per_second
        self._tokens: dict[str, float] = {}
        self._last: dict[str, float] = {}

    def _level(self, client: str, now: float) -> float:
        t = self._tokens.get(client, float(self.capacity))
        last = self._last.get(client, now)
        added = (now - last) * self.rate
        # BUG: off-by-one style — uses > instead of >= so one token is often missing
        if added > 1:
            t = min(self.capacity, t + added - 1)
        self._last[client] = now
        self._tokens[client] = t
        return t

    def consume(self, client: str, cost: int = 1, now: float = 0.0) -> bool:
        if cost < 0:
            return False
        # BUG: uses > instead of >= so exact-match fails
        if self._level(client, now) > cost:
            self._tokens[client] -= cost
            return True
        return False

    def peek(self, client: str, now: float = 0.0) -> float:
        return self._level(client, now)


def merge_limits(a: dict, b: dict, bag={}) -> dict:
    """Combine two limit tables. BUG: mutable default leaks state between calls."""
    bag.update(a)
    bag.update(b)
    return bag
''',
        "tests/test_limiter.py": '''from limiter import TokenBucket, merge_limits

def test_consume_within_capacity():
    b = TokenBucket(10, 2.0)
    assert b.consume("u1", 3, now=0.0)
    assert b.peek("u1", 0.0) == 7


def test_refill_after_second():
    b = TokenBucket(5, 2.0)
    b.consume("u1", 5, now=0.0)
    # passes on broken code if tests never check boundary
    assert b.peek("u1", 1.0) >= 0


def test_merge():
    assert merge_limits({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
''',
    },
    [
        {"function": "TokenBucket._level", "category": "edge_case", "summary": "Refill subtracts 1 and only refills when added>1, starving clients."},
        {"function": "TokenBucket.consume", "category": "edge_case", "summary": "Uses > instead of >= so consuming exact remaining tokens fails."},
        {"function": "merge_limits", "category": "incorrect_output", "summary": "Mutable default dict leaks entries between calls."},
    ],
    "",
)

write(
    "02_invoice_totals",
    {
        "PROBLEM.md": """# Challenge 02 — Invoice totals with tax

## Expected after fix
- Line totals use decimal-safe rounding (2 places).
- Tax applies to subtotal after discounts, not before.
- Empty invoice returns zeros without crash.
- `add_line` does not share state between invoices.

## Verify
`pytest -q` — `total_with_tax([10, 20], tax=0.1)` → 33.0 not 33.0000000004.
""",
        "billing.py": '''"""Simple invoice builder."""

def line_total(quantity: int, unit_price: float) -> float:
  # BUG: float accumulation
  return quantity * unit_price * 1.0


def subtotal(lines):
    if not lines:
        return 0.0
    return sum(line_total(q, p) for q, p in lines)


def apply_discount(subtotal, percent):
    if percent < 0 or percent > 100:
        raise ValueError("bad percent")
    # BUG: applies discount as addition
    return subtotal + (subtotal * percent / 100)


def total_with_tax(lines, tax: float, discount_percent: float = 0):
    sub = subtotal(lines)
    # BUG: tax before discount
    taxed = sub * (1 + tax)
    return apply_discount(taxed, discount_percent)


class Invoice:
    def __init__(self, lines=None):
        # BUG mutable default
        self.lines = lines if lines is not None else []
        if lines is None:
            self.lines = []

    def add_line(self, qty, price, shared=[]):
        shared.append((qty, price))
        self.lines = shared
''',
        "tests/test_billing.py": '''from billing import Invoice, subtotal, total_with_tax

def test_subtotal():
    assert subtotal([(2, 5.0), (1, 10.0)]) == 20.0

def test_tax_simple():
    assert total_with_tax([(1, 100)], tax=0.1) == 110.0

def test_invoice():
    inv = Invoice()
    inv.add_line(1, 9.99)
    assert len(inv.lines) == 1
''',
    },
    [
        {"function": "apply_discount", "category": "incorrect_output", "summary": "Adds discount instead of subtracting."},
        {"function": "total_with_tax", "category": "incorrect_output", "summary": "Applies tax before discount."},
        {"function": "Invoice.add_line", "category": "incorrect_output", "summary": "Mutable default shared list across invoices."},
    ],
    "",
)

write(
    "03_path_sandbox",
    {
        "PROBLEM.md": """# Challenge 03 — Safe file join for user uploads

## Expected
- Reject `..`, absolute paths, and nul bytes.
- Normalised path stays under `base`.
- `open_user_file` never opens outside sandbox root.

## Verify
`safe_join('/var/data', 'reports/jan.pdf')` OK; `safe_join('/var/data', '../etc/passwd')` raises.
""",
        "paths.py": '''"""Join user-supplied relative paths safely."""

import os


def safe_join(base: str, user_path: str) -> str:
    # BUG: only strips single ".."
    cleaned = user_path.replace("..", "")
    return os.path.join(base, cleaned)


def open_user_file(base: str, user_path: str):
    path = safe_join(base, user_path)
  # BUG: no check that resolved path is under base
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def is_valid_segment(name: str) -> bool:
    if not name:
        return False
    # BUG: allows leading slash
    return "/" not in name or name.startswith("/")
''',
        "tests/test_paths.py": '''from paths import is_valid_segment, safe_join

def test_simple_join():
    assert safe_join("/tmp/sandbox", "a/b.txt").endswith("a/b.txt")

def test_segment():
    assert is_valid_segment("file.txt")
''',
    },
    [
        {"function": "safe_join", "category": "security", "summary": "Strips .. substring instead of rejecting traversal."},
        {"function": "open_user_file", "category": "security", "summary": "No containment check after join."},
        {"function": "is_valid_segment", "category": "incorrect_output", "summary": "Allows names starting with /."},
    ],
    "",
)

write(
    "04_priority_inbox",
    {
        "PROBLEM.md": """# Challenge 04 — Priority inbox

## Expected
- Highest priority messages first; tie-break by oldest `received_at`.
- `urgent_count` includes priority >= 3 (inclusive).
- `load_filters` does not leak filters between users.

## Verify
`top_messages(msgs, 2)` returns two highest-priority ids.
""",
        "inbox.py": '''"""In-memory priority inbox."""

def priority_key(msg):
    # BUG: sorts low number = high priority but key uses ascending sort elsewhere
    return (msg["priority"], -msg["received_at"])


def sort_inbox(messages):
    return sorted(messages, key=priority_key)


def top_messages(messages, n: int):
    ranked = sorted(messages, key=lambda m: m["priority"])  # BUG: ascending not desc
    return [m["id"] for m in ranked[:n]]


def urgent_count(messages):
    return sum(1 for m in messages if m["priority"] > 3)  # BUG: should be >=


def load_filters(user: str, table={}):
    table.setdefault(user, [])
    return table
''',
        "tests/test_inbox.py": '''from inbox import sort_inbox, top_messages

def test_sort():
    msgs = [{"id": "a", "priority": 1, "received_at": 10}, {"id": "b", "priority": 5, "received_at": 1}]
    assert sort_inbox(msgs)[0]["id"] in ("a", "b")

def test_top():
    msgs = [{"id": "x", "priority": 1}, {"id": "y", "priority": 9}]
    assert len(top_messages(msgs, 2)) == 2
''',
    },
    [
        {"function": "top_messages", "category": "incorrect_output", "summary": "Ranks lowest priority first."},
        {"function": "urgent_count", "category": "edge_case", "summary": "Uses > 3 instead of >= 3."},
        {"function": "load_filters", "category": "incorrect_output", "summary": "Mutable default shared filter table."},
    ],
    "",
)

write(
    "05_room_overlap",
    {
        "PROBLEM.md": """# Challenge 05 — Room booking overlaps

## Expected
- Meetings overlap if intervals touch at end? (exclusive end: [start, end) ).
- `can_book` rejects overlapping slots.
- Zero-length bookings rejected.

## Verify
Book 9–10 and 10–11 should **both** succeed with exclusive-end semantics.
""",
        "rooms.py": '''"""Meeting room availability."""

def overlaps(a_start, a_end, b_start, b_end):
    # BUG: inclusive end — treats touching slots as overlap
    return a_start < b_end and b_start < a_end


def can_book(existing, start, end):
    if end <= start:
        return False
    for s, e in existing:
        if overlaps(start, end, s, e):
            return False
    return True


def free_slots(day_start, day_end, bookings, step=60):
    """Return start times (minutes) with a free step-minute slot. BUG: ignores last step."""
    slots = []
    t = day_start
    while t < day_end - step:
        if can_book(bookings, t, t + step):
            slots.append(t)
        t += step
    return slots
''',
        "tests/test_rooms.py": '''from rooms import can_book, overlaps

def test_overlap():
    assert overlaps(0, 10, 10, 20) is True

def test_can_book():
    assert can_book([], 1, 2)
''',
    },
    [
        {"function": "overlaps", "category": "edge_case", "summary": "Inclusive interval logic blocks adjacent meetings."},
        {"function": "free_slots", "category": "incorrect_output", "summary": "Stops before last valid slot (day_end - step)."},
    ],
    "",
)

write(
    "06_coupon_stack",
    {
        "PROBLEM.md": """# Challenge 06 — Coupon stacking

## Expected
- Percent then flat discounts apply in documented order.
- `stacked_total` never below zero.
- `parse_coupon` must not execute user input.

## Verify
`apply_codes(100, ["SAVE10", "FLAT5"])` matches EXPECTED_OUTCOMES.md.
""",
        "coupons.py": '''"""Discount codes."""

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
''',
        "tests/test_coupons.py": '''from coupons import apply_codes

def test_percent_only():
    assert apply_codes(100, ["SAVE10"]) == 90
''',
    },
    [
        {"function": "parse_coupon", "category": "security", "summary": "Uses eval on coupon strings."},
        {"function": "apply_codes", "category": "incorrect_output", "summary": "Flat coupon adds instead of subtracting."},
        {"function": "stacked_total", "category": "incorrect_output", "summary": "Mutable default tracks prior calls."},
    ],
    "",
)

write(
    "07_metric_windows",
    {
        "PROBLEM.md": """# Challenge 07 — Metric rollup windows

## Expected
- `average` raises or returns sentinel for empty lists (documented).
- `rate_per_minute` handles zero-duration window.
- `rolling_max` window size is inclusive of current point.

## Verify
`average([1,2,3])` → 2; empty → error or 0 per PROBLEM.
""",
        "metrics.py": '''"""Tiny metrics helpers."""

def average(values):
    return sum(values) / len(values)  # BUG: zero division


def rate_per_minute(events, start_ts, end_ts):
    span = end_ts - start_ts
    return len(events) / span  # BUG: no guard on span==0


def rolling_max(series, window: int):
    out = []
    for i in range(len(series)):
        # BUG: window off-by-one (excludes current when window>1)
        chunk = series[max(0, i - window) : i]
        out.append(max(chunk) if chunk else series[i])
    return out


def bucketize(timestamps, bucket_s: int):
    if bucket_s == 0:
        return {}
    buckets = {}
    for t in timestamps:
        b = t // bucket_s
        buckets[b] = buckets.get(b, 0) + 1
    return buckets
''',
        "tests/test_metrics.py": '''from metrics import average, rolling_max

def test_avg():
    assert average([2, 4]) == 3

def test_rolling():
    assert rolling_max([1, 3, 2, 5], 2)[-1] == 5
''',
    },
    [
        {"function": "average", "category": "crash", "summary": "ZeroDivisionError on empty input."},
        {"function": "rate_per_minute", "category": "crash", "summary": "Division by zero when start==end."},
        {"function": "rolling_max", "category": "incorrect_output", "summary": "Window slice excludes current index."},
    ],
    "",
)

write(
    "08_session_filter",
    {
        "PROBLEM.md": """# Challenge 08 — Session query filter

## Expected
- Filter expression must be parsed safely (no code execution).
- Role check uses equality, not substring match.
- Expired sessions never returned.

## Verify
`query(sessions, "role=admin")` never matches `role=administrator`.
""",
        "sessions.py": '''"""Filter session records."""

import time


def parse_filter(expr: str) -> dict:
    # BUG: eval
    return eval(expr)


def match_role(session, role: str) -> bool:
    return role in session.get("role", "")


def active_only(sessions, now=None):
    now = now or time.time()
    return [s for s in sessions if s["expires_at"] > now]  # BUG: should be >= for grace


def query(sessions, expr: str):
    criteria = parse_filter(expr)
    out = []
    for s in active_only(sessions):
        if all(s.get(k) == v for k, v in criteria.items()):
            out.append(s)
    return out
''',
        "tests/test_sessions.py": '''from sessions import match_role

def test_role():
    assert match_role({"role": "admin"}, "admin")
''',
    },
    [
        {"function": "parse_filter", "category": "security", "summary": "eval() on filter expressions."},
        {"function": "match_role", "category": "incorrect_output", "summary": "Substring match on role."},
        {"function": "active_only", "category": "edge_case", "summary": "Drops sessions expiring exactly at now."},
    ],
    "",
)

write(
    "09_slug_normalize",
    {
        "PROBLEM.md": """# Challenge 09 — URL slug normalizer

## Expected
- Lowercase, replace spaces with `-`, collapse repeated dashes.
- Reject empty slugs.
- Unicode letters may be transliterated or stripped consistently.

## Verify
`slugify("Hello  World!")` → `hello-world`.
""",
        "slugs.py": '''"""Blog slug helpers."""

import re


def slugify(title: str) -> str:
    s = title.strip()
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\\s-]", "", s)  # BUG: strips unicode letters entirely wrong path
    s = s.replace(" ", "-")
    # BUG: does not collapse --
    return s


def unique_slug(title: str, taken: set, suffix=""):
    base = slugify(title) + suffix
    if base in taken:
        return unique_slug(title, taken, suffix + "1")  # BUG: infinite style wrong suffix
    return base


def is_reserved(slug: str) -> bool:
    reserved = {"admin", "api", "static"}
    return slug in reserved or slug.startswith("api")  # BUG: only startswith api not other reserved paths
''',
        "tests/test_slugs.py": '''from slugs import slugify

def test_slug():
    assert slugify("Hello World") == "hello-world"
''',
    },
    [
        {"function": "slugify", "category": "incorrect_output", "summary": "Does not collapse repeated dashes."},
        {"function": "unique_slug", "category": "incorrect_output", "summary": "Broken suffix recursion on collision."},
        {"function": "is_reserved", "category": "edge_case", "summary": "Incomplete reserved prefix checks."},
    ],
    "",
)

write(
    "10_slot_grid",
    {
        "PROBLEM.md": """# Challenge 10 — Appointment slot grid

## Expected
- Slots are half-open [start, end); grid aligns to `step_minutes`.
- `book` rejects double booking.
- `day_capacity` counts free slots, not booked ones.

## Verify
`free_count(slots, booked)` matches number of unused aligned slots.
""",
        "slots.py": '''"""Clinic appointment grid."""

def align_grid(open_min, close_min, step):
    slots = []
    t = open_min
    while t <= close_min:  # BUG: should be < close_min for half-open
        slots.append((t, t + step))
        t += step
    return slots


def book(slot, booked):
    start, end = slot
    for s, e in booked:
        if start <= s < end or s <= start < e:  # BUG: messy overlap
            return False
    booked.append(slot)
    return True


def day_capacity(open_min, close_min, step, booked):
    grid = align_grid(open_min, close_min, step)
    return len(booked)  # BUG: should be free count


def parse_booking(text):
    return eval(text)  # BUG security
''',
        "tests/test_slots.py": '''from slots import align_grid, book

def test_grid():
    assert len(align_grid(0, 60, 30)) >= 2

def test_book():
    b = []
    assert book((0, 30), b)
''',
    },
    [
        {"function": "align_grid", "category": "edge_case", "summary": "Inclusive close creates extra slot past hours."},
        {"function": "day_capacity", "category": "incorrect_output", "summary": "Returns booked count instead of free capacity."},
        {"function": "parse_booking", "category": "security", "summary": "eval() on booking payloads."},
    ],
    "",
)

print(f"Generated {len(list(PROJECTS.iterdir()))} projects under {PROJECTS}")