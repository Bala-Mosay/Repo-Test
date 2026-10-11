"""Strategies are declarative data and the single branch point of the experiment."""

from __future__ import annotations

from pathlib import Path

import pytest

from repotest_bridge.retrieval import (
    ExtractionContext,
    build_context,
    make_extraction_context,
)
from repotest_bridge.retrieval.strategies import (
    ABLATED,
    BASELINE,
    CONTROL,
    FULL,
    STRATEGIES,
    ablation_matrix,
    allowed_categories,
    build_bundle,
    get_strategy,
    strategy_names,
)
from repotest_bridge.retrieval.types import ArtifactCategory

# The nine strategies declared in docs/experiment_protocol.md, plus the
# source-only anchor the ablation deltas are measured against.
DECLARED = {
    "issue_only",
    "ctx_source_only",
    "ctx_related_test",
    "ctx_fixture",
    "ctx_conftest",
    "ctx_helper",
    "ctx_import_edge",
    "ctx_project_config",
    "repo_aware",
    "ctx_random_matched",
}


def test_registry_matches_protocol() -> None:
    assert set(strategy_names()) == DECLARED
    assert BASELINE in STRATEGIES
    assert FULL in STRATEGIES
    assert CONTROL in STRATEGIES


def test_unknown_strategy_is_rejected() -> None:
    with pytest.raises(KeyError, match="unknown strategy"):
        get_strategy("magic_context")


def test_ablation_arms_are_source_plus_exactly_one_class() -> None:
    for name in ABLATED:
        spec = get_strategy(f"ctx_{name}")
        assert spec.extractors == ("source", name)
        assert spec.role == "ablation"


def test_full_arm_contains_every_class() -> None:
    spec = get_strategy(FULL)
    assert spec.extractors == ("source", *ABLATED)
    assert spec.role == "full"


def test_issue_only_is_empty_and_control_is_empty() -> None:
    assert get_strategy("issue_only").extractors == ()
    assert get_strategy(CONTROL).extractors == ()


def test_allowed_categories_blocks_smuggling() -> None:
    assert ArtifactCategory.RANDOM_CONTROL not in allowed_categories(FULL)
    assert ArtifactCategory.HELPER not in allowed_categories("ctx_conftest")
    assert allowed_categories(CONTROL) == frozenset({ArtifactCategory.RANDOM_CONTROL})


def test_ablation_matrix_covers_every_strategy() -> None:
    matrix = ablation_matrix()
    assert set(matrix) == DECLARED
    assert matrix["repo_aware"]["related_test"] == "yes"
    assert matrix["ctx_fixture"]["fixture"] == "yes"
    assert matrix["ctx_fixture"]["conftest"] == "-"


def test_issue_only_yields_no_context(
    demo_view, demo_project, config
) -> None:
    bundle = build_context(demo_view, demo_project, config.retrieval, "issue_only")
    assert bundle.items == []
    assert bundle.total_tokens == 0


def test_every_strategy_stays_within_the_global_ceiling(
    demo_view, demo_project, config
) -> None:
    for name in strategy_names():
        bundle = build_context(demo_view, demo_project, config.retrieval, name, seed=7)
        assert bundle.total_tokens <= config.retrieval.max_context_tokens, name
        assert {i.category for i in bundle.items} <= allowed_categories(name), name


def test_issue_only_prompt_still_carries_the_issue(demo_view, demo_project, config) -> None:
    from repotest_bridge.generation.prompts import build_messages

    bundle = build_context(demo_view, demo_project, config.retrieval, "issue_only")
    messages = build_messages(demo_view, bundle)
    assert messages[0]["role"] == "system"
    assert demo_view.issue_title in messages[1]["content"]
    assert "no repository context retrieved" in messages[1]["content"]


def test_control_is_deterministic_for_a_fixed_seed(
    demo_view, demo_project, config
) -> None:
    first = build_context(demo_view, demo_project, config.retrieval, CONTROL, seed=5)
    second = build_context(demo_view, demo_project, config.retrieval, CONTROL, seed=5)
    assert [i.path for i in first.items] == [i.path for i in second.items]
    assert first.total_tokens == second.total_tokens


def test_control_draw_depends_on_the_seed(tmp_path: Path) -> None:
    """The negative control is a *random* draw: seed changes what it selects.

    Uses a purpose-built checkout with many equal-sized files so the draw is
    genuinely under-determined; the small demo checkout would fit in one shot.
    """
    from repotest_bridge.repository import inspect_repository
    from repotest_bridge.retrieval.extractors import extract_random_control

    root = tmp_path / "pool"
    (root / "pkg").mkdir(parents=True)
    for i in range(16):
        (root / "pkg" / f"m{i:02d}.py").write_text("VALUE = 0\n" * 30, encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'pool'\n", encoding="utf-8")
    project = inspect_repository(root)

    draws: set[tuple[str, ...]] = set()
    for seed in range(12):
        ctx = ExtractionContext(
            root=root, project=project, issue_text="issue", issue_entities=[], seed=seed
        )
        items = extract_random_control(ctx, target_tokens=450, seed=seed)
        draws.add(tuple(sorted(i.path for i in items)))
    assert len(draws) > 1, "control draw did not vary with the seed"


def test_control_is_sized_to_the_full_arm(
    demo_view, demo_project, config
) -> None:
    full = build_context(demo_view, demo_project, config.retrieval, FULL, seed=0)
    control = build_context(
        demo_view,
        demo_project,
        config.retrieval,
        CONTROL,
        matched_target_tokens=full.total_tokens,
        seed=0,
    )
    assert control.ceiling_tokens == full.total_tokens
    # Never larger than the arm it is matched against.
    assert control.total_tokens <= full.total_tokens + 1


def test_control_actually_spends_its_matched_target(
    demo_view, demo_project, config
) -> None:
    """H4 needs a *matched* control; a starved one confounds spend with selection.

    The draw pool must be the same candidate pool `repo_aware` draws from --
    source, test, test-tree (conftest/helpers) and config files -- otherwise the
    control exhausts itself on small repositories and never reaches its target.
    """
    full = build_context(demo_view, demo_project, config.retrieval, FULL, seed=0)
    control = build_context(
        demo_view,
        demo_project,
        config.retrieval,
        CONTROL,
        matched_target_tokens=full.total_tokens,
        seed=0,
    )
    # Matching error is allowed to be up to 15%: the draw is a greedy fill over
    # whole files, so the last gap cannot be closed when every remaining file
    # is larger than it. What must not happen is the ~50% starvation a narrow
    # candidate pool produces.
    assert control.total_tokens >= 0.85 * full.total_tokens, (
        f"control spent {control.total_tokens} of a {full.total_tokens} matched "
        f"target (>=85% required); dropped={control.dropped}"
    )


def test_single_branch_point_receives_no_gold_data(
    demo_view, demo_project, config, tmp_path: Path
) -> None:
    """build_bundle must be callable with nothing but generation inputs."""
    ctx = make_extraction_context(demo_view, demo_project)
    bundle = build_bundle(FULL, ctx, config.retrieval)
    assert bundle.strategy == FULL
    assert not hasattr(ctx, "evaluation")
