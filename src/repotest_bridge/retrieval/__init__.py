"""Retrieval entry point.

Offline by construction: no network, no model calls, no gold data. Given a
:class:`~repotest_bridge.tasks.GenerationView` (which cannot carry gold data by
type), a classified checkout, and the strategy name, it returns the budgeted
:class:`~repotest_bridge.retrieval.types.ContextBundle`.
"""

from __future__ import annotations

from repotest_bridge.config import RetrievalConfig
from repotest_bridge.repository import ProjectInfo
from repotest_bridge.retrieval.extractors import ExtractionContext
from repotest_bridge.retrieval.signals import extract_issue_entities
from repotest_bridge.retrieval.strategies import build_bundle, get_strategy
from repotest_bridge.retrieval.types import ArtifactCategory, ContextBundle
from repotest_bridge.tasks import GenerationView

__all__ = [
    "ArtifactCategory",
    "ContextBundle",
    "ExtractionContext",
    "build_context",
    "make_extraction_context",
]


def issue_text(view: GenerationView) -> str:
    parts = [view.issue_title.strip(), view.issue_body.strip()]
    if view.issue_source_url:
        parts.append(f"Source: {view.issue_source_url}")
    return "\n\n".join(p for p in parts if p)


def make_extraction_context(
    view: GenerationView,
    project: ProjectInfo,
    *,
    max_entities: int = 40,
    seed: int = 0,
) -> ExtractionContext:
    text = issue_text(view)
    return ExtractionContext(
        root=project.root,
        project=project,
        issue_text=text,
        issue_entities=extract_issue_entities(text, limit=max_entities),
        seed=seed,
    )


def build_context(
    view: GenerationView,
    project: ProjectInfo,
    config: RetrievalConfig,
    strategy: str,
    *,
    matched_target_tokens: int | None = None,
    seed: int = 0,
) -> ContextBundle:
    """Build one arm's context. The single call site of ``build_bundle``."""
    get_strategy(strategy)  # fail fast on a bad name
    ctx = make_extraction_context(
        view, project, max_entities=config.max_issue_entities, seed=seed
    )
    bundle = build_bundle(
        strategy,
        ctx,
        config,
        matched_target_tokens=matched_target_tokens,
    )
    _assert_categories(strategy, bundle)
    return bundle


def _assert_categories(strategy: str, bundle: ContextBundle) -> None:
    """Invariant: a strategy may never emit an artifact class it did not ask for."""
    from repotest_bridge.retrieval.strategies import allowed_categories

    allowed = allowed_categories(strategy)
    seen = {item.category for item in bundle.items}
    illegal = seen - allowed
    if illegal:
        raise AssertionError(
            f"strategy {strategy!r} emitted forbidden categories "
            f"{sorted(c.value for c in illegal)}; allowed={sorted(c.value for c in allowed)}"
        )
