"""Token-bucket rate limiter per client id."""

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
