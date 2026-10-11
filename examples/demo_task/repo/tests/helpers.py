"""Shared helpers for the pricinglib test suite."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    name: str
    unit_cents: int
    quantity: int

    @property
    def total_cents(self) -> int:
        return self.unit_cents * self.quantity


class Catalogue:
    """A collection of order lines used by several test modules."""

    def __init__(self, lines: list[Line] | list[tuple[str, int, int]]) -> None:
        parsed: list[Line] = []
        for entry in lines:
            if isinstance(entry, Line):
                parsed.append(entry)
            else:
                name, unit_cents, quantity = entry
                parsed.append(Line(name=name, unit_cents=unit_cents, quantity=quantity))
        self.lines = parsed

    def total_for(self, name: str) -> int:
        for line in self.lines:
            if line.name == name:
                return line.total_cents
        raise KeyError(name)


def assert_cents(actual: int, expected: int, *, label: str = "") -> None:
    """Assert an exact cent amount with a readable failure message."""
    assert actual == expected, f"{label}: expected {expected} cents, got {actual} cents"


def rates_by_quantity(quantities: list[int], rates: list[float]) -> dict[int, float]:
    """Zip quantities with rates for table-driven assertions."""
    if len(quantities) != len(rates):
        raise ValueError("quantities and rates must be the same length")
    return dict(zip(quantities, rates, strict=True))
