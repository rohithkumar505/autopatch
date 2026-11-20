"""Filter session records."""

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
