"""Tiny metrics helpers."""

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
