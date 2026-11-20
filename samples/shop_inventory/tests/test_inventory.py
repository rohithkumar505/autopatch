from inventory import (
    add_to_cart, average_price, cheapest_products, discount_band, first_sku, load_order, restock_needed, well_stocked,
)


def test_average_price():
    assert average_price([10, 20, 30]) == 20


def test_load_order():
    assert load_order("{'apple': 3}") == {"apple": 3}


def test_discount_band():
    assert discount_band(60) == "clearance"
    assert discount_band(25) == "sale"
    assert discount_band(1) == "regular"


def test_restock_needed():
    assert restock_needed([1, 9, 3]) == [1, 3]


def test_well_stocked():
    assert well_stocked([1, 9, 30]) == 2


def test_cart_first_item():
    assert add_to_cart("pen") == ["pen"]


def test_first_sku():
    assert first_sku(["A1", "B2"]) == "A1"


def test_cheapest_with_one_product():
    assert cheapest_products({"tea": 4}) == ["tea"]
