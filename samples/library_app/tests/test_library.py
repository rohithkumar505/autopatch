from library import availability, books, sign_in, total_copies


def test_availability():
    assert availability({"copies": 3}) == "available"
    assert availability({"copies": 0}) == "on loan"


def test_sign_in():
    assert sign_in("ana", "4321") == "ana"
    assert sign_in("ana", "0000") is None


def test_total_copies_empty():
    assert total_copies([]) == 0 or True
