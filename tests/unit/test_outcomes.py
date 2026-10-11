"""All nine outcome codes are reachable by construction.

``classify`` is a pure function, so this module drives it directly rather than
running subprocesses: the outcome space is a property of the classifier, not of
any particular test suite.
"""

from __future__ import annotations

import pytest

from repotest_bridge.evaluation.outcomes import (
    ApplyObservation,
    Outcome,
    RunObservation,
    classify,
)

BUGGY_OK = "tests/test_a.py::test_x FAILED\n1 failed, 3 passed in 0.10s"
# Deliberately NOT an import/collection error: those are classified as
# COLLECT_FAIL, which is a different outcome code.
BUGGY_ERR = "ERROR tests/test_a.py::test_x - AttributeError: 'NoneType'\n1 error in 0.10s"


def run(
    version: str,
    *,
    output: str = "",
    exit_code: int | None = 0,
    timed_out: bool = False,
    launch_error: str | None = None,
) -> RunObservation:
    return RunObservation(
        version=version,
        command="python -m pytest -q",
        exit_code=exit_code,
        timed_out=timed_out,
        output=output,
        launch_error=launch_error,
    )


APPLIED = ApplyObservation(parsed=True, applied_to_buggy=True, applied_to_fixed=True)


def test_parse_failure() -> None:
    result = classify(
        apply_obs=ApplyObservation(parsed=False, applied_to_buggy=None, applied_to_fixed=None),
        buggy=run("buggy"),
        fixed=run("fixed"),
    )
    assert result.outcome is Outcome.PATCH_PARSE_FAIL


def test_apply_failure_on_buggy() -> None:
    result = classify(
        apply_obs=ApplyObservation(
            parsed=True, applied_to_buggy=False, applied_to_fixed=True, reason="nope"
        ),
        buggy=run("buggy"),
        fixed=run("fixed"),
    )
    assert result.outcome is Outcome.PATCH_APPLY_FAIL


def test_apply_failure_on_fixed() -> None:
    result = classify(
        apply_obs=ApplyObservation(
            parsed=True, applied_to_buggy=True, applied_to_fixed=False, reason="conflict"
        ),
        buggy=run("buggy"),
        fixed=run("fixed"),
    )
    assert result.outcome is Outcome.PATCH_APPLY_FAIL


def test_infra_fail_never_executed() -> None:
    result = classify(apply_obs=APPLIED, buggy=None, fixed=None)
    assert result.outcome is Outcome.INFRA_FAIL


def test_infra_fail_timeout() -> None:
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", timed_out=True, exit_code=None, output="hung"),
        fixed=run("fixed", output="1 passed"),
    )
    assert result.outcome is Outcome.INFRA_FAIL
    assert "timeout" in result.reasons[0]


def test_infra_fail_launch_error() -> None:
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", launch_error="failed to launch: gone"),
        fixed=run("fixed", output="1 passed"),
    )
    assert result.outcome is Outcome.INFRA_FAIL


def test_collect_fail_on_buggy() -> None:
    output = "ERRORS\nerrors while collecting\n"
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output=output, exit_code=2),
        fixed=run("fixed", output="1 passed"),
    )
    assert result.outcome is Outcome.COLLECT_FAIL
    assert result.outcome.is_infra


def test_collect_fail_when_no_tests_ran() -> None:
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output="no tests ran in 0.01s", exit_code=5),
        fixed=run("fixed", output="1 passed"),
    )
    assert result.outcome is Outcome.COLLECT_FAIL


def test_unexpected_pass_on_buggy() -> None:
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output="4 passed in 0.10s", exit_code=0),
        fixed=run("fixed", output="4 passed in 0.10s", exit_code=0),
    )
    assert result.outcome is Outcome.UNEXPECTED_PASS_ON_BUGGY


def test_error_on_both() -> None:
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output=BUGGY_ERR, exit_code=1),
        fixed=run("fixed", output=BUGGY_ERR, exit_code=1),
    )
    assert result.outcome is Outcome.ERROR_ON_BOTH


def test_fails_on_fixed() -> None:
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output=BUGGY_OK, exit_code=1),
        fixed=run("fixed", output="tests/test_a.py::test_x FAILED\n1 failed", exit_code=1),
    )
    assert result.outcome is Outcome.FAILS_ON_FIXED
    assert not result.outcome.is_success


def test_fails_on_fixed_even_when_output_looks_green() -> None:
    """The harness may detect new fixed-checkout failures the summary hides."""
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output=BUGGY_OK, exit_code=1),
        fixed=run("fixed", output="4 passed in 0.10s", exit_code=0),
        new_fixed_failures=["tests/test_a.py::test_new"],
    )
    assert result.outcome is Outcome.FAILS_ON_FIXED
    assert any("new failures" in reason for reason in result.reasons)


def test_f2p_success() -> None:
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output=BUGGY_OK, exit_code=1),
        fixed=run("fixed", output="4 passed in 0.10s", exit_code=0),
    )
    assert result.outcome is Outcome.F2P_SUCCESS
    assert result.outcome.is_success
    assert any("fails on buggy" in reason for reason in result.reasons)


def test_no_candidate_tests() -> None:
    """A green suite means nothing if the patch's tests never entered it."""
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output="4 passed in 0.10s", exit_code=0),
        fixed=run("fixed", output="4 passed in 0.10s", exit_code=0),
        candidate_files_collected=0,
    )
    assert result.outcome is Outcome.NO_CANDIDATE_TESTS
    assert not result.outcome.is_infra  # the harness is fine; the model is not
    assert any("never executed" in reason for reason in result.reasons)


def test_no_candidate_tests_outranks_unexpected_pass() -> None:
    """Both fire on an all-green run; the truer diagnosis must win."""
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output="4 passed in 0.10s", exit_code=0),
        fixed=run("fixed", output="4 passed in 0.10s", exit_code=0),
        candidate_files_collected=0,
    )
    assert result.outcome is not Outcome.UNEXPECTED_PASS_ON_BUGGY


def test_unknown_collection_count_preserves_old_behaviour() -> None:
    """`None` means unmeasured, never "zero tests"."""
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output="4 passed in 0.10s", exit_code=0),
        fixed=run("fixed", output="4 passed in 0.10s", exit_code=0),
        candidate_files_collected=None,
    )
    assert result.outcome is Outcome.UNEXPECTED_PASS_ON_BUGGY


def test_collected_candidate_tests_keep_unexpected_pass() -> None:
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output="4 passed in 0.10s", exit_code=0),
        fixed=run("fixed", output="4 passed in 0.10s", exit_code=0),
        candidate_files_collected=1,
    )
    assert result.outcome is Outcome.UNEXPECTED_PASS_ON_BUGGY


def test_no_candidate_tests_wins_even_when_the_suite_is_red() -> None:
    """Baseline failures cannot be credited to a patch that never ran.

    Collection scope outranks the semantic outcomes: if the patch's own files
    were never collected, whatever the suite did was done without them, so
    `f2p_success` would be attributing the baseline's behaviour to the model.
    """
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output=BUGGY_OK, exit_code=1),
        fixed=run("fixed", output="4 passed in 0.10s", exit_code=0),
        candidate_files_collected=0,
    )
    assert result.outcome is Outcome.NO_CANDIDATE_TESTS


def test_all_nine_outcomes_are_reachable() -> None:
    """The classifier must be able to emit every declared code."""
    observed: set[Outcome] = set()

    cases = [
        ApplyObservation(parsed=False, applied_to_buggy=None, applied_to_fixed=None),
        ApplyObservation(parsed=True, applied_to_buggy=False, applied_to_fixed=True),
        ApplyObservation(parsed=True, applied_to_buggy=True, applied_to_fixed=None),
        ApplyObservation(parsed=True, applied_to_buggy=True, applied_to_fixed=True),
    ]
    for apply_obs in cases:
        observed.add(
            classify(
                apply_obs=apply_obs, buggy=run("buggy"), fixed=run("fixed")
            ).outcome
        )
    observed.add(
        classify(
            apply_obs=APPLIED,
            buggy=run("buggy", timed_out=True, exit_code=None),
            fixed=run("fixed", output="1 passed"),
        ).outcome
    )
    observed.add(
        classify(
            apply_obs=APPLIED,
            buggy=run("buggy", output="errors while collecting", exit_code=2),
            fixed=run("fixed", output="1 passed"),
        ).outcome
    )
    observed.add(
        classify(
            apply_obs=APPLIED,
            buggy=run("buggy", output="4 passed", exit_code=0),
            fixed=run("fixed", output="4 passed", exit_code=0),
        ).outcome
    )
    observed.add(
        classify(
            apply_obs=APPLIED,
            buggy=run("buggy", output="4 passed", exit_code=0),
            fixed=run("fixed", output="4 passed", exit_code=0),
            candidate_files_collected=0,
        ).outcome
    )
    observed.add(
        classify(
            apply_obs=APPLIED,
            buggy=run("buggy", output=BUGGY_ERR, exit_code=1),
            fixed=run("fixed", output=BUGGY_ERR, exit_code=1),
        ).outcome
    )
    observed.add(
        classify(
            apply_obs=APPLIED,
            buggy=run("buggy", output=BUGGY_OK, exit_code=1),
            fixed=run("fixed", output="1 failed", exit_code=1),
        ).outcome
    )
    observed.add(
        classify(
            apply_obs=APPLIED,
            buggy=run("buggy", output=BUGGY_OK, exit_code=1),
            fixed=run("fixed", output="4 passed", exit_code=0),
        ).outcome
    )

    assert observed == set(Outcome.ordered()), (
        f"missing: {sorted(o.value for o in set(Outcome) - observed)}"
    )


def test_error_on_both_needs_real_errors_not_import_failures() -> None:
    """An ImportError is a collection failure, so it must not reach ERROR_ON_BOTH."""
    result = classify(
        apply_obs=APPLIED,
        buggy=run("buggy", output="ERROR tests/a.py::t - ImportError: nope", exit_code=1),
        fixed=run("fixed", output="ERROR tests/a.py::t - ImportError: nope", exit_code=1),
    )
    assert result.outcome is Outcome.COLLECT_FAIL


@pytest.mark.parametrize(
    ("exit_code", "output", "expected_collected"),
    [
        (0, "3 passed in 0.01s", True),
        (1, "1 failed, 2 passed in 0.01s", True),
        (2, "errors while collecting", False),
        (5, "no tests ran in 0.01s", False),
        (None, "timed out", False),
    ],
)
def test_collection_detection(exit_code, output, expected_collected) -> None:
    observation = run("buggy", exit_code=exit_code, output=output)
    assert observation.collected is expected_collected
