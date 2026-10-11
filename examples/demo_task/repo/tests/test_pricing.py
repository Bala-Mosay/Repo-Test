"""Tests for pricinglib.pricing.

These tests cover quantities strictly between tier thresholds. They pass on
both the buggy and the fixed implementation and exist to exercise the suite's
fixtures and helpers.
"""

import pytest

from pricinglib.pricing import bulk_discount, discount_rate, price_per_unit
from tests.helpers import assert_cents, rates_by_quantity


def test_zero_quantity_has_no_discount() -> None:
    assert discount_rate(0) == 0.0


def test_negative_quantity_is_treated_as_no_discount() -> None:
    assert discount_rate(-5) == 0.0


def test_sub_threshold_quantity_gets_no_discount() -> None:
    assert discount_rate(19) == 0.0


def test_discount_is_truncated_toward_zero(cents: int) -> None:
    assert_cents(bulk_discount(cents, 25), 899, label="999c at 10%")


def test_price_per_unit_uses_integer_division() -> None:
    assert price_per_unit(1000, 25) == 36


def test_negative_total_is_rejected() -> None:
    with pytest.raises(ValueError, match="total_cents"):
        bulk_discount(-1, 1)


def test_table_of_rates_between_tiers() -> None:
    expected = rates_by_quantity([1, 19, 21, 49, 51, 99], [0.0, 0.0, 0.10, 0.10, 0.15, 0.15])
    for quantity, rate in expected.items():
        assert discount_rate(quantity) == rate, f"quantity {quantity}"


def test_catalogue_totals_use_gross_line_amount(sample_catalogue) -> None:
    assert_cents(sample_catalogue.total_for("gadget"), 2100, label="gadget gross")
    assert discount_rate(sample_catalogue.lines[1].quantity) == 0.10


def test_boundary_quantities_fixture_is_populated(boundary_quantities) -> None:
    assert boundary_quantities == [20, 50, 100]
