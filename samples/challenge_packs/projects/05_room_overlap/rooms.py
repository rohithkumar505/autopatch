"""Meeting room availability."""

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
