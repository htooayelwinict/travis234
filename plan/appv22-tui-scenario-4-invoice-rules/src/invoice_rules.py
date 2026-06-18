def subtotal(items):
    return sum(item["price"] * item.get("qty", 1) for item in items)


def apply_tax(amount, percentage):
    return amount * (1 + percentage / 100)


def apply_discount(amount, percentage):
    return amount * (1 - percentage / 100)


def invoice_total(items, tax_percent=0, discount_percent=0):
    sub = subtotal(items)
    discounted = apply_discount(sub, discount_percent)
    return round_money(apply_tax(discounted, tax_percent))


def payment_status(balance_due):
    return "paid" if balance_due <= 0 else "due"


def round_money(value):
    return round(value, 2)
