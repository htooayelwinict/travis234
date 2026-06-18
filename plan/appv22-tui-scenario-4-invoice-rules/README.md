Invoice rules scenario.

Goal: expand `src/invoice_rules.py` with tax, discount, and status helpers while preserving existing subtotal behavior.

The module provides `invoice_total` to calculate the final invoice amount with discounts and taxes, `payment_status` to determine if an invoice is paid or due, and `round_money` to round values to two decimal places.
