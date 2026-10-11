"""Fail-to-pass evaluation. The only package that executes repository code."""

from __future__ import annotations

from repotest_bridge.evaluation.harness import CommandResult, ExecutionPlan, run_command
from repotest_bridge.evaluation.outcomes import (
    ApplyObservation,
    Outcome,
    RunObservation,
    classify,
)
from repotest_bridge.evaluation.runner import (
    EvaluationResult,
    evaluate_patch,
    evaluation_available,
)

__all__ = [
    "ApplyObservation",
    "CommandResult",
    "EvaluationResult",
    "ExecutionPlan",
    "Outcome",
    "RunObservation",
    "classify",
    "evaluate_patch",
    "evaluation_available",
    "run_command",
]
