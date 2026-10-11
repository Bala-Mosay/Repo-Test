"""Paired buggy/fixed evaluation of one generated test patch.

Four bounded test runs per (task, strategy) on two independent checkouts. The
resulting observations are handed to :func:`repotest_bridge.evaluation.outcomes.classify`,
which is the only place an outcome code is decided.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repotest_bridge.config import AppConfig
from repotest_bridge.errors import ExecutionError, PatchValidationError
from repotest_bridge.evaluation.harness import ExecutionPlan, run_command
from repotest_bridge.evaluation.outcomes import (
    ApplyObservation,
    Classification,
    Outcome,
    RunObservation,
    classify,
    failing_set,
    observation_from_result,
)
from repotest_bridge.generation.parsing import paths_in_diff
from repotest_bridge.tasks import (
    EvaluationInputs,
    TaskEntry,
    generation_view,
    load_evaluation,
    materialize_checkout,
)
from repotest_bridge.validation.collect import (
    apply_diff,
    prepare_workdir,
    reset_hard,
)
from repotest_bridge.validation.patch import validate_patch


@dataclass
class EvaluationResult:
    """Full audit trail for one (task, strategy) evaluation."""

    task_id: str
    strategy: str
    outcome: Outcome
    reasons: list[str] = field(default_factory=list)
    apply_obs: ApplyObservation | None = None
    observations: dict[str, RunObservation] = field(default_factory=dict)
    new_fixed_failures: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    commands: dict[str, str] = field(default_factory=dict)
    exit_codes: dict[str, int | None] = field(default_factory=dict)
    output_truncated: bool = False
    workdir: str | None = None
    error: str | None = None
    candidate_files_collected: int | None = None

    @property
    def ok(self) -> bool:
        return self.outcome.is_success

    def to_record(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "strategy": self.strategy,
            "outcome": self.outcome.value,
            "success": self.ok,
            "reasons": self.reasons,
            "new_fixed_failures": self.new_fixed_failures,
            "commands": self.commands,
            "exit_codes": self.exit_codes,
            "timings_ms": {k: round(v, 2) for k, v in self.timings_ms.items()},
            "output_truncated": self.output_truncated,
            "observations": {
                name: {
                    "command": obs.command,
                    "exit_code": obs.exit_code,
                    "timed_out": obs.timed_out,
                    "duration_ms": round(obs.duration_ms, 2),
                    "collected": obs.collected,
                    "failed": obs.failed_ids,
                    "errored": obs.errored_ids,
                    "summary": obs.summary_line,
                    "truncated": obs.truncated,
                }
                for name, obs in self.observations.items()
            },
            "apply": None
            if self.apply_obs is None
            else {
                "parsed": self.apply_obs.parsed,
                "applied_to_buggy": self.apply_obs.applied_to_buggy,
                "applied_to_fixed": self.apply_obs.applied_to_fixed,
                "reason": self.apply_obs.reason,
            },
            "error": self.error,
            "candidate_files_collected": self.candidate_files_collected,
        }


def _norm_path(path: str) -> str:
    path = path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def _collected_files(output: str) -> set[str]:
    """Files pytest actually collected, parsed from ``--collect-only -q``."""
    files: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if "::" not in line or line.startswith("=") or " " in line.split("::", 1)[0]:
            continue
        files.add(_norm_path(line.split("::", 1)[0]))
    return files


def _probe_candidate_collection(
    workdir: Path,
    test_command: str,
    config: AppConfig,
    candidate_diff: str,
    candidate_obs: RunObservation | None,
) -> int | None:
    """How many of the patch's own target files entered the collected suite.

    Returns ``None`` whenever the answer is unknowable -- the probe is
    unsupported by this runner, it failed, or it produced nothing parseable --
    so callers fall back to the historical behaviour instead of guessing.

    Only run in the branch where the patch "passes on buggy": that is the one
    place a silently-uncollected test is indistinguishable from a vacuous one.
    """
    if candidate_obs is None or not candidate_obs.all_passed:
        return None
    targets = {_norm_path(p) for p in paths_in_diff(candidate_diff)}
    if not targets:
        return None
    try:
        result = run_command(
            _plan(f"{test_command} --collect-only -q", workdir), config.evaluation
        )
    except ExecutionError:
        return None
    if result.timed_out or result.exit_code not in (0, 5):
        return None
    got = _collected_files(result.stdout + "\n" + result.stderr)
    if not got:
        return None
    return sum(1 for target in targets if target in got)


def _plan(command: str, cwd: Path) -> ExecutionPlan:
    return ExecutionPlan(raw=command, cwd=cwd)


def _evaluate_dir(
    workdir: Path,
    test_command: str,
    config: AppConfig,
    candidate_diff: str | None,
    gold_diff: str | None,
) -> tuple[
    ApplyObservation,
    dict[str, RunObservation],
    list[str],
    dict[str, float],
    bool,
    int | None,
]:
    """Run the four-checkout protocol inside ``workdir``.

    ``workdir`` is a throwaway copy; nothing outside it is touched.
    """
    apply_reasons: list[str] = []
    obs: dict[str, RunObservation] = {}
    timings: dict[str, float] = {}
    truncated = False
    parsed = True
    collected_targets: int | None = None

    if candidate_diff is None:
        parsed = False
        apply_reasons.append("no patch to apply")

    # ---- baseline buggy (no candidate) ---------------------------------
    reset_hard(workdir)
    t0 = time.perf_counter()
    res = run_command(_plan(test_command, workdir), config.evaluation)
    timings["baseline_buggy"] = (time.perf_counter() - t0) * 1000
    truncated |= res.truncated
    obs["baseline_buggy"] = observation_from_result("buggy", res)

    applied_buggy: bool | None = None
    if candidate_diff is not None and parsed:
        reset_hard(workdir)
        try:
            apply_diff(workdir, candidate_diff)
            applied_buggy = True
        except (PatchValidationError, ExecutionError) as exc:
            applied_buggy = False
            apply_reasons.append(f"buggy checkout: {exc}")
        if applied_buggy:
            t0 = time.perf_counter()
            res = run_command(_plan(test_command, workdir), config.evaluation)
            timings["candidate_buggy"] = (time.perf_counter() - t0) * 1000
            truncated |= res.truncated
            obs["candidate_buggy"] = observation_from_result("buggy", res)
            # The workdir is in exactly the candidate-buggy state right now:
            # this is the only moment the probe can answer "did the patch's
            # own tests ever enter the suite?".
            collected_targets = _probe_candidate_collection(
                workdir, test_command, config, candidate_diff, obs["candidate_buggy"]
            )

    applied_fixed: bool | None = None
    if candidate_diff is not None and parsed and gold_diff is not None:
        reset_hard(workdir)
        try:
            apply_diff(workdir, gold_diff)
            t0 = time.perf_counter()
            res = run_command(_plan(test_command, workdir), config.evaluation)
            timings["baseline_fixed"] = (time.perf_counter() - t0) * 1000
            truncated |= res.truncated
            obs["baseline_fixed"] = observation_from_result("fixed", res)

            apply_diff(workdir, candidate_diff)
            applied_fixed = True
        except (PatchValidationError, ExecutionError) as exc:
            applied_fixed = False
            apply_reasons.append(f"fixed checkout: {exc}")
        if applied_fixed:
            t0 = time.perf_counter()
            res = run_command(_plan(test_command, workdir), config.evaluation)
            timings["candidate_fixed"] = (time.perf_counter() - t0) * 1000
            truncated |= res.truncated
            obs["candidate_fixed"] = observation_from_result("fixed", res)

    apply_obs = ApplyObservation(
        parsed=parsed,
        applied_to_buggy=applied_buggy,
        applied_to_fixed=applied_fixed,
        reason="; ".join(apply_reasons),
    )
    return apply_obs, obs, [], timings, truncated, collected_targets


def evaluate_patch(
    task: TaskEntry,
    manifest_path: Path,
    config: AppConfig,
    *,
    strategy: str,
    candidate_diff: str | None,
    validation_reasons: list[str] | None = None,
    work_root: Path,
    keep_workdir: bool = False,
    source_base: Path | None = None,
) -> EvaluationResult:
    """Evaluate one generated patch. Executes repository code.

    This is the *only* caller of :func:`repotest_bridge.tasks.load_evaluation`.
    It takes the full ``TaskEntry`` precisely because evaluation is the one
    stage entitled to see gold data; it immediately narrows to a
    :class:`GenerationView` for everything except the gold lookup.
    """
    view = generation_view(task)
    evaluation: EvaluationInputs | None = load_evaluation(task)

    if candidate_diff is None:
        return EvaluationResult(
            task_id=view.task_id,
            strategy=strategy,
            outcome=Outcome.PATCH_PARSE_FAIL,
            reasons=validation_reasons or ["no patch produced"],
        )

    verdict = validate_patch(candidate_diff)
    if not verdict.valid:
        return EvaluationResult(
            task_id=view.task_id,
            strategy=strategy,
            outcome=Outcome.PATCH_PARSE_FAIL,
            reasons=verdict.reasons,
        )

    gold_diff: str | None = None
    if evaluation is not None and evaluation.code_patch_file:
        patch_path = (manifest_path.parent / evaluation.code_patch_file).resolve()
        if not patch_path.is_file():
            return EvaluationResult(
                task_id=view.task_id,
                strategy=strategy,
                outcome=Outcome.INFRA_FAIL,
                reasons=[f"gold code patch missing: {evaluation.code_patch_file}"],
            )
        gold_diff = patch_path.read_text(encoding="utf-8")

    if gold_diff is None:
        return EvaluationResult(
            task_id=view.task_id,
            strategy=strategy,
            outcome=Outcome.INFRA_FAIL,
            reasons=[
                "task has no gold code patch; fail-to-pass cannot be established "
                "(add evaluation.code_patch_file or evaluation.fixed_commit)"
            ],
        )

    task_dir = work_root / view.task_id / strategy
    try:
        if source_base is not None and source_base.is_dir():
            workdir = prepare_workdir(source_base, task_dir)
        else:
            base = materialize_checkout(view, manifest_path, work_root / view.task_id / "base")
            workdir = prepare_workdir(base, task_dir)
    except Exception as exc:
        return EvaluationResult(
            task_id=view.task_id,
            strategy=strategy,
            outcome=Outcome.INFRA_FAIL,
            reasons=[f"could not prepare checkout: {exc}"],
        )

    try:
        (
            apply_obs,
            observations,
            _notes,
            timings,
            truncated,
            collected_targets,
        ) = _evaluate_dir(workdir, view.test_command, config, candidate_diff, gold_diff)
    except Exception as exc:  # harness must never crash the run
        _cleanup(task_dir, keep_workdir)
        return EvaluationResult(
            task_id=view.task_id,
            strategy=strategy,
            outcome=Outcome.INFRA_FAIL,
            reasons=[f"harness error: {exc}"],
            workdir=str(task_dir) if keep_workdir else None,
        )

    new_failures: list[str] = []
    base_fixed = observations.get("baseline_fixed")
    cand_fixed = observations.get("candidate_fixed")
    if base_fixed is not None and cand_fixed is not None:
        new_failures = sorted(failing_set(cand_fixed) - failing_set(base_fixed))

    classification: Classification = classify(
        apply_obs=apply_obs,
        buggy=observations.get("candidate_buggy"),
        fixed=observations.get("candidate_fixed"),
        new_fixed_failures=new_failures,
        candidate_files_collected=collected_targets,
    )

    result = EvaluationResult(
        task_id=view.task_id,
        strategy=strategy,
        outcome=classification.outcome,
        reasons=classification.reasons,
        apply_obs=apply_obs,
        observations=observations,
        new_fixed_failures=new_failures,
        timings_ms=timings,
        commands={k: v.command for k, v in observations.items()},
        exit_codes={k: v.exit_code for k, v in observations.items()},
        output_truncated=truncated,
        workdir=str(task_dir) if keep_workdir else None,
        candidate_files_collected=collected_targets,
    )
    _cleanup(task_dir, keep_workdir)
    return result


def _cleanup(path: Path, keep: bool) -> None:
    if keep or not path.exists():
        return
    shutil.rmtree(path, ignore_errors=True)


def evaluation_available(task: TaskEntry) -> bool:
    """Whether fail-to-pass scoring is possible for this task."""
    return load_evaluation(task) is not None
