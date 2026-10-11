"""Aggregation of per-run records into protocol metrics (docs/experiment_protocol.md §5).

Every rate is reported as ``k/N (pp%)``: the raw counts travel with the
percentage so a reader can always recompute the denominator.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from repotest_bridge.evaluation.outcomes import Outcome

MIN_N_FOR_INTERVAL = 30


@dataclass
class StrategySummary:
    """Aggregate metrics for one strategy over one manifest."""

    strategy: str
    n_total: int = 0            # tasks attempted
    n_admitted: int = 0         # tasks with an evaluable patch and no parse failure
    outcome_counts: dict[str, int] = field(default_factory=dict)

    parse_fail: int = 0
    apply_fail: int = 0
    collect_fail: int = 0
    no_candidate_tests: int = 0
    infra_fail: int = 0
    f2p_success: int = 0
    other_failure: int = 0

    validity_pass: int = 0
    apply_pass: int = 0
    executed: int = 0

    input_tokens: int = 0
    output_tokens: int = 0
    generation_ms: float = 0.0
    execution_ms: float = 0.0
    total_context_tokens: int = 0
    context_rows: int = 0
    generation_errors: int = 0

    # -- rates (all over N = n_admitted, protocol §5.1) ---------------------

    @property
    def N(self) -> int:
        return self.n_admitted

    def _rate(self, numerator: int) -> tuple[int, int, float | None]:
        if self.N == 0:
            return numerator, self.N, None
        return numerator, self.N, 100.0 * numerator / self.N

    @property
    def patch_validity(self) -> tuple[int, int, float | None]:
        return self._rate(self.validity_pass)

    @property
    def patch_application(self) -> tuple[int, int, float | None]:
        return self._rate(self.apply_pass)

    @property
    def test_execution(self) -> tuple[int, int, float | None]:
        return self._rate(self.executed)

    @property
    def f2p_rate(self) -> tuple[int, int, float | None]:
        return self._rate(self.f2p_success)

    @property
    def infra_rate(self) -> tuple[int, int, float | None]:
        return self._rate(self.infra_fail)

    # -- derived (H3/H4, protocol §5.3) -------------------------------------

    def f2p_per_1k_tokens_vs(self, baseline: StrategySummary) -> float | None:
        """(F2P_arm - F2P_base) / (tokens_arm - tokens_base) * 1000, in pp."""
        if self.N == 0 or baseline.N == 0:
            return None
        arm_pp = 100.0 * self.f2p_success / self.N
        base_pp = 100.0 * baseline.f2p_success / baseline.N
        d_tokens = self.input_tokens - baseline.input_tokens
        if d_tokens <= 0:
            return None
        return (arm_pp - base_pp) / d_tokens * 1000.0

    def to_row(self) -> dict[str, Any]:
        f2p_k, _f2p_n, f2p_pp = self.f2p_rate
        val_k, _val_n, val_pp = self.patch_validity
        app_k, _app_n, app_pp = self.patch_application
        exe_k, _exe_n, exe_pp = self.test_execution
        return {
            "strategy": self.strategy,
            "tasks_attempted": self.n_total,
            "N_admitted": self.N,
            "f2p_success": f2p_k,
            "f2p_rate_pct": None if f2p_pp is None else round(f2p_pp, 2),
            "validity": val_k,
            "validity_pct": None if val_pp is None else round(val_pp, 2),
            "applied": app_k,
            "applied_pct": None if app_pp is None else round(app_pp, 2),
            "executed": exe_k,
            "executed_pct": None if exe_pp is None else round(exe_pp, 2),
            "patch_parse_fail": self.parse_fail,
            "patch_apply_fail": self.apply_fail,
            "collect_fail": self.collect_fail,
            "no_candidate_tests": self.no_candidate_tests,
            "infra_fail": self.infra_fail,
            "other_failure": self.other_failure,
            "generation_errors": self.generation_errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "mean_context_tokens": (
                round(self.total_context_tokens / self.context_rows, 1)
                if self.context_rows
                else 0
            ),
            "generation_ms": round(self.generation_ms, 1),
            "execution_ms": round(self.execution_ms, 1),
        }


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval; abstains when n is too small to be honest."""
    if n < MIN_N_FOR_INTERVAL or n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - margin) * 100, min(1.0, centre + margin) * 100)


def aggregate(strategy: str, records: Iterable[dict[str, Any]]) -> StrategySummary:
    """Fold per-(task, strategy) records into one :class:`StrategySummary`."""
    summary = StrategySummary(strategy=strategy)
    for record in records:
        summary.n_total += 1

        generated = bool(record.get("has_diff")) or record.get("generation_ok") is True
        if record.get("error"):
            summary.generation_errors += 1

        # Cost and wall-clock are summed over every attempt, not only the
        # admitted ones. An arm whose patches fail to apply still paid for the
        # tokens (protocol §5.2), and §5.3 normalises F2P against exactly those
        # token counts -- skipping them would make failing arms look cheap.
        usage = record.get("usage") or {}
        summary.input_tokens += int(usage.get("input_tokens") or 0)
        summary.output_tokens += int(usage.get("output_tokens") or 0)
        summary.generation_ms += float(record.get("elapsed_ms") or 0)

        context = record.get("context") or {}
        if context.get("total_tokens") is not None:
            summary.total_context_tokens += int(context.get("total_tokens") or 0)
            summary.context_rows += 1

        evaluation = record.get("evaluation") or {}
        timings = evaluation.get("timings_ms") or record.get("timings_ms") or {}
        for value in timings.values():
            summary.execution_ms += float(value or 0)

        verdict = record.get("validation") or {}
        valid = bool(verdict.get("valid", False))

        outcome = record.get("outcome")
        if outcome is None:
            if not generated or not valid:
                summary.parse_fail += 1
                continue
            summary.apply_fail += 1
            continue

        code = Outcome(outcome)
        summary.outcome_counts[code.value] = summary.outcome_counts.get(code.value, 0) + 1

        if code in {Outcome.PATCH_PARSE_FAIL}:
            summary.parse_fail += 1
            continue
        if code is Outcome.PATCH_APPLY_FAIL:
            summary.apply_fail += 1
            continue

        # Admitted: the patch parsed, applied, and reached the harness.
        summary.n_admitted += 1
        summary.apply_pass += 1
        # Protocol §5.1 puts validity over N, so it is counted only here --
        # counting it for non-admitted rows would yield a numerator > N.
        summary.validity_pass += 1 if valid else 0

        if code is Outcome.INFRA_FAIL:
            summary.infra_fail += 1
        elif code is Outcome.NO_CANDIDATE_TESTS:
            # Admitted and applied, but the model's tests never ran: counted
            # against the model, and deliberately not as an execution.
            summary.no_candidate_tests += 1
        elif code is Outcome.COLLECT_FAIL:
            summary.collect_fail += 1
            summary.executed += 1
        elif code is Outcome.F2P_SUCCESS:
            summary.f2p_success += 1
            summary.executed += 1
        else:
            summary.other_failure += 1
            summary.executed += 1

    return summary


def outcome_matrix(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """strategy -> outcome -> count. Used by the report's raw-count table."""
    matrix: dict[str, dict[str, int]] = {}
    for record in records:
        strategy = str(record.get("strategy", "?"))
        outcome = str(record.get("outcome") or "not_evaluated")
        matrix.setdefault(strategy, {})
        matrix[strategy][outcome] = matrix[strategy].get(outcome, 0) + 1
    return matrix
