"""Aggregates ``tasks.jsonl`` into ``summary.csv`` and ``report.md``.

The report prints raw counts next to every percentage and deliberately
abstains from significance language when N is below the protocol threshold
(``metrics.MIN_N_FOR_INTERVAL``).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from repotest_bridge.evaluation.outcomes import Outcome
from repotest_bridge.metrics import (
    MIN_N_FOR_INTERVAL,
    StrategySummary,
    aggregate,
    outcome_matrix,
    wilson_interval,
)

CSV_COLUMNS = [
    "strategy",
    "tasks_attempted",
    "N_admitted",
    "f2p_success",
    "f2p_rate_pct",
    "validity",
    "validity_pct",
    "applied",
    "applied_pct",
    "executed",
    "executed_pct",
    "patch_parse_fail",
    "patch_apply_fail",
    "collect_fail",
    "no_candidate_tests",
    "infra_fail",
    "other_failure",
    "generation_errors",
    "input_tokens",
    "output_tokens",
    "mean_context_tokens",
    "generation_ms",
    "execution_ms",
]


def _pct(triple: tuple[int, int, float | None]) -> str:
    k, n, pp = triple
    if pp is None:
        return f"{k}/{n} (n/a)"
    return f"{k}/{n} ({pp:.1f}%)"


def summarise(records: Iterable[dict[str, Any]]) -> list[StrategySummary]:
    """Fold records per strategy, preserving a stable, declared order."""
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_strategy.setdefault(str(record.get("strategy", "?")), []).append(record)
    ordered = sorted(by_strategy)
    # Prefer the protocol's declaration order when it is known.
    from repotest_bridge.retrieval.strategies import STRATEGIES

    declared = [n for n in STRATEGIES if n in by_strategy]
    rest = [n for n in ordered if n not in declared]
    return [aggregate(name, by_strategy[name]) for name in declared + rest]


def write_summary_csv(summaries: list[StrategySummary], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for summary in summaries:
            writer.writerow(summary.to_row())
    return path


def render_report(
    summaries: list[StrategySummary],
    records: list[dict[str, Any]],
    *,
    experiment_id: str,
    meta: dict[str, Any] | None = None,
) -> str:
    out = io.StringIO()
    w = out.write

    w(f"# RepoTest-Bridge report — `{experiment_id}`\n\n")
    if meta:
        if meta.get("pipeline_validation") or meta.get("provider") == "mock":
            w(
                "> **PIPELINE VALIDATION ONLY.** This run used the offline mock "
                "provider, which returns a checked-in fixture instead of a model "
                "response. The numbers below prove that retrieval, prompting, "
                "patch validation and paired evaluation all work end to end. "
                "**They are not model performance and must never be quoted as "
                "such.**\n\n"
            )
        w("| field | value |\n|---|---|\n")
        meta_keys = (
            "manifest_sha256",
            "model",
            "provider",
            "pipeline_validation",
            "temperature",
            "prompt_template_version",
            "seed",
            "git",
        )
        for key in meta_keys:
            if key in meta:
                w(f"| {key} | `{meta[key]}` |\n")
        w("\n")

    w("## Primary metrics (protocol §5.1)\n\n")
    w("`k/N (pp%)` — counts accompany every percentage.\n\n")
    w(
        "| strategy | N | validity | applied | executed | **F2P** | infra |\n"
        "|---|---:|---|---|---|---|---|\n"
    )
    for s in summaries:
        w(
            f"| `{s.strategy}` | {s.N} | {_pct(s.patch_validity)} | "
            f"{_pct(s.patch_application)} | {_pct(s.test_execution)} | "
            f"**{_pct(s.f2p_rate)}** | {_pct(s.infra_rate)} |\n"
        )
    w("\n")

    codes = [o.value for o in Outcome.ordered()]
    w(f"## Outcome counts (all {len(codes)} codes)\n\n")
    w("| strategy | " + " | ".join(codes) + " |\n")
    w("|---" * (len(codes) + 1) + "|\n")
    matrix = outcome_matrix(records)
    for s in summaries:
        row = matrix.get(s.strategy, {})
        w(f"| `{s.strategy}` | " + " | ".join(str(row.get(c, 0)) for c in codes) + " |\n")
    w("\n")

    w("## Cost (protocol §5.2–5.3)\n\n")
    w("| strategy | input tokens | output tokens | mean ctx tokens | gen ms | exec ms |\n")
    w("|---|---:|---:|---:|---:|---:|\n")
    for s in summaries:
        w(
            f"| `{s.strategy}` | {s.input_tokens} | {s.output_tokens} | "
            f"{s.to_row()['mean_context_tokens']} | {round(s.generation_ms)} | "
            f"{round(s.execution_ms)} |\n"
        )
    w("\n")

    baseline = next((s for s in summaries if s.strategy == "issue_only"), None)
    anchor = next((s for s in summaries if s.strategy == "ctx_source_only"), baseline)
    if anchor is not None and len(summaries) > 1:
        w("## Normalised effect (H3/H4, protocol §5.3)\n\n")
        w("ΔF2P per 1k extra input tokens, measured against `"
          f"`{anchor.strategy}``\n\n")
        w("| strategy | ΔF2P (pp) | Δ tokens | ΔF2P per 1k tokens |\n|---|---:|---:|---:|\n")
        a_pp = (100.0 * anchor.f2p_success / anchor.N) if anchor.N else 0.0
        for s in summaries:
            if s.strategy == anchor.strategy or s.N == 0:
                continue
            s_pp = 100.0 * s.f2p_success / s.N
            d_tok = s.input_tokens - anchor.input_tokens
            metric = s.f2p_per_1k_tokens_vs(anchor)
            metric_s = "n/a" if metric is None else f"{metric:+.3f}"
            w(
                f"| `{s.strategy}` | {s_pp - a_pp:+.1f} | {d_tok:+d} | {metric_s} |\n"
            )
        w("\n")

    w("## Confidence intervals\n\n")
    any_ci = False
    for s in summaries:
        ci = wilson_interval(s.f2p_success, s.N)
        if ci:
            any_ci = True
            w(f"- `{s.strategy}`: F2P {100.0 * s.f2p_success / s.N:.1f}% "
              f"(95% Wilson {ci[0]:.1f}–{ci[1]:.1f}%, n={s.N})\n")
    if not any_ci:
        w(
            f"_No interval reported: every arm has N < {MIN_N_FOR_INTERVAL}. "
            "Counts only; significance language is withheld (protocol §5.4)._\n"
        )
    w("\n")

    w("## Interpretation guardrails\n\n")
    w("- The model used here is **not** the model behind any published baseline; "
      "only within-experiment paired deltas are interpretable.\n")
    w("- All arms are reported, including null and negative results (protocol §5.4).\n")
    w("- `collect_fail` and `infra_fail` are infrastructure, never detected bugs.\n")
    return out.getvalue()


def write_report(
    experiment_dir: Path,
    records: list[dict[str, Any]],
    *,
    experiment_id: str,
    meta: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    summaries = summarise(records)
    csv_path = write_summary_csv(summaries, experiment_dir / "summary.csv")
    text = render_report(summaries, records, experiment_id=experiment_id, meta=meta)
    md_path = experiment_dir / "report.md"
    md_path.write_text(text, encoding="utf-8")
    return csv_path, md_path
