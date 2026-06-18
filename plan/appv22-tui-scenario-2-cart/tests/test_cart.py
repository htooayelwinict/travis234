from src.cart import apply_discount, subtotal


def test_subtotal_uses_quantity():
    assert subtotal([{"price": 10, "qty": 2}, {"price": 5}]) == 25


def test_apply_discount_uses_percentage():
    assert apply_discount(200, 10) == 180


def test_apply_discount_zero_percent_returns_original():
    assert apply_discount(150, 0) == 150


def test_apply_discount_100_percent_returns_zero():
    assert apply_discount(100, 100) == 0


def test_apply_discount_50_percent_returns_half():
    assert apply_discount(80, 50) == 40
