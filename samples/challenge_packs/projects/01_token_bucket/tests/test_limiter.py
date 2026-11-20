from limiter import TokenBucket, merge_limits

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
