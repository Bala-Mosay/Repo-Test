"""The nine mutually exclusive outcome codes.

``classify`` is a pure function of observations. The test suite drives it into
every one of the nine codes by construction (``tests/unit/test_outcomes.py``),
so a new edge case can never silently widen or narrow the outcome space.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum

from repotest_bridge.evaluation.harness import COLLECTION_MARKERS, CommandResult


class Outcome(StrEnum):
    PATCH_PARSE_FAIL = "patch_parse_fail"
    PATCH_APPLY_FAIL = "patch_apply_fail"
    COLLECT_FAIL = "collect_fail"
    NO_CANDIDATE_TESTS = "no_candidate_tests"
    INFRA_FAIL = "infra_fail"
    UNEXPECTED_PASS_ON_BUGGY = "unexpected_pass_on_buggy"
    FAILS_ON_FIXED = "fails_on_fixed"
    ERROR_ON_BOTH = "error_on_both"
    F2P_SUCCESS = "f2p_success"

    @property
    def is_success(self) -> bool:
        return self is Outcome.F2P_SUCCESS

    @property
    def is_infra(self) -> bool:
        # NO_CANDIDATE_TESTS is deliberately excluded: the harness ran fine,
        # the model simply put its tests where the suite never looks.
        return self in {Outcome.INFRA_FAIL, Outcome.COLLECT_FAIL}

    @classmethod
    def ordered(cls) -> tuple[Outcome, ...]:
        return (
            cls.F2P_SUCCESS,
            cls.UNEXPECTED_PASS_ON_BUGGY,
            cls.FAILS_ON_FIXED,
            cls.ERROR_ON_BOTH,
            cls.NO_CANDIDATE_TESTS,
            cls.COLLECT_FAIL,
            cls.INFRA_FAIL,
            cls.PATCH_APPLY_FAIL,
            cls.PATCH_PARSE_FAIL,
        )


SUCCESS = Outcome.F2P_SUCCESS

# pytest exit codes: 0 ok, 1 failures, 2 interrupted/collection,
# 3 internal error, 4 usage error, 5 no tests collected.
_COLLECTION_EXIT_CODES = frozenset({2, 4, 5})
_INTERNAL_EXIT_CODES = frozenset({3})

_FAILED_LINE = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+.*)?$", re.MULTILINE)
_SUMMARY = re.compile(
    r"^=+\s*(?P<detail>.+?)\s*=+$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class RunObservation:
    """What one test run on one version of the repository actually did."""

    version: str  # "buggy" | "fixed"
    command: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    output: str = ""
    duration_ms: float = 0.0
    truncated: bool = False
    launch_error: str | None = None

    # -- derived ----------------------------------------------------------

    @property
    def ran(self) -> bool:
        return self.launch_error is None and not self.timed_out and self.exit_code is not None

    @property
    def text(self) -> str:
        return self.output

    @property
    def collection_markers(self) -> list[str]:
        lowered = self.output.lower()
        return [m for m in COLLECTION_MARKERS if m in lowered]

    @property
    def collected(self) -> bool:
        """Whether the framework actually imported and enumerated tests."""
        if not self.ran:
            return False
        if self.exit_code in _COLLECTION_EXIT_CODES:
            return False
        if self.collection_markers:
            # A collection marker alongside a failure can still mean partial
            # collection; pytest prints 'collected N items' first when it worked.
            return bool(re.search(r"collected \d+ item", self.output))
        if self.exit_code in _INTERNAL_EXIT_CODES:
            return False
        if "no tests ran" in self.output.lower():
            return False
        return self.exit_code is not None

    @property
    def test_ids(self) -> list[str]:
        return _test_ids(self.output)

    @property
    def failed_ids(self) -> list[str]:
        return [m.group(2) for m in _FAILED_LINE.finditer(self.output) if m.group(1) == "FAILED"]

    @property
    def errored_ids(self) -> list[str]:
        return [m.group(2) for m in _FAILED_LINE.finditer(self.output) if m.group(1) == "ERROR"]

    @property
    def all_passed(self) -> bool:
        return self.exit_code == 0 and not self.failed_ids and not self.errored_ids

    @property
    def has_execution_error(self) -> bool:
        """Tests raised (assertion-less error) rather than failing an assertion."""
        return bool(self.errored_ids)

    @property
    def summary_line(self) -> str:
        for match in _SUMMARY.finditer(self.output):
            return match.group("detail")
        return ""


def _test_ids(output: str) -> list[str]:
    return [m.group(2) for m in _FAILED_LINE.finditer(output)]


@dataclass(frozen=True)
class ApplyObservation:
    """Whether a patch could be parsed and applied to a given checkout."""

    parsed: bool
    applied_to_buggy: bool | None
    applied_to_fixed: bool | None
    reason: str = ""

    @property
    def any_apply_failed(self) -> bool:
        return self.applied_to_buggy is False or self.applied_to_fixed is False


@dataclass
class Classification:
    outcome: Outcome
    reasons: list[str] = field(default_factory=list)
    notes: str = ""


def _infra_detected(*results: RunObservation | None) -> str | None:
    for run in results:
        if run is None:
            continue
        if run.launch_error:
            return f"launch error on {run.version}: {run.launch_error}"
        if run.timed_out:
            return f"timeout on {run.version} after {run.duration_ms:.0f} ms"
        if run.exit_code in _INTERNAL_EXIT_CODES:
            return f"pytest internal error on {run.version} (exit 3)"
        if run.exit_code is None:
            return f"no exit code recorded on {run.version}"
        lowered = run.output.lower()
        if "modulenotfounderror" in lowered and not run.failed_ids and not run.errored_ids:
            return f"missing dependency on {run.version}"
    return None


def classify(
    *,
    apply_obs: ApplyObservation,
    buggy: RunObservation | None,
    fixed: RunObservation | None,
    new_fixed_failures: Iterable[str] = (),
    candidate_files_collected: int | None = None,
) -> Classification:
    """Map observations onto exactly one outcome code.

    Order is the protocol's precedence (docs/experiment_protocol.md §4):
    parse -> apply -> infrastructure -> collection -> candidate-not-collected
    -> the four semantic outcomes.

    ``candidate_files_collected`` is how many of the patch's own target files
    entered the suite, or ``None`` when that could not be measured. ``None``
    preserves the historical behaviour instead of guessing.
    """
    if not apply_obs.parsed:
        return Classification(
            Outcome.PATCH_PARSE_FAIL,
            [apply_obs.reason or "structured output or diff would not parse"],
        )

    if apply_obs.any_apply_failed:
        return Classification(
            Outcome.PATCH_APPLY_FAIL,
            [apply_obs.reason or "diff rejected by the target checkout"],
        )

    if buggy is None or fixed is None:
        return Classification(
            Outcome.INFRA_FAIL, ["one or both checkouts were never executed"]
        )

    infra_reason = _infra_detected(buggy, fixed)
    if infra_reason:
        return Classification(Outcome.INFRA_FAIL, [infra_reason])

    if not buggy.collected or not fixed.collected:
        side = []
        if not buggy.collected:
            side.append(f"buggy: {buggy.collection_markers or 'not collected'}")
        if not fixed.collected:
            side.append(f"fixed: {fixed.collection_markers or 'not collected'}")
        return Classification(
            Outcome.COLLECT_FAIL,
            ["test collection/import error — infra, not a detected bug", *side],
        )

    if candidate_files_collected == 0:
        return Classification(
            Outcome.NO_CANDIDATE_TESTS,
            [
                "the patch applied and the suite ran, but none of the patch's "
                "own test files were collected — its tests never executed",
                "usually the model placed the file outside the suite's "
                "collection scope, or wrote onto an existing test file",
            ],
        )

    # --- semantic outcomes -------------------------------------------------

    if buggy.all_passed:
        return Classification(
            Outcome.UNEXPECTED_PASS_ON_BUGGY,
            ["passes on the buggy checkout, so the issue is not reproduced"],
        )

    if buggy.has_execution_error and fixed.has_execution_error:
        return Classification(
            Outcome.ERROR_ON_BOTH,
            [
                "raises on both versions (assertion-less error or missing symbol)",
                f"buggy errors: {', '.join(buggy.errored_ids[:5])}",
            ],
        )

    if not fixed.all_passed:
        remaining = sorted(
            set(fixed.failed_ids) | (set(fixed.errored_ids) - set(new_fixed_failures))
        )
        return Classification(
            Outcome.FAILS_ON_FIXED,
            [
                "still fails after the fix: test is wrong or over-specified",
                *(f"remaining: {r}" for r in remaining[:5]),
            ],
        )

    if new_fixed_failures:
        return Classification(
            Outcome.FAILS_ON_FIXED,
            [
                "new failures introduced on the fixed checkout:",
                *sorted(new_fixed_failures)[:5],
            ],
        )

    return Classification(
        Outcome.F2P_SUCCESS,
        [
            "fails on buggy, passes on fixed, no new failures on fixed",
            f"buggy failures: {', '.join(buggy.failed_ids[:5])}",
        ],
    )


def observation_from_result(version: str, result: CommandResult) -> RunObservation:
    """Convert a harness result into a classified observation."""
    launch_error = None
    if result.stderr.startswith("failed to launch"):
        launch_error = result.stderr
    return RunObservation(
        version=version,
        command=result.command,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        output=(result.stdout + "\n" + result.stderr).strip(),
        duration_ms=result.duration_ms,
        truncated=result.truncated,
        launch_error=launch_error,
    )


def failing_set(run: RunObservation) -> set[str]:
    return set(run.failed_ids) | set(run.errored_ids)


def with_output(run: RunObservation, extra: str) -> RunObservation:
    return replace(run, output=(run.output + "\n" + extra).rstrip())
