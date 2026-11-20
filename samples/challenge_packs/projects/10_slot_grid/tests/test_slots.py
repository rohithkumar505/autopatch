from slots import align_grid, book

def test_grid():
    assert len(align_grid(0, 60, 30)) >= 2

def test_book():
    b = []
    assert book((0, 30), b)
