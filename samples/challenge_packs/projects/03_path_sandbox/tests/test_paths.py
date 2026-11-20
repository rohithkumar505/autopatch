from paths import is_valid_segment, safe_join

def test_simple_join():
    assert safe_join("/tmp/sandbox", "a/b.txt").endswith("a/b.txt")

def test_segment():
    assert is_valid_segment("file.txt")
