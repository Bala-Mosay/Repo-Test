"""Budget enforcement: per-class ceilings and the no-reallocation invariant."""

from __future__ import annotations

from repotest_bridge.config import RetrievalConfig
from repotest_bridge.retrieval.budget import apply_budget, estimate_tokens, spend_by_category
from repotest_bridge.retrieval.types import ArtifactCategory, ContextItem


def make(category: ArtifactCategory, path: str, tokens: int, score: float = 1.0) -> ContextItem:
    return ContextItem(
        category=category,
        path=path,
        content="x" * tokens,
        score=score,
        tokens=tokens,
        sha256=ContextItem.hash_content("x" * tokens),
    )


def config() -> RetrievalConfig:
    return RetrievalConfig(
        max_context_tokens=1000,
        per_category_tokens={
            "source": 400,
            "related_test": 300,
            "fixture": 200,
            "conftest": 200,
            "helper": 200,
            "project_config": 200,
        },
        max_items_per_category=10,
    )


def test_items_within_a_class_respect_its_ceiling() -> None:
    items = [make(ArtifactCategory.SOURCE, f"src/a{i}.py", 250) for i in range(5)]
    _accepted, budgets, total, dropped = apply_budget(items, config())
    source = next(b for b in budgets if b.category is ArtifactCategory.SOURCE)
    assert source.used_tokens <= 400
    assert total <= 400
    assert dropped, "items over the ceiling must be reported as dropped"


def test_no_reallocation_unused_budget_is_not_handed_to_another_class() -> None:
    """A class that spends nothing must not inflate a class that is full."""
    cfg = config()
    fixtures = [make(ArtifactCategory.FIXTURE, f"tests/f{i}.py", 150) for i in range(4)]
    helpers = [make(ArtifactCategory.HELPER, f"tests/h{i}.py", 150) for i in range(4)]

    accepted, budgets, _, _ = apply_budget([*fixtures, *helpers], cfg)
    fixture_budget = next(b for b in budgets if b.category is ArtifactCategory.FIXTURE)
    helper_budget = next(b for b in budgets if b.category is ArtifactCategory.HELPER)

    # Neither class may exceed its own 200-token allowance, even though the
    # other class left budget unused.
    assert fixture_budget.used_tokens <= 200
    assert helper_budget.used_tokens <= 200
    for item in accepted:
        assert item.category is not ArtifactCategory.FIXTURE or item.tokens <= 200


def test_global_ceiling_applies_across_classes() -> None:
    items = []
    for category in (
        ArtifactCategory.SOURCE,
        ArtifactCategory.RELATED_TEST,
        ArtifactCategory.FIXTURE,
        ArtifactCategory.CONFTEST,
        ArtifactCategory.HELPER,
        ArtifactCategory.PROJECT_CONFIG,
    ):
        items.extend(make(category, f"{category.value}/{i}.py", 150) for i in range(3))
    accepted, _, total, dropped = apply_budget(items, config())
    assert total <= config().max_context_tokens
    assert sum(i.tokens for i in accepted) == total
    assert any("global ceiling" in reason for reason in dropped)


def test_budget_is_deterministic() -> None:
    items = [make(ArtifactCategory.SOURCE, f"s{i}.py", 120, score=i) for i in range(8)]
    first = apply_budget(list(items), config())
    second = apply_budget(list(items), config())
    assert [i.path for i in first[0]] == [i.path for i in second[0]]
    assert first[2] == second[2]


def test_item_cap_is_enforced() -> None:
    cfg = RetrievalConfig(max_context_tokens=100_000, max_items_per_category=2)
    items = [make(ArtifactCategory.SOURCE, f"s{i}.py", 10) for i in range(6)]
    accepted, budgets, _, _ = apply_budget(items, cfg)
    source = next(b for b in budgets if b.category is ArtifactCategory.SOURCE)
    assert source.accepted_items == 2
    assert len(accepted) == 2


def test_spend_by_category_matches_bundle_totals() -> None:
    items = [
        make(ArtifactCategory.SOURCE, "a.py", 100),
        make(ArtifactCategory.SOURCE, "b.py", 50),
        make(ArtifactCategory.HELPER, "h.py", 25),
    ]
    spend = spend_by_category(items)
    assert spend == {"helper": 25, "source": 150}


def test_estimate_tokens_is_monotonic_and_positive() -> None:
    short = estimate_tokens("def f(): pass")
    long = estimate_tokens("def f(): pass\n" * 20)
    assert 0 < short < long
    assert estimate_tokens("") == 0
