"""Gold-information separation -- the project's hardest invariant.

If any of these fail, the run is invalid and its results must be discarded
(docs/experiment_protocol.md section 1.1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repotest_bridge.errors import ManifestError, RepositoryError
from repotest_bridge.tasks import (
    GOLD_FIELD_MARKERS,
    GenerationView,
    assert_generation_view_is_pure,
    content_digest,
    describe_manifest,
    generation_inputs_fingerprint,
    generation_view,
    load_evaluation,
    load_manifest,
    materialize_checkout,
    select_tasks,
    validate_task_resolvable,
)


def test_generation_view_has_no_gold_fields() -> None:
    offending = [
        name
        for name in GenerationView.model_fields
        if any(marker in name.lower() for marker in GOLD_FIELD_MARKERS)
    ]
    assert offending == [], f"GenerationView exposes gold-like fields: {offending}"


def test_pure_guard_rejects_a_poisoned_field() -> None:
    class Poisoned(GenerationView):  # type: ignore[misc]
        gold_patch: str | None = None

    with pytest.raises(AssertionError, match="gold"):
        assert_generation_view_is_pure(Poisoned.model_construct(gold_patch="--- a/x"))


def test_generation_view_cannot_carry_evaluation_data(demo_task) -> None:
    view = generation_view(demo_task)
    dump = view.model_dump()
    for marker in ("evaluation", "fixed_commit", "code_patch_file", "fail_to_pass"):
        assert marker not in dump
    assert view.task_id == demo_task.task_id


def test_evaluation_is_only_reachable_through_load_evaluation(demo_task) -> None:
    gold = load_evaluation(demo_task)
    assert gold is not None
    assert gold.code_patch_file is not None
    assert gold.fail_to_pass, "demo manifest must declare expected F2P node ids"


def test_fingerprint_is_stable_and_content_sensitive(demo_task) -> None:
    view = generation_view(demo_task)
    assert generation_inputs_fingerprint(view) == generation_inputs_fingerprint(view)
    other = view.model_copy(update={"issue_body": "different issue"})
    assert generation_inputs_fingerprint(view) != generation_inputs_fingerprint(other)


ONE_TASK = (
    "  - task_id: {tid}\n"
    "    split: demo\n"
    "    generation: {{issue_title: t, issue_body: b, test_command: 'x'}}\n"
)


def test_manifest_rejects_duplicate_task_ids(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "version: 1\ntasks:\n" + ONE_TASK.format(tid="a") + ONE_TASK.format(tid="a"),
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="duplicate"):
        load_manifest(path)


def test_manifest_rejects_empty_and_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(missing)

    empty = tmp_path / "empty.yaml"
    empty.write_text("version: 1\ntasks: []\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="no tasks"):
        load_manifest(empty)


def test_gold_keys_in_generation_never_reach_the_view(tmp_path: Path) -> None:
    """A gold key under ``generation:`` is dropped, not surfaced to the model."""
    path = tmp_path / "m.yaml"
    path.write_text(
        "version: 1\ntasks:\n  - task_id: a\n    split: eval\n"
        "    generation:\n      issue_title: t\n      issue_body: b\n"
        "      test_command: 'x'\n      gold_patch: 'oops'\n"
        "      fail_to_pass: ['tests/test_a.py::test_x']\n",
        encoding="utf-8",
    )
    manifest, _ = load_manifest(path)
    task = manifest.tasks[0]
    assert not hasattr(task.generation, "gold_patch")
    dump = generation_view(task).model_dump()
    assert "gold_patch" not in dump and "fail_to_pass" not in dump


def test_local_path_must_not_escape_manifest(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text(
        "version: 1\ntasks:\n  - task_id: a\n    split: demo\n"
        "    generation:\n      issue_title: t\n      issue_body: b\n"
        "      test_command: 'x'\n      local_path: ../../etc\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="escape"):
        load_manifest(path)


def test_select_tasks_filters_by_split_and_id(demo_manifest) -> None:
    manifest, _ = demo_manifest
    assert len(select_tasks(manifest, splits=("demo",))) == len(manifest.tasks)
    assert select_tasks(manifest, splits=("eval",)) == []
    chosen = select_tasks(manifest, task_ids=("demo-001",))
    assert [t.task_id for t in chosen] == ["demo-001"]
    assert select_tasks(manifest, task_ids=("nope",)) == []


def test_describe_manifest_reports_shape(demo_manifest) -> None:
    manifest, path = demo_manifest
    info = describe_manifest(manifest, path)
    assert info["task_count"] == len(manifest.tasks)
    assert info["with_gold"] >= 1
    assert info["splits_valid"] is True


def test_demo_task_is_resolvable(demo_manifest) -> None:
    manifest, path = demo_manifest
    for task in manifest.tasks:
        reason = validate_task_resolvable(generation_view(task), path)
        assert reason is None, reason


def test_content_digest_is_stable(small_repo: Path) -> None:
    first = content_digest(small_repo)
    assert first == content_digest(small_repo)
    (small_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    assert content_digest(small_repo) != first


def test_materialize_checkout_copies_without_mutating_source(
    demo_manifest, tmp_path: Path
) -> None:
    manifest, path = demo_manifest
    task = manifest.tasks[0]
    view = generation_view(task)
    dest = tmp_path / "checkout"
    materialize_checkout(view, path, dest)
    assert (dest / "pricinglib" / "pricing.py").is_file()
    # Source checkout untouched and no VCS metadata copied in.
    assert not (dest / ".git").exists()
    assert (path.parent / "repo" / "pricinglib" / "pricing.py").is_file()

    with pytest.raises(RepositoryError, match="already exists"):
        materialize_checkout(view, path, dest)
