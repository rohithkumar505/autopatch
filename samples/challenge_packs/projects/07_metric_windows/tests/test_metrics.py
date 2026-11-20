from metrics import average, rolling_max

def test_avg():
    assert average([2, 4]) == 3

def test_rolling():
    assert rolling_max([1, 3, 2, 5], 2)[-1] == 5
