"""Regenerate the demo task's gold patches.

The demo repository under ``examples/demo_task/repo`` is the **buggy** state.
This script rebuilds ``examples/demo_task/gold/*.diff`` from scratch in a
throw-away git checkout so the patches are known-good and reproducible.

Run from anywhere:

    python scripts/make_demo_gold.py

Nothing here is used at evaluation time; the gold patches are read by the
harness only, never by the generator.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "examples" / "demo_task" / "repo"
GOLD = ROOT / "examples" / "demo_task" / "gold"

CODE_REL = Path("pricinglib") / "pricing.py"
GOLD_TEST_REL = Path("tests") / "test_bulk_discount_boundaries.py"

GIT_ID = ["-c", "user.name=RepoTest-Bridge", "-c", "user.email=repotest@example.invalid"]

BUGGY_SNIPPET = """    rate = 0.0
    for threshold, candidate in TIER_BREAKS:
        if quantity > threshold:
            rate = candidate
    return rate
"""

FIXED_SNIPPET = """    rate = 0.0
    for threshold, candidate in TIER_BREAKS:
        if quantity >= threshold:
            rate = candidate
    return rate
"""

GOLD_TEST = '''"""Regression tests for volume discount tier boundaries.

Issue: discount tiers were never applied to orders whose quantity fell
*exactly* on a tier threshold (20, 50 or 100 units), because the tier
comparison used a strict greater-than instead of greater-or-equal.
"""

import pytest

from pricinglib.pricing import bulk_discount, discount_rate
from tests.helpers import assert_cents, rates_by_quantity


def test_exact_lower_boundary_gets_ten_percent() -> None:
    assert discount_rate(20) == 0.10


def test_exact_middle_boundary_gets_fifteen_percent() -> None:
    assert discount_rate(50) == 0.15


def test_exact_upper_boundary_gets_twenty_five_percent() -> None:
    assert discount_rate(100) == 0.25


def test_all_boundaries_are_discounted(boundary_quantities) -> None:
    expected = rates_by_quantity(boundary_quantities, [0.10, 0.15, 0.25])
    for quantity, rate in expected.items():
        assert discount_rate(quantity) == rate, f"quantity {quantity}"


def test_bulk_discount_at_exact_boundary(cents) -> None:
    assert_cents(bulk_discount(1000, 20), 900, label="1000c at 10%")
    assert_cents(bulk_discount(1000, 50), 850, label="1000c at 15%")
    assert_cents(bulk_discount(1000, 100), 750, label="1000c at 25%")


def test_quantity_just_below_boundary_is_not_discounted() -> None:
    assert discount_rate(19) == 0.0
    assert discount_rate(49) == 0.10
    assert discount_rate(99) == 0.15


def test_negative_and_zero_quantities_unchanged() -> None:
    assert discount_rate(0) == 0.0
    assert discount_rate(-1) == 0.0
    assert_cents(bulk_discount(500, 0), 500, label="no discount at qty 0")


def test_validation_errors_still_raised() -> None:
    with pytest.raises(ValueError, match="total_cents"):
        bulk_discount(-1, 20)
    with pytest.raises(ValueError, match="quantity"):
        bulk_discount(100, -1)
'''


def run(args: list[str], cwd: Path, *, ok_codes: tuple[int, ...] = (0,)) -> str:
    """Run a git command, allowing non-zero exits listed in ``ok_codes``.

    ``git diff`` exits 1 when the working tree differs from the index, which
    is the expected outcome here rather than an error.
    """
    proc = subprocess.run(
        ["git", *GIT_ID, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode not in ok_codes:
        raise RuntimeError(f"git {args} failed in {cwd}:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def main() -> int:
    if not REPO.is_dir():
        print(f"missing demo repo at {REPO}", file=sys.stderr)
        return 1

    source = (REPO / CODE_REL).read_text(encoding="utf-8")
    if BUGGY_SNIPPET not in source:
        print("buggy snippet not found; demo repo changed?", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="repotest-gold-"))
    try:
        work = tmp / "repo"
        shutil.copytree(REPO, work)

        run(["init", "-q"], work)
        run(["add", "-A"], work)
        run(["commit", "-q", "-m", "buggy base"], work)

        # --- gold code fix -------------------------------------------------
        (work / CODE_REL).write_text(
            source.replace(BUGGY_SNIPPET, FIXED_SNIPPET), encoding="utf-8"
        )
        code_diff = run(["diff", "--", str(CODE_REL)], work, ok_codes=(0, 1))
        if not code_diff.strip():
            print("code fix produced an empty diff", file=sys.stderr)
            return 1

        # --- gold test fix -------------------------------------------------
        (work / GOLD_TEST_REL).write_text(GOLD_TEST, encoding="utf-8")
        run(["add", "-N", "--", str(GOLD_TEST_REL)], work)
        test_diff = run(["diff", "--", "tests/"], work, ok_codes=(0, 1))
        if not test_diff.strip():
            print("test fix produced an empty diff", file=sys.stderr)
            return 1

        GOLD.mkdir(parents=True, exist_ok=True)
        (GOLD / "code_fix.diff").write_text(code_diff, encoding="utf-8", newline="\n")
        (GOLD / "gold_test.diff").write_text(test_diff, encoding="utf-8", newline="\n")
        print(f"wrote {GOLD / 'code_fix.diff'}")
        print(f"wrote {GOLD / 'gold_test.diff'}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
