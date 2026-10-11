"""``repotest-bridge`` command-line interface.

Commands that do **not** execute repository code: ``doctor``, ``retrieve``,
``generate``, ``validate``, ``report``. Only ``evaluate`` and ``research``
(with ``--no-dry-run``) launch repository tests (protocol section 4.1).
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import typer

from repotest_bridge.config import AppConfig, find_default_config, load_config
from repotest_bridge.providers import load_env_file
from repotest_bridge.reporting import RunRecord

app = typer.Typer(
    name="repotest-bridge",
    help="Repository-aware context for issue-conditioned regression-test generation.",
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


@app.callback()
def _bootstrap() -> None:
    """Load a project `.env` (if present) before any command runs.

    Shell-exported values always win over the file, and only credential-shaped
    names are ever read; nothing is printed or logged.
    """
    load_env_file()
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _load(
    config_path: str | None,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    try:
        return load_config(config_path, overrides=overrides)
    except Exception as exc:  # ConfigError and friends
        typer.secho(f"configuration error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def _load_tasks(
    manifest: str,
    splits: Sequence[str] | None,
    task_ids: Sequence[str] | None,
    limit: int | None,
) -> tuple[list[Any], Path, dict[str, Any]]:
    from repotest_bridge.tasks import describe_manifest, load_manifest, select_tasks

    try:
        parsed, path = load_manifest(manifest)
    except Exception as exc:
        typer.secho(f"manifest error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    chosen = select_tasks(parsed, splits=splits, task_ids=task_ids)
    if limit is not None:
        chosen = chosen[:limit]
    if not chosen:
        typer.secho(
            "no tasks matched the given --split/--task filters", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)
    return chosen, path, describe_manifest(parsed, path)


def _echo_bundle(bundle: Any) -> None:
    typer.echo(f"strategy      : {bundle.strategy}")
    typer.echo(
        f"context       : {bundle.total_tokens}/{bundle.ceiling_tokens} tokens, "
        f"{len(bundle.items)} items"
    )
    totals = bundle.category_totals()
    for category, tokens in totals.items():
        typer.echo(f"  {category:<16} {tokens:>6} tokens")
    if bundle.dropped:
        typer.echo(f"  dropped ({len(bundle.dropped)}):")
        for reason in bundle.dropped[:10]:
            typer.echo(f"    - {reason}")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor(
    config: str | None = typer.Option(None, "--config", help="path to a YAML config"),
    provider: str = typer.Option("openai", "--provider", help="mock | openai"),
) -> None:
    """Check the environment. Never executes repository code."""
    from repotest_bridge.providers import build_provider, env_summary

    cfg = _load(config)
    typer.echo("RepoTest-Bridge doctor")
    typer.echo(f"  python        : {sys.version.split()[0]} ({sys.executable})")

    default_cfg = find_default_config()
    typer.echo(f"  config        : {default_cfg or '(defaults only)'}")
    typer.echo(
        f"  budgets       : max_context={cfg.retrieval.max_context_tokens} "
        f"per_category={cfg.retrieval.per_category_tokens}"
    )
    typer.echo(
        f"  generation    : temperature={cfg.generation.temperature} "
        f"max_tokens={cfg.generation.max_output_tokens} retries={cfg.generation.max_retries}"
    )

    import shutil

    typer.echo(f"  git           : {shutil.which('git') or 'MISSING (needed for evaluate)'}")

    for key, value in env_summary().items():
        typer.echo(f"  {key:<13}: {value}")

    try:
        provider_obj = build_provider(provider, config=cfg)
        ok, detail = provider_obj.healthcheck()
    except Exception as exc:
        ok, detail = False, str(exc)
    colour = typer.colors.GREEN if ok else typer.colors.YELLOW
    typer.secho(f"  provider      : {'ok' if ok else 'not ready'} - {detail}", fg=colour)
    if hasattr(provider_obj, "close"):
        provider_obj.close()

    if not ok and provider != "mock":
        typer.echo(
            "\nTo run the offline smoke test instead:  repotest-bridge doctor --provider mock"
        )


# ---------------------------------------------------------------------------
# retrieve
# ---------------------------------------------------------------------------


@app.command()
def retrieve(
    manifest: str = typer.Option(..., "--manifest", help="path to manifest.yaml"),
    strategy: str = typer.Option(..., "--strategy", help="strategy name"),
    config: str | None = typer.Option(None, "--config"),
    split: list[str] | None = typer.Option(None, "--split", help="demo|dev|eval"),
    task: list[str] | None = typer.Option(None, "--task", help="task id (repeatable)"),
    limit: int | None = typer.Option(None, "--limit", min=1),
    seed: int = typer.Option(0, "--seed"),
    json_out: bool = typer.Option(False, "--json", help="emit machine-readable output"),
) -> None:
    """Build a context bundle and show what would be sent. No network, no execution."""
    from repotest_bridge.generation import prepare_project
    from repotest_bridge.retrieval import build_context
    from repotest_bridge.retrieval.strategies import get_strategy
    from repotest_bridge.tasks import generation_view

    get_strategy(strategy)
    cfg = _load(config)
    tasks, manifest_path, _ = _load_tasks(manifest, split or None, task or None, limit)

    work = Path(".repotest-eval") / "_retrieve"
    for entry in tasks:
        view = generation_view(entry)
        project = prepare_project(view, manifest_path, work)
        bundle = build_context(view, project, cfg.retrieval, strategy, seed=seed)
        if json_out:
            typer.echo(
                json.dumps(
                    {
                        "task_id": view.task_id,
                        "strategy": bundle.strategy,
                        "total_tokens": bundle.total_tokens,
                        "items": [
                            {"path": i.path, "category": i.category.value, "tokens": i.tokens}
                            for i in bundle.items
                        ],
                        "dropped": bundle.dropped,
                    },
                    indent=2,
                )
            )
        else:
            typer.echo(f"\n=== {view.task_id} ===")
            _echo_bundle(bundle)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@app.command()
def generate(
    manifest: str = typer.Option(..., "--manifest"),
    strategy: str = typer.Option("repo_aware", "--strategy"),
    config: str | None = typer.Option(None, "--config"),
    provider: str = typer.Option("openai", "--provider"),
    split: list[str] | None = typer.Option(None, "--split"),
    task: list[str] | None = typer.Option(None, "--task"),
    limit: int | None = typer.Option(None, "--limit", min=1),
    seed: int = typer.Option(0, "--seed"),
    out: Path = typer.Option(Path("experiments/results"), "--out"),
) -> None:
    """Generate test patches only. Never runs repository tests."""
    from repotest_bridge.generation import prepare_project, run_generation
    from repotest_bridge.providers import build_provider
    from repotest_bridge.reporting import RunRecord
    from repotest_bridge.retrieval.strategies import get_strategy
    from repotest_bridge.tasks import generation_view

    get_strategy(strategy)
    cfg = _load(config)
    tasks, manifest_path, info = _load_tasks(manifest, split or None, task or None, limit)
    mock_fixture = manifest_path.parent / "fixtures" / "mock_response.diff"
    try:
        provider_obj = build_provider(provider, config=cfg, mock_fixture=mock_fixture)
    except Exception as exc:
        typer.secho(f"provider error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    record = RunRecord.create(
        cfg,
        prefix="gen",
        root=out,
        manifest_path=manifest_path,
        strategies=[strategy],
        provider_name=getattr(provider_obj, "name", provider),
        model=getattr(provider_obj, "default_model", ""),
        seed=seed,
        task_ids=[t.task_id for t in tasks],
    )
    work = Path(".repotest-eval") / "_generate"
    typer.echo(f"manifest: {info['path']}  tasks={len(tasks)}  strategy={strategy}")

    try:
        for entry in tasks:
            view = generation_view(entry)
            project = prepare_project(view, manifest_path, work)
            result = run_generation(view, project, cfg, strategy, provider_obj, seed=seed)
            row = result.to_record()
            record.append(row)
            if result.diff:
                record.save_patch(view.task_id, strategy, result.diff)
            status = "ok" if result.ok else f"FAIL ({result.error})"
            typer.echo(f"  {view.task_id:<20} {status}")
    finally:
        record.close()
        if hasattr(provider_obj, "close"):
            provider_obj.close()

    typer.echo(f"\nwrote {record.root / 'tasks.jsonl'}")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    patch: str = typer.Argument(..., help="path to a .diff file, or '-' for stdin"),
) -> None:
    """Structurally validate a patch. No execution."""
    from repotest_bridge.validation import validate_patch

    text = sys.stdin.read() if patch == "-" else Path(patch).read_text(encoding="utf-8")
    verdict = validate_patch(text)
    typer.echo(f"valid  : {verdict.valid}")
    typer.echo(f"paths  : {', '.join(verdict.paths) or '(none)'}")
    typer.echo(f"tests  : {verdict.touches_test_files}")
    for reason in verdict.reasons:
        typer.secho(f"reason : {reason}", fg=typer.colors.YELLOW)
    if not verdict.valid:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


@app.command()
def evaluate(
    manifest: str = typer.Option(..., "--manifest"),
    patch: str = typer.Argument(..., help="path to a .diff file, or '-' for stdin"),
    config: str | None = typer.Option(None, "--config"),
    task: str = typer.Option(..., "--task"),
    strategy: str = typer.Option("adhoc", "--strategy"),
    keep: bool = typer.Option(False, "--keep", help="keep the scratch checkout"),
) -> None:
    """Run fail-to-pass evaluation of one patch. **Executes repository code.**"""
    from repotest_bridge.evaluation import evaluate_patch
    from repotest_bridge.tasks import load_manifest, select_tasks

    cfg = _load(config)
    text = sys.stdin.read() if patch == "-" else Path(patch).read_text(encoding="utf-8")
    manifest_obj, manifest_path = load_manifest(manifest)
    chosen = select_tasks(manifest_obj, task_ids=(task,))
    if not chosen:
        typer.secho(f"task {task!r} not found", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    typer.secho(
        "warning: this command executes the repository's test suite", fg=typer.colors.YELLOW
    )
    result = evaluate_patch(
        chosen[0],
        manifest_path,
        cfg,
        strategy=strategy,
        candidate_diff=text,
        work_root=Path(".repotest-eval") / "_evaluate",
        keep_workdir=keep,
    )
    typer.echo(json.dumps(result.to_record(), indent=2))
    if not result.ok:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@app.command()
def report(
    experiment: str = typer.Argument(..., help="experiment id or directory"),
    root: Path = typer.Option(Path("experiments/results"), "--root"),
    open_file: bool = typer.Option(False, "--print", help="print the markdown to stdout"),
) -> None:
    """Aggregate tasks.jsonl into summary.csv + report.md. No execution."""
    from repotest_bridge.reporting import write_report

    directory = Path(experiment)
    if not directory.is_dir():
        directory = root / experiment
    if not directory.is_dir():
        typer.secho(f"no such experiment directory: {experiment}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    rows: list[dict[str, Any]] = []
    tasks_path = directory / "tasks.jsonl"
    if tasks_path.is_file():
        for line in tasks_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not rows:
        typer.secho(f"no records in {tasks_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else None
    csv_path, md_path = write_report(
        directory, rows, experiment_id=directory.name, meta=meta
    )
    typer.echo(f"wrote {csv_path}")
    typer.echo(f"wrote {md_path}")
    if open_file:
        typer.echo("\n" + md_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# research (the main experiment)
# ---------------------------------------------------------------------------


@app.command()
def research(
    manifest: str = typer.Option(..., "--manifest"),
    config: str | None = typer.Option(None, "--config"),
    provider: str = typer.Option("openai", "--provider"),
    strategy: list[str] | None = typer.Option(None, "--strategy", help="repeatable; default all"),
    split: list[str] | None = typer.Option(None, "--split"),
    task: list[str] | None = typer.Option(None, "--task"),
    limit: int | None = typer.Option(None, "--limit", min=1),
    seed: int = typer.Option(0, "--seed"),
    experiment_id: str | None = typer.Option(None, "--experiment-id"),
    out: Path = typer.Option(Path("experiments/results"), "--out"),
    dry_run: bool = typer.Option(
        True, "--dry-run/--execute",
        help="generate + validate only (default). --execute runs repository tests.",
    ),
    force: bool = typer.Option(False, "--force", help="re-run finished rows"),
) -> None:
    """Run the full experiment: retrieve -> generate -> validate -> [evaluate]."""
    from repotest_bridge.generation import prepare_project, run_generation
    from repotest_bridge.providers import build_provider
    from repotest_bridge.reporting import RunRecord, write_report
    from repotest_bridge.retrieval.strategies import STRATEGIES, get_strategy
    from repotest_bridge.tasks import generation_view

    cfg = _load(config)
    chosen_strategies = list(strategy) if strategy else list(STRATEGIES)
    for name in chosen_strategies:
        get_strategy(name)

    tasks, manifest_path, info = _load_tasks(manifest, split or None, task or None, limit)

    # `repo_aware` must precede the matched control so the control can be sized.
    if "ctx_random_matched" in chosen_strategies and "repo_aware" in chosen_strategies:
        chosen_strategies = (
            ["repo_aware"]
            + [s for s in chosen_strategies if s not in {"repo_aware", "ctx_random_matched"}]
            + ["ctx_random_matched"]
        )

    mock_fixture = manifest_path.parent / "fixtures" / "mock_response.diff"
    try:
        provider_obj = build_provider(provider, config=cfg, mock_fixture=mock_fixture)
    except Exception as exc:
        typer.secho(f"provider error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    record = RunRecord.create(
        cfg,
        experiment_id=experiment_id,
        prefix="exp",
        root=out,
        manifest_path=manifest_path,
        strategies=chosen_strategies,
        provider_name=getattr(provider_obj, "name", provider),
        model=getattr(provider_obj, "default_model", ""),
        seed=seed,
        task_ids=[t.task_id for t in tasks],
        resume=True,
    )
    if force:
        record._written = set()

    mode = "DRY RUN (no repository code executed)" if dry_run else "EXECUTE (runs test suites)"
    typer.secho(f"experiment : {record.experiment_id}", fg=typer.colors.CYAN)
    typer.secho(f"mode       : {mode}", fg=typer.colors.CYAN)
    typer.echo(
        f"manifest   : {info['path']}  tasks={len(tasks)}  strategies={len(chosen_strategies)}"
    )
    typer.echo(f"model      : {record.model} @ {record.provider_name}")

    work = Path(".repotest-eval") / record.experiment_id
    started_all = time.perf_counter()
    counts = {"ok": 0, "failed": 0, "skipped": 0}

    try:
        for entry in tasks:
            view = generation_view(entry)
            pending = [s for s in chosen_strategies if not record.is_done(view.task_id, s) or force]
            if not pending:
                counts["skipped"] += len(chosen_strategies)
                typer.echo(f"  {view.task_id:<20} already complete, skipping")
                continue

            try:
                project = prepare_project(view, manifest_path, work)
            except Exception as exc:
                typer.secho(f"  {view.task_id:<20} checkout failed: {exc}", fg=typer.colors.RED)
                for name in pending:
                    record.append(
                        {
                            "task_id": view.task_id,
                            "strategy": name,
                            "has_diff": False,
                            "error": f"checkout failed: {exc}",
                            "outcome": None,
                        }
                    )
                continue

            full_spend: int | None = None
            if "ctx_random_matched" in pending and "repo_aware" in pending:
                rest = [s for s in pending if s not in {"repo_aware", "ctx_random_matched"}]
                pending = ["repo_aware", *rest, "ctx_random_matched"]

            for name in pending:
                target = None
                if name == "ctx_random_matched":
                    if full_spend is None:
                        # repo_aware was not in this run's arms: measure its spend
                        # once here, or reuse the spend already on record.
                        full_spend = _recorded_spend(record, view.task_id, "repo_aware")
                    if full_spend is None:
                        first = run_generation(
                            view, project, cfg, "repo_aware", provider_obj, seed=seed
                        )
                        _persist(
                            record, view.task_id, "repo_aware", first, cfg,
                            dry_run, manifest_path, entry, work,
                        )
                        full_spend = first.bundle.get("total_tokens")
                    target = int(full_spend or cfg.retrieval.max_context_tokens)

                result = run_generation(
                    view, project, cfg, name, provider_obj,
                    matched_target_tokens=target, seed=seed,
                )
                if name == "repo_aware" and full_spend is None:
                    # Size the matched control from *this* request, so the control
                    # is compared against the spend that was actually recorded.
                    full_spend = result.bundle.get("total_tokens")
                row = _persist(
                    record, view.task_id, name, result, cfg, dry_run, manifest_path, entry, work
                )
                outcome = row.get("outcome")
                status = outcome or ("generated" if result.ok else f"FAIL {result.error}")
                counts["ok" if outcome == "f2p_success" else "failed"] += 1
                colour = (
                    typer.colors.GREEN if outcome == "f2p_success" else typer.colors.RESET
                )
                typer.secho(f"  {view.task_id:<20} {name:<20} {status}", fg=colour)
    finally:
        record.close()
        if hasattr(provider_obj, "close"):
            provider_obj.close()

    rows = record.read_tasks()
    csv_path, md_path = write_report(
        record.root, rows, experiment_id=record.experiment_id, meta=record.write_meta()
    )
    typer.echo(
        f"\ndone in {time.perf_counter() - started_all:.1f}s - "
        f"rows={len(rows)}; wrote {csv_path.name} and {md_path.name}"
    )
    typer.echo(f"results: {record.root}")


def _recorded_spend(record: Any, task_id: str, strategy: str) -> int | None:
    """Context tokens already on record for ``(task_id, strategy)``, if any.

    Lets a resumed run size the token-matched control without paying for a
    second ``repo_aware`` generation.
    """
    for row in record.read_tasks():
        if row.get("task_id") == task_id and row.get("strategy") == strategy:
            total = (row.get("context") or {}).get("total_tokens")
            return int(total) if total else None
    return None


def _persist(
    record: RunRecord,
    task_id: str,
    strategy: str,
    result: Any,
    cfg: AppConfig,
    dry_run: bool,
    manifest_path: Path,
    entry: Any,
    work: Path,
) -> dict[str, Any]:
    """Validate, optionally evaluate, then append one row."""
    from repotest_bridge.evaluation import evaluate_patch
    from repotest_bridge.validation import validate_patch

    row = result.to_record()
    row["strategy"] = strategy

    verdict = validate_patch(result.diff) if result.diff else None
    row["validation"] = None if verdict is None else {
        "valid": verdict.valid,
        "paths": verdict.paths,
        "reasons": verdict.reasons,
        "only_test_paths": verdict.only_test_paths,
    }

    if verdict is not None and verdict.valid and result.diff:
        record.save_patch(task_id, strategy, result.diff)

    if not dry_run and verdict is not None and verdict.valid and result.diff:
        eval_result = evaluate_patch(
            entry,
            manifest_path,
            cfg,
            strategy=strategy,
            candidate_diff=result.diff,
            work_root=work / "_eval",
            source_base=work / task_id / "base",
        )
        row["outcome"] = eval_result.outcome.value
        row["evaluation"] = eval_result.to_record()
    else:
        row["outcome"] = None

    record.append(row)
    return row


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    app()
