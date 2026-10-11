"""One generation call: context -> prompt -> model -> parsed diff.

The function is deliberately linear and has no branches on strategy beyond the
single :func:`repotest_bridge.retrieval.build_context` call, so every arm differs
from every other arm *only* in which files it was shown.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from repotest_bridge.config import AppConfig
from repotest_bridge.errors import ProviderError, RepositoryError
from repotest_bridge.generation.parsing import ParsedOutput, parse_completion
from repotest_bridge.generation.prompts import (
    build_messages,
    describe_bundle,
    prompt_digest,
)
from repotest_bridge.providers.base import Provider, ProviderRequest, ProviderResponse
from repotest_bridge.repository import ProjectInfo, inspect_repository
from repotest_bridge.retrieval import build_context
from repotest_bridge.retrieval.strategies import CONTROL, FULL, strategy_names
from repotest_bridge.retrieval.types import ContextBundle
from repotest_bridge.tasks import GenerationView, materialize_checkout


@dataclass
class GenerationRecord:
    """Everything needed to reproduce or audit one generation call."""

    task_id: str
    strategy: str
    prompt_digest: str
    bundle: dict[str, Any]
    messages: list[dict[str, str]] = field(default_factory=list)
    response: dict[str, Any] | None = None
    parsed: ParsedOutput | None = None
    diff: str | None = None
    error: str | None = None
    started_at: float = 0.0
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.diff is not None and self.error is None

    def to_record(self, *, include_prompt: bool = False) -> dict[str, Any]:
        """JSON-safe projection for ``results/<run>/<task>/<strategy>.json``."""
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "strategy": self.strategy,
            "ok": self.ok,
            "error": self.error,
            "prompt_digest": self.prompt_digest,
            "context": self.bundle,
            "usage": self.response,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "has_diff": self.diff is not None,
            "diff_sha256": None,
            "parse_errors": list(self.parsed.errors) if self.parsed else [],
            "rationale": self.parsed.rationale if self.parsed else None,
        }
        if self.diff:
            import hashlib

            payload["diff_sha256"] = hashlib.sha256(self.diff.encode("utf-8")).hexdigest()
        if include_prompt:
            payload["messages"] = self.messages
        return payload


from pathlib import Path  # noqa: E402


def prepare_project(
    view: GenerationView,
    manifest_path: Path,
    workdir: Path,
    *,
    reuse: bool = True,
) -> ProjectInfo:
    """Materialise (or reuse) the base checkout and classify it.

    The checkout is always the *buggy* base state; gold data never enters here.
    """
    target = workdir / view.task_id / "base"
    if not (reuse and target.exists()):
        if target.exists():
            import shutil

            shutil.rmtree(target, ignore_errors=True)
        try:
            materialize_checkout(view, manifest_path, target)
        except RepositoryError:
            raise
    return inspect_repository(target)


def run_generation(
    view: GenerationView,
    project: ProjectInfo,
    config: AppConfig,
    strategy: str,
    provider: Provider,
    *,
    matched_target_tokens: int | None = None,
    seed: int = 0,
    keep_messages: bool = False,
) -> GenerationRecord:
    """Perform exactly one generation call."""
    started = time.perf_counter()
    record = GenerationRecord(
        task_id=view.task_id, strategy=strategy, prompt_digest="", bundle={}
    )
    try:
        bundle: ContextBundle = build_context(
            view,
            project,
            config.retrieval,
            strategy,
            matched_target_tokens=matched_target_tokens,
            seed=seed,
        )
    except Exception as exc:
        record.error = f"retrieval failed: {exc}"
        record.elapsed_ms = (time.perf_counter() - started) * 1000
        return record

    messages = build_messages(view, bundle)
    record.bundle = describe_bundle(view, bundle)
    record.prompt_digest = prompt_digest(view, bundle)
    if keep_messages:
        record.messages = messages

    request = ProviderRequest(
        messages=messages,
        temperature=config.generation.temperature,
        max_tokens=config.generation.max_output_tokens,
        model=getattr(provider, "default_model", "") or "",
    )

    try:
        response: ProviderResponse = provider.complete(request)
    except ProviderError as exc:
        record.error = f"provider error: {exc}"
        record.elapsed_ms = (time.perf_counter() - started) * 1000
        return record

    record.response = response.usage
    record.parsed = parse_completion(response.text)
    if record.parsed.ok:
        record.diff = record.parsed.diff
    else:
        record.error = "parse failure: " + "; ".join(record.parsed.errors)
    record.elapsed_ms = (time.perf_counter() - started) * 1000
    return record


def run_arms(
    view: GenerationView,
    project: ProjectInfo,
    config: AppConfig,
    provider: Provider,
    *,
    strategies: list[str] | None = None,
    seed: int = 0,
    keep_messages: bool = False,
) -> dict[str, GenerationRecord]:
    """Run every requested arm for one task.

    ``repo_aware`` is always evaluated first when the control arm is requested,
    because the control must be sized to the full arm's measured spend (H4).
    """
    requested = list(strategies) if strategies else strategy_names()
    if CONTROL in requested and FULL in requested:
        requested = [FULL] + [s for s in requested if s not in {FULL, CONTROL}] + [CONTROL]
    elif CONTROL in requested:
        requested = [s for s in requested if s != CONTROL] + [CONTROL]

    out: dict[str, GenerationRecord] = {}
    full_spend: int | None = None

    for name in requested:
        target = None
        if name == CONTROL:
            if full_spend is None:
                full_record = run_generation(
                    view, project, config, FULL, provider,
                    seed=seed, keep_messages=keep_messages,
                )
                out[FULL] = full_record
                full_spend = full_record.bundle.get("total_tokens")
            target = int(full_spend or config.retrieval.max_context_tokens)
        out[name] = run_generation(
            view,
            project,
            config,
            name,
            provider,
            matched_target_tokens=target,
            seed=seed,
            keep_messages=keep_messages,
        )
    return out
