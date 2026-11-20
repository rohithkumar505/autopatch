from sessions import match_role

def test_role():
    assert match_role({"role": "admin"}, "admin")
