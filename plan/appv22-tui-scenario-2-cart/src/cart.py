def subtotal(items):
    return sum(item["price"] * item.get("qty", 1) for item in items)


def apply_discount(total, percent):
    """Apply a discount to the total.
    
    Args:
        total: The original total amount.
        percent: Discount percentage, from 0 to 100.
    
    Returns:
        The total after applying the discount.
    """
    return total - total * percent / 100
