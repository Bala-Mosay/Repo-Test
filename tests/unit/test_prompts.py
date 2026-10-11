"""Prompt construction: two messages, no gold data, stable digest."""

from __future__ import annotations

from repotest_bridge.generation.prompts import (
    SYSTEM_PROMPT,
    build_messages,
    describe_bundle,
    prompt_digest,
    repo_label,
)
from repotest_bridge.retrieval import build_context


def test_prompt_is_exactly_two_messages(demo_view, demo_project, config) -> None:
    bundle = build_context(demo_view, demo_project, config.retrieval, "repo_aware")
    messages = build_messages(demo_view, bundle)
    assert len(messages) == 2
    assert [m["role"] for m in messages] == ["system", "user"]
    assert all(isinstance(m["content"], str) and m["content"] for m in messages)


def test_system_prompt_states_the_task_and_the_rules() -> None:
    assert "regression tests" in SYSTEM_PROMPT
    assert "ONLY add or modify test files" in SYSTEM_PROMPT
    assert "```diff" in SYSTEM_PROMPT


def test_user_prompt_carries_issue_and_task_identity(
    demo_view, demo_project, config
) -> None:
    bundle = build_context(demo_view, demo_project, config.retrieval, "repo_aware")
    user = build_messages(demo_view, bundle)[1]["content"]
    assert demo_view.issue_title in user
    assert demo_view.issue_body in user
    assert demo_view.task_id in user
    assert demo_view.test_command in user
    assert "repo_aware" in user


def test_context_block_lists_selected_files(demo_view, demo_project, config) -> None:
    bundle = build_context(demo_view, demo_project, config.retrieval, "ctx_source_only")
    user = build_messages(demo_view, bundle)[1]["content"]
    assert bundle.total_tokens > 0
    assert "### source" in user
    assert "pricinglib/pricing.py" in user


def test_empty_context_is_explicit_not_silent(demo_view, demo_project, config) -> None:
    bundle = build_context(demo_view, demo_project, config.retrieval, "issue_only")
    user = build_messages(demo_view, bundle)[1]["content"]
    assert "(no repository context retrieved for this strategy)" in user


def test_prompt_contains_no_gold_identifiers(demo_view, demo_project, config) -> None:
    """Structural gold identifiers (not the English word 'fixed') must be absent."""
    leaks = (
        "gold_patch",
        "gold_test_patch",
        "code_patch_file",
        "fixed_commit",
        "fail_to_pass",
        "pass_to_pass",
    )
    for strategy in ("issue_only", "ctx_source_only", "repo_aware"):
        bundle = build_context(demo_view, demo_project, config.retrieval, strategy)
        text = "\n".join(m["content"] for m in build_messages(demo_view, bundle)).lower()
        for marker in leaks:
            assert marker not in text, f"{marker!r} leaked into {strategy} prompt"


def test_prompt_digest_is_stable_and_sensitive(
    demo_view, demo_project, config
) -> None:
    a = build_context(demo_view, demo_project, config.retrieval, "repo_aware")
    b = build_context(demo_view, demo_project, config.retrieval, "repo_aware")
    assert prompt_digest(demo_view, a) == prompt_digest(demo_view, b)
    c = build_context(demo_view, demo_project, config.retrieval, "issue_only")
    assert prompt_digest(demo_view, a) != prompt_digest(demo_view, c)


def test_repo_label_prefers_local_checkout(demo_view, tmp_path) -> None:
    assert demo_view.local_path in repo_label(demo_view)
    remote = demo_view.model_copy(update={"local_path": None, "repo_url": "https://x/y"})
    assert repo_label(remote) == "https://x/y"
    neither = demo_view.model_copy(update={"local_path": None, "repo_url": None})
    assert repo_label(neither) == "unknown"


def test_describe_bundle_is_json_safe(demo_view, demo_project, config) -> None:
    import json

    bundle = build_context(demo_view, demo_project, config.retrieval, "repo_aware")
    described = describe_bundle(demo_view, bundle)
    json.dumps(described)  # must not raise
    assert described["total_tokens"] == bundle.total_tokens
    assert described["strategy"] == "repo_aware"
    assert all("sha256" in item for item in described["items"])


def test_context_separator_is_not_a_diff_header(demo_view, demo_project, config) -> None:
    """`--- path ---` read as a unified-diff header and got echoed into output.

    The separator must not match the pattern that parses file headers out of a
    patch, or the model has something valid-looking to copy.
    """
    from repotest_bridge.generation.parsing import PATH_RE, paths_in_diff

    bundle = build_context(demo_view, demo_project, config.retrieval, "repo_aware")
    block = bundle.as_prompt_block()
    assert "=== FILE:" in block
    assert not any(
        line.startswith("--- ") for line in block.splitlines() if line.strip()
    ), "context still uses a `--- path` separator"
    # Parsed as a diff, the separator must contribute no file paths.
    assert paths_in_diff(block) == []
    assert PATH_RE.search("=== FILE: tests/test_x.py ===") is None


def test_system_prompt_forbids_reusing_and_misplacing_tests() -> None:
    """Each rule answers an observed failure from smoke-real-01."""
    assert "NEW test filename" in SYSTEM_PROMPT
    assert "Never append to, overwrite, or reuse" in SYSTEM_PROMPT
    assert "not the repo root" in SYSTEM_PROMPT
    assert "never executed and scores nothing" in SYSTEM_PROMPT


def test_system_prompt_requires_hunk_counts_to_match() -> None:
    assert "MUST equal the number of lines under it" in SYSTEM_PROMPT


def test_system_prompt_example_uses_a_complete_header() -> None:
    assert "diff --git a/tests/test_example.py" in SYSTEM_PROMPT
    assert "new file mode" in SYSTEM_PROMPT
    assert "--- /dev/null" in SYSTEM_PROMPT
    assert "+++ b/tests/test_example.py" in SYSTEM_PROMPT
