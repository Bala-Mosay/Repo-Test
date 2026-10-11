"""Volume discount pricing rules for a wholesale catalogue.

Discount tiers are applied to the line total based on the quantity ordered.
"""

TIER_BREAKS = ((20, 0.10), (50, 0.15), (100, 0.25))


def discount_rate(quantity: int) -> float:
    """Return the discount rate for an order quantity.

    Tiers (cumulative, highest matching tier wins):

    ==========  ======
    quantity    rate
    ==========  ======
    >= 100      0.25
    >= 50       0.15
    >= 20       0.10
    < 20        0.00
    ==========  ======
    """
    if quantity <= 0:
        return 0.0
    rate = 0.0
    for threshold, candidate in TIER_BREAKS:
        if quantity > threshold:
            rate = candidate
    return rate


def bulk_discount(total_cents: int, quantity: int) -> int:
    """Return the line total in cents after applying the tier discount.

    The result is truncated toward zero, so a 10% discount on 999 cents
    yields 899 cents rather than 899.1 cents.
    """
    if total_cents < 0:
        raise ValueError("total_cents must be non-negative")
    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    rate = discount_rate(quantity)
    return int(total_cents * (1.0 - rate))


def price_per_unit(total_cents: int, quantity: int) -> int:
    """Return the effective per-unit price in cents after discount."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    return bulk_discount(total_cents, quantity) // quantity
