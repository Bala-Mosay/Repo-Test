"""Strategy registry -- the single branch point of the experiment.

``build_bundle`` is the *only* function in the codebase that decides which
artifact classes a generation call receives. Strategies are declarative
compositions of named extractors, so an arm is a line of data, not a code path.
This is docs/architecture.md invariant 2.
"""

from __future__ import annotations

from dataclasses import dataclass

from repotest_bridge.config import RetrievalConfig
from repotest_bridge.retrieval.budget import apply_budget, estimate_tokens
from repotest_bridge.retrieval.extractors import (
    ExtractionContext,
    extract_random_control,
    run_extractor,
)
from repotest_bridge.retrieval.types import ArtifactCategory, ContextBundle, ContextItem

SOURCE = "source"
ABLATED = ("related_test", "fixture", "conftest", "helper", "import_edge", "project_config")


@dataclass(frozen=True)
class StrategySpec:
    name: str
    extractors: tuple[str, ...]
    description: str
    role: str  # "baseline" | "full" | "ablation" | "control"

    @property
    def includes_source_anchor(self) -> bool:
        return SOURCE in self.extractors


def _ablation(one: str) -> StrategySpec:
    return StrategySpec(
        name=f"ctx_{one}",
        extractors=(SOURCE, one),
        description=f"issue + source + {one} (one artifact class, budget-normalised)",
        role="ablation",
    )


STRATEGIES: dict[str, StrategySpec] = {
    spec.name: spec
    for spec in (
        StrategySpec(
            name="issue_only",
            extractors=(),
            description="issue text only; no repository context whatsoever",
            role="baseline",
        ),
        StrategySpec(
            name="ctx_source_only",
            extractors=(SOURCE,),
            description="issue + ranked source files; anchor for every ablation delta",
            role="baseline",
        ),
        *(_ablation(one) for one in ABLATED),
        StrategySpec(
            name="repo_aware",
            extractors=(SOURCE, *ABLATED),
            description="issue + source + all six artifact classes",
            role="full",
        ),
        StrategySpec(
            name="ctx_random_matched",
            extractors=(),
            description="issue-agnostic random files, sized to the repo_aware spend",
            role="control",
        ),
    )
}

BASELINE = "ctx_source_only"
FULL = "repo_aware"
CONTROL = "ctx_random_matched"
AblationName = str


def strategy_names() -> list[str]:
    return list(STRATEGIES)


def get_strategy(name: str) -> StrategySpec:
    if name not in STRATEGIES:
        raise KeyError(
            f"unknown strategy {name!r}; available: {', '.join(STRATEGIES)}"
        )
    return STRATEGIES[name]


def build_bundle(
    strategy_name: str,
    ctx: ExtractionContext,
    config: RetrievalConfig,
    *,
    matched_target_tokens: int | None = None,
) -> ContextBundle:
    """Materialise the context for one arm.

    ``matched_target_tokens`` is only meaningful for ``ctx_random_matched``:
    it is the measured spend of the ``repo_aware`` arm on this task, so the
    control is size-for-size comparable (docs/research_proposal.md, H4).
    """
    spec = get_strategy(strategy_name)

    if spec.role == "control":
        target = matched_target_tokens or config.max_context_tokens
        items = list(extract_random_control(ctx, target_tokens=target, seed=ctx.seed))
        for item in items:
            item.tokens = item.tokens or estimate_tokens(item.content)
        accepted, budgets, total, dropped = apply_budget(
            items, config, max_items_per_category=len(items) or 1
        )
        # The control's ceiling is its matched target, not the global one.
        if total > target:
            keep: list[ContextItem] = []
            spent = 0
            for item in sorted(accepted, key=lambda i: i.path):
                if spent + item.tokens <= target:
                    keep.append(item)
                    spent += item.tokens
                else:
                    dropped.append(f"{item.path}: over matched target ({target})")
            accepted, total = keep, spent
        return ContextBundle(
            strategy=spec.name,
            items=accepted,
            budgets=budgets,
            total_tokens=total,
            ceiling_tokens=target,
            dropped=dropped,
        )

    candidates: list[ContextItem] = []
    ran: dict[str, int] = {}
    for extractor_name in spec.extractors:
        items = run_extractor(extractor_name, ctx)
        ran[extractor_name] = len(items)
        candidates.extend(items)

    accepted, budgets, total, dropped = apply_budget(candidates, config)
    return ContextBundle(
        strategy=spec.name,
        items=accepted,
        budgets=budgets,
        total_tokens=total,
        ceiling_tokens=config.max_context_tokens,
        dropped=dropped,
    )


def ablation_matrix() -> dict[str, dict[str, str]]:
    """Table used by the report: which class each strategy does/doesn't add."""
    rows: dict[str, dict[str, str]] = {}
    for name, spec in STRATEGIES.items():
        row = {"role": spec.role, "description": spec.description}
        for cls in (SOURCE, *ABLATED):
            row[cls] = "yes" if cls in spec.extractors else "-"
        rows[name] = row
    return rows


def allowed_categories(strategy_name: str) -> frozenset[ArtifactCategory]:
    """Categories a strategy may emit. Asserted after every build."""
    spec = get_strategy(strategy_name)
    if spec.role == "control":
        return frozenset({ArtifactCategory.RANDOM_CONTROL})
    if not spec.extractors:
        return frozenset()
    return frozenset(ArtifactCategory(name) for name in spec.extractors)
