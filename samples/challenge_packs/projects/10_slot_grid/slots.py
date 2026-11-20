"""Clinic appointment grid."""

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
