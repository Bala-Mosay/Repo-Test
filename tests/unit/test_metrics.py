"""Metric aggregation: denominators, counts, and honest intervals."""

from __future__ import annotations

from repotest_bridge.evaluation.outcomes import Outcome
from repotest_bridge.metrics import (
    MIN_N_FOR_INTERVAL,
    StrategySummary,
    aggregate,
    outcome_matrix,
    wilson_interval,
)


def record(
    task_id: str,
    strategy: str,
    outcome: str | None,
    *,
    valid: bool = True,
    generated: bool = True,
    input_tokens: int = 100,
    context_tokens: int = 50,
    timings: dict[str, float] | None = None,
    error: str = "",
) -> dict:
    return {
        "task_id": task_id,
        "strategy": strategy,
        "has_diff": generated,
        "generation_ok": generated,
        "error": error,
        "validation": {"valid": valid},
        "outcome": outcome,
        "usage": {"input_tokens": input_tokens, "output_tokens": 40},
        "elapsed_ms": 12.5,
        "context": {"total_tokens": context_tokens},
        "evaluation": {"timings_ms": timings or {"buggy": 10.0, "fixed": 5.0}},
    }


def test_counts_and_denominators() -> None:
    records = [
        record("t1", "s", Outcome.F2P_SUCCESS.value),
        record("t2", "s", Outcome.FAILS_ON_FIXED.value),
        record("t3", "s", Outcome.INFRA_FAIL.value),
        record("t4", "s", Outcome.COLLECT_FAIL.value),
        record("t5", "s", Outcome.PATCH_APPLY_FAIL.value),
        record("t6", "s", Outcome.PATCH_PARSE_FAIL.value, valid=False),
    ]
    summary = aggregate("s", records)
    assert summary.n_total == 6
    # parse/apply failures never reach the harness, so they are not admitted.
    assert summary.N == 4
    assert summary.f2p_success == 1
    assert summary.infra_fail == 1
    assert summary.collect_fail == 1
    assert summary.parse_fail == 1
    assert summary.apply_fail == 1


def test_rates_are_over_n_admitted() -> None:
    summary = aggregate(
        "s",
        [
            record("t1", "s", Outcome.F2P_SUCCESS.value),
            record("t2", "s", Outcome.FAILS_ON_FIXED.value),
            record("t3", "s", Outcome.INFRA_FAIL.value),
        ],
    )
    k, n, pp = summary.f2p_rate
    assert (k, n) == (1, 3)
    assert pp is not None and abs(pp - 33.33) < 0.1


def test_rate_abstains_when_n_is_zero() -> None:
    summary = aggregate("s", [])
    assert summary.N == 0
    assert summary.f2p_rate == (0, 0, None)


def test_execution_counts_excludes_infra_failures() -> None:
    summary = aggregate(
        "s",
        [
            record("t1", "s", Outcome.F2P_SUCCESS.value),
            record("t2", "s", Outcome.COLLECT_FAIL.value),
            record("t3", "s", Outcome.INFRA_FAIL.value),
            record("t4", "s", Outcome.ERROR_ON_BOTH.value),
        ],
    )
    assert summary.N == 4
    assert summary.executed == 3  # infra_fail never executed
    k, n, _ = summary.test_execution
    assert (k, n) == (3, 4)


def test_missing_outcome_is_classified_from_the_record() -> None:
    summary = aggregate(
        "s",
        [
            record("t1", "s", None, generated=False),
            record("t2", "s", None, valid=False),
            record("t3", "s", None),
        ],
    )
    assert summary.parse_fail == 2
    assert summary.apply_fail == 1
    assert summary.N == 0


def test_usage_and_timing_are_summed() -> None:
    summary = aggregate(
        "s",
        [
            record("t1", "s", Outcome.F2P_SUCCESS.value),
            record("t2", "s", Outcome.F2P_SUCCESS.value),
        ],
    )
    assert summary.input_tokens == 200
    assert summary.output_tokens == 80
    assert summary.generation_ms == 25.0
    assert summary.execution_ms == 30.0
    assert summary.total_context_tokens == 100
    assert summary.context_rows == 2


def test_mean_context_tokens_is_per_task_not_per_item() -> None:
    summary = aggregate(
        "s", [record("t1", "s", Outcome.F2P_SUCCESS.value, context_tokens=300)]
    )
    row = summary.to_row()
    assert row["mean_context_tokens"] == 300.0


def test_generation_errors_are_counted_even_when_unsuccessful() -> None:
    summary = aggregate(
        "s",
        [
            record("t1", "s", None, generated=False, error="provider timeout"),
            record("t2", "s", Outcome.F2P_SUCCESS.value),
        ],
    )
    assert summary.generation_errors == 1
    assert summary.n_total == 2


def test_f2p_per_1k_tokens_never_divides_by_zero() -> None:
    base = aggregate("base", [record("t1", "base", Outcome.F2P_SUCCESS.value)])
    same = aggregate("arm", [record("t1", "arm", Outcome.F2P_SUCCESS.value)])
    assert same.f2p_per_1k_tokens_vs(base) is None  # no extra tokens spent
    richer = aggregate(
        "arm",
        [record("t1", "arm", Outcome.F2P_SUCCESS.value, input_tokens=500)],
    )
    value = richer.f2p_per_1k_tokens_vs(base)
    assert value is None or value > 0 or value <= 0  # finite number, not an error
    assert value is not None
    assert aggregate("arm", []).f2p_per_1k_tokens_vs(base) is None


def test_wilson_interval_abstains_below_minimum_n() -> None:
    assert wilson_interval(1, 5) is None
    assert wilson_interval(0, 0) is None
    assert wilson_interval(1, MIN_N_FOR_INTERVAL) is not None


def test_wilson_interval_brackets_the_observed_rate() -> None:
    low, high = wilson_interval(50, 100)  # type: ignore[misc]
    assert low < 50.0 < high
    assert low >= 0.0 and high <= 100.0


def test_wilson_interval_handles_extremes() -> None:
    low, high = wilson_interval(0, 40)  # type: ignore[misc]
    assert low == 0.0
    assert high < 10.0
    low, high = wilson_interval(40, 40)  # type: ignore[misc]
    assert high == 100.0
    assert low > 90.0


def test_outcome_matrix_covers_every_record() -> None:
    records = [
        record("t1", "a", Outcome.F2P_SUCCESS.value),
        record("t2", "a", Outcome.INFRA_FAIL.value),
        record("t3", "b", Outcome.F2P_SUCCESS.value),
        record("t4", "b", None, generated=False),
    ]
    matrix = outcome_matrix(records)
    assert matrix["a"] == {"f2p_success": 1, "infra_fail": 1}
    assert matrix["b"] == {"f2p_success": 1, "not_evaluated": 1}


def test_to_row_carries_raw_counts_alongside_percentages() -> None:
    summary = aggregate(
        "s",
        [
            record("t1", "s", Outcome.F2P_SUCCESS.value),
            record("t2", "s", Outcome.FAILS_ON_FIXED.value),
        ],
    )
    row = summary.to_row()
    assert row["f2p_success"] == 1
    assert row["N_admitted"] == 2
    assert row["f2p_rate_pct"] == 50.0
    assert row["strategy"] == "s"


def test_empty_summary_row_is_safe() -> None:
    row = StrategySummary(strategy="empty").to_row()
    assert row["N_admitted"] == 0
    assert row["f2p_rate_pct"] is None
    assert row["mean_context_tokens"] == 0


def test_validity_numerator_never_exceeds_n() -> None:
    """Regression: an apply-failure used to be counted twice.

    `validation.valid` was tallied for every record *and* again on the
    PATCH_APPLY_FAIL branch, while `n_admitted` was not -- producing the
    impossible report cell ``2/0 (n/a)``.
    """
    summary = aggregate(
        "s",
        [
            record("t1", "s", Outcome.F2P_SUCCESS.value),
            record("t2", "s", Outcome.PATCH_APPLY_FAIL.value, valid=True),
        ],
    )
    assert summary.N == 1
    k, n, pp = summary.patch_validity
    assert n == summary.N
    assert k <= n, "validity numerator must stay within N"
    assert pp is not None


def test_cost_counts_every_attempt_not_only_admitted() -> None:
    """Regression: rejected patches used to be billed as free.

    §5.2 measures tokens over all attempts and §5.3 normalises F2P against
    them, so an arm whose patches fail to apply must still show its spend.
    """
    summary = aggregate(
        "s",
        [
            record("t1", "s", Outcome.F2P_SUCCESS.value, input_tokens=100),
            record(
                "t2",
                "s",
                Outcome.PATCH_APPLY_FAIL.value,
                input_tokens=500,
                context_tokens=700,
                timings={"buggy": 40.0},
            ),
            record("t3", "s", Outcome.PATCH_PARSE_FAIL.value, valid=False),
        ],
    )
    # N only counts the admitted row, but every attempt is billed.
    assert summary.N == 1
    assert summary.input_tokens == 700
    assert summary.output_tokens == 120
    assert summary.generation_ms == 37.5
    assert summary.total_context_tokens == 800
    assert summary.context_rows == 3
    assert summary.execution_ms == 70.0


def test_mean_context_tokens_includes_rejected_patches() -> None:
    summary = aggregate(
        "s",
        [
            record("t1", "s", Outcome.F2P_SUCCESS.value, context_tokens=300),
            record("t2", "s", Outcome.PATCH_APPLY_FAIL.value, context_tokens=700),
        ],
    )
    assert summary.to_row()["mean_context_tokens"] == 500.0


def test_no_candidate_tests_is_admitted_but_never_executed() -> None:
    """The patch applied, so it counts; its tests never ran, so it doesn't."""
    summary = aggregate(
        "s",
        [
            record("t1", "s", Outcome.F2P_SUCCESS.value),
            record("t2", "s", Outcome.NO_CANDIDATE_TESTS.value),
        ],
    )
    assert summary.n_total == 2
    assert summary.N == 2
    assert summary.apply_pass == 2
    assert summary.no_candidate_tests == 1
    assert summary.executed == 1
    assert summary.f2p_success == 1  # t1 only; t2 is billed separately
    assert not summary.outcome_counts.get(Outcome.UNEXPECTED_PASS_ON_BUGGY.value)


def test_no_candidate_tests_is_not_infra() -> None:
    assert not Outcome.NO_CANDIDATE_TESTS.is_infra


def test_no_candidate_tests_appears_in_summary_row() -> None:
    row = aggregate(
        "s", [record("t1", "s", Outcome.NO_CANDIDATE_TESTS.value)]
    ).to_row()
    assert row["no_candidate_tests"] == 1
    assert row["N_admitted"] == 1
    assert row["executed"] == 0
