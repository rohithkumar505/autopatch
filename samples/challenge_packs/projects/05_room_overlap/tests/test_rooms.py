from rooms import can_book, overlaps

def test_overlap():
    assert overlaps(0, 10, 10, 20) is True

def test_can_book():
    assert can_book([], 1, 2)
