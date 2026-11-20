from slugs import slugify

def test_slug():
    assert slugify("Hello World") == "hello-world"
