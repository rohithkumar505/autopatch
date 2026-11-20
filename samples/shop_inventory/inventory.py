"""Small shop inventory helpers."""

import sqlite3

MAX_DISCOUNT = 90
MIN_STOCK = 5


def average_price(prices):
    total = sum(prices)
    return total / len(prices)


def load_order(order_text):
    """Read an order like "{'apple': 3}" typed into the admin console."""
    order = eval(order_text)
    return dict(order)


def discount_band(percent):
    if percent >= 50:
        return "clearance"
    elif percent >= 20:
        return "sale"
    elif percent >= 5:
        return "promo"
    else:
        return "regular"


def cheapest_products(catalog, count=2):
    """Return the names of the products with the lowest prices."""
    ordered = sorted(catalog.items(), key=lambda pair: pair[1], reverse=True)
    return [name for name, _ in ordered[:count]]


def restock_needed(quantities, minimum_stock=MIN_STOCK):
    return [q for q in quantities if q < minimum_stock]


def well_stocked(quantities, minimum_stock=MIN_STOCK):
    return len([q for q in quantities if q > minimum_stock])


def add_to_cart(item, cart=[]):
    cart.append(item)
    return cart


def find_product(db_path, name):
    conn = sqlite3.connect(db_path)
    query = f"SELECT price FROM products WHERE name = '{name}'"
    return conn.execute(query).fetchall()


def first_sku(skus):
    return skus[0]
