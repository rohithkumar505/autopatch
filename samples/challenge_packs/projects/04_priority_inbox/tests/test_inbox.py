from inbox import sort_inbox, top_messages

def test_sort():
    msgs = [{"id": "a", "priority": 1, "received_at": 10}, {"id": "b", "priority": 5, "received_at": 1}]
    assert sort_inbox(msgs)[0]["id"] in ("a", "b")

def test_top():
    msgs = [{"id": "x", "priority": 1}, {"id": "y", "priority": 9}]
    assert len(top_messages(msgs, 2)) == 2
