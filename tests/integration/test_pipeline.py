"""Offline end-to-end pipeline tests.

Marked ``integration``: these build real context, call the mock provider, parse
and validate the patch, and (for the marked-slow test) actually execute the
demo repository's test suite. They never touch the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repotest_bridge.evaluation import Outcome, evaluate_patch
from repotest_bridge.generation.generator import run_generation
from repotest_bridge.providers import build_provider
from repotest_bridge.repository import inspect_repository
from repotest_bridge.retrieval import build_context
from repotest_bridge.retrieval.strategies import strategy_names
from repotest_bridge.validation import validate_patch

pytestmark = pytest.mark.integration

DEMO_MANIFEST = Path("examples/demo_task/manifest.yaml")
DEMO_REPO = Path("examples/demo_task/repo")
MOCK_FIXTURE = Path("examples/demo_task/fixtures/mock_response.diff")


@pytest.fixture(scope="module")
def provider():
    fixture = MOCK_FIXTURE if MOCK_FIXTURE.is_file() else None
    return build_provider("mock", mock_fixture=fixture)


@pytest.fixture(scope="module")
def project():
    if not DEMO_REPO.is_dir():
        pytest.skip("demo checkout missing")
    return inspect_repository(DEMO_REPO)


def test_mock_generation_produces_a_valid_test_patch(
    demo_view, config, project, provider
) -> None:
    record = run_generation(
        demo_view, project, config, "repo_aware", provider, seed=0
    )
    assert record.error is None, record.error
    assert record.diff, "mock provider must yield a diff"
    assert record.parsed is not None and record.parsed.ok

    verdict = validate_patch(record.diff)
    assert verdict.valid, verdict.reasons
    assert verdict.only_test_paths, verdict.paths

    assert record.bundle["total_tokens"] <= config.retrieval.max_context_tokens
    assert record.prompt_digest


def test_every_registered_strategy_generates_within_budget(
    demo_view, config, project, provider
) -> None:
    digests: dict[str, str] = {}
    for strategy in strategy_names():
        record = run_generation(
            demo_view, project, config, strategy, provider, seed=0
        )
        assert record.error is None, (strategy, record.error)
        assert record.diff, strategy
        assert record.bundle["total_tokens"] <= config.retrieval.max_context_tokens, strategy
        assert validate_patch(record.diff).valid, strategy
        digests[strategy] = record.prompt_digest

    # Determinism: identical inputs, identical prompt.
    again = run_generation(
        demo_view, project, config, "repo_aware", provider, seed=0
    )
    assert again.prompt_digest == digests["repo_aware"]

    # The arms are genuinely different contexts, not one prompt repeated.
    assert len(set(digests.values())) > 1
    assert digests["issue_only"] != digests["ctx_source_only"]


def test_context_budget_is_never_reallocation_safe(
    demo_view, config, project
) -> None:
    for strategy in strategy_names():
        bundle = build_context(demo_view, project, config.retrieval, strategy, seed=0)
        for budget in bundle.budgets:
            assert budget.reallocated is False, strategy
            assert budget.used_tokens <= budget.limit_tokens, strategy
        assert bundle.total_tokens <= bundle.ceiling_tokens, strategy


def test_missing_candidate_is_a_parse_failure(demo_view, config, tmp_path: Path) -> None:
    manifest_path = Path("examples/demo_task/manifest.yaml").resolve()
    from repotest_bridge.tasks import load_manifest, select_tasks

    manifest, _ = load_manifest(manifest_path)
    task = select_tasks(manifest, task_ids=(demo_view.task_id,))[0]

    result = evaluate_patch(
        task,
        manifest_path,
        config,
        strategy="adhoc",
        candidate_diff=None,
        work_root=tmp_path / "eval",
    )
    assert result.outcome is Outcome.PATCH_PARSE_FAIL
    assert result.observations == {}


@pytest.mark.slow
def test_offline_pipeline_reaches_f2p_success(
    demo_view, config, project, provider, tmp_path: Path
) -> None:
    """retrieve -> generate -> validate -> evaluate, entirely offline."""
    manifest_path = Path("examples/demo_task/manifest.yaml").resolve()
    from repotest_bridge.tasks import load_manifest, select_tasks

    manifest, _ = load_manifest(manifest_path)
    task = select_tasks(manifest, task_ids=(demo_view.task_id,))[0]

    record = run_generation(demo_view, project, config, "repo_aware", provider, seed=0)
    verdict = validate_patch(record.diff or "")
    assert verdict.valid, verdict.reasons

    result = evaluate_patch(
        task,
        manifest_path,
        config,
        strategy="repo_aware",
        candidate_diff=record.diff,
        work_root=tmp_path / "eval",
    )
    assert result.outcome is Outcome.F2P_SUCCESS, (result.outcome, result.reasons)
    assert result.reasons


def test_unsafe_patch_never_reaches_the_suite(config, tmp_path: Path) -> None:
    """A structurally invalid patch is a parse failure and is never applied."""
    from repotest_bridge.tasks import load_manifest, select_tasks

    manifest_path = Path("examples/demo_task/manifest.yaml").resolve()
    manifest, _ = load_manifest(manifest_path)
    task = select_tasks(manifest, task_ids=("demo-001",))[0]

    prod_diff = (
        "--- a/pricinglib/pricing.py\n"
        "+++ b/pricinglib/pricing.py\n"
        "@@ -1,5 +1,5 @@\n"
        "-    if quantity > threshold:\n"
        "+    if quantity >= threshold:\n"
    )
    result = evaluate_patch(
        task,
        manifest_path,
        config,
        strategy="bad",
        candidate_diff=prod_diff,
        work_root=tmp_path / "eval",
        validation_reasons=["patch does not touch any file that looks like a test"],
    )
    assert result.outcome is Outcome.PATCH_PARSE_FAIL
    assert result.observations == {}
    assert any("test" in reason for reason in result.reasons)
