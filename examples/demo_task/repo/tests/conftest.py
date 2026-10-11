"""Shared fixtures for the pricinglib test suite."""

import pytest

from pricinglib.pricing import TIER_BREAKS
from tests.helpers import Catalogue


@pytest.fixture
def boundary_quantities() -> list[int]:
    """Every quantity exactly at a tier threshold."""
    return [threshold for threshold, _ in TIER_BREAKS]


@pytest.fixture
def sample_catalogue() -> Catalogue:
    """A small catalogue whose line totals straddle the tier thresholds."""
    return Catalogue(
        lines=[
            ("widget", 100, 7),
            ("gadget", 100, 21),
            ("sprocket", 250, 50),
            ("flange", 1000, 100),
        ]
    )


@pytest.fixture
def cents() -> int:
    """A line total that is awkward for percentages: 999 cents."""
    return 999
