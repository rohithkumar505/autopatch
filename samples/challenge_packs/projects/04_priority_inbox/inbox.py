"""In-memory priority inbox."""

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
