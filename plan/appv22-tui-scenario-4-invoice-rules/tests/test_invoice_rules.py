from src.invoice_rules import subtotal, apply_tax, apply_discount, invoice_total, payment_status, round_money


def test_subtotal_uses_quantity():
    assert subtotal([{"price": 10, "qty": 2}, {"price": 5}]) == 25


def test_apply_tax_adds_percentage():
    assert apply_tax(100, 8) == 108


def test_apply_discount_subtracts_percentage():
    assert apply_discount(200, 25) == 150


def test_invoice_total_calculation():
    items = [{"price": 100, "qty": 1}, {"price": 50, "qty": 2}]
    assert invoice_total(items, discount_percent=10, tax_percent=5) == 189


def test_payment_status_paid_for_zero_or_less():
    assert payment_status(0) == "paid"
    assert payment_status(-5) == "paid"


def test_payment_status_due_for_positive_balance():
    assert payment_status(12.50) == "due"


def test_round_money():
    assert round_money(10.236) == 10.24


def test_invoice_total_rounds_to_two_decimals():
    items = [{"price": 10.236, "qty": 1}]
    assert invoice_total(items, discount_percent=0, tax_percent=0) == 10.24
