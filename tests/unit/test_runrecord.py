"""Run-record lifecycle: append-only, idempotent, never overwrites."""

from __future__ import annotations

from pathlib import Path

import pytest

from repotest_bridge.config import load_config
from repotest_bridge.reporting import render_report, summarise, write_summary_csv
from repotest_bridge.reporting.runrecord import RunRecord, new_experiment_id


@pytest.fixture
def config():
    return load_config(None, environ={})


def row(task_id: str, strategy: str, **extra) -> dict:
    return {"task_id": task_id, "strategy": strategy, **extra}


def test_new_experiment_id_is_unique_and_prefixed() -> None:
    ids = {new_experiment_id("exp") for _ in range(20)}
    assert len(ids) == 20
    assert all(i.startswith("exp-") for i in ids)


def test_create_writes_meta_and_directories(config, tmp_path: Path) -> None:
    record = RunRecord.create(
        config, root=tmp_path, experiment_id="exp-test", strategies=["issue_only"]
    )
    assert (tmp_path / "exp-test" / "meta.json").is_file()
    assert (tmp_path / "exp-test" / "patches").is_dir()
    assert (tmp_path / "exp-test" / "logs").is_dir()
    meta = record.write_meta()
    assert meta["strategies"] == ["issue_only"]
    assert meta["prompt_template_version"]
    assert meta["budgets"]["max_context_tokens"] == config.retrieval.max_context_tokens
    record.close()


def test_append_is_idempotent_per_task_and_strategy(config, tmp_path: Path) -> None:
    with RunRecord.create(config, root=tmp_path, experiment_id="exp-idem") as record:
        record.append(row("t1", "s1", outcome="f2p_success"))
        record.append(row("t1", "s1", outcome="infra_fail"))  # ignored
        record.append(row("t1", "s2", outcome="fails_on_fixed"))
        rows = record.read_tasks()
    assert len(rows) == 2
    assert rows[0]["outcome"] == "f2p_success"  # first write wins
    assert {r["strategy"] for r in rows} == {"s1", "s2"}


def test_completed_keys_survives_reopening(config, tmp_path: Path) -> None:
    with RunRecord.create(config, root=tmp_path, experiment_id="exp-resume") as record:
        record.append(row("t1", "s1"))
    with RunRecord.create(config, root=tmp_path, experiment_id="exp-resume") as again:
        assert again.is_done("t1", "s1")
        assert not again.is_done("t1", "s2")
        again.append(row("t1", "s1"))
        again.append(row("t1", "s2"))
        assert len(again.read_tasks()) == 2


def test_create_refuses_to_reuse_a_directory(config, tmp_path: Path) -> None:
    RunRecord.create(config, root=tmp_path, experiment_id="exp-once").close()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        RunRecord.create(
            config, root=tmp_path, experiment_id="exp-once", resume=False
        )


def test_patches_are_never_overwritten(config, tmp_path: Path) -> None:
    with RunRecord.create(config, root=tmp_path, experiment_id="exp-patches") as record:
        first = record.save_patch("task/one", "s1", "--- a/x\n+++ b/x\n@@ -1 +1 @@\n")
        second = record.save_patch("task/one", "s1", "--- a/x\n+++ b/x\n@@ -1 +1 @@\n")
    assert first != second
    assert first.read_text(encoding="utf-8").startswith("--- a/x")
    assert second.read_text(encoding="utf-8").startswith("--- a/x")


def test_logs_are_truncated_not_exploded(config, tmp_path: Path) -> None:
    with RunRecord.create(config, root=tmp_path, experiment_id="exp-logs") as record:
        path = record.save_log("huge.log", "y" * 10_000, max_bytes=100)
    assert path.stat().st_size < 400
    assert b"truncated" in path.read_bytes()


def test_corrupt_tasks_lines_are_skipped(config, tmp_path: Path) -> None:
    with RunRecord.create(config, root=tmp_path, experiment_id="exp-corrupt") as record:
        (record.root / "tasks.jsonl").write_text(
            '{"task_id":"a","strategy":"s"}\nnot json\n\n', encoding="utf-8"
        )
        record.close()
        assert record.read_tasks() == [{"task_id": "a", "strategy": "s"}]
        assert record.completed_keys() == {("a", "s")}


def test_summary_csv_and_report_round_trip(config, tmp_path: Path) -> None:
    records = [
        row("t1", "s", outcome="f2p_success", **{"has_diff": True}),
        row("t2", "s", outcome="fails_on_fixed", **{"has_diff": True}),
        row("t1", "issue_only", outcome="patch_parse_fail", **{"has_diff": False}),
    ]
    summaries = summarise(records)
    assert {s.strategy for s in summaries} == {"s", "issue_only"}

    csv_path = write_summary_csv(summaries, tmp_path / "summary.csv")
    assert csv_path.is_file()
    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert "f2p_success" in header and "N_admitted" in header

    report = render_report(
        summaries, records, experiment_id="exp-x", meta={"provider": "mock"}
    )
    assert "exp-x" in report
    assert "f2p_success" in report
    assert any(s.f2p_success == 1 for s in summaries)


def test_report_never_forgets_denominators(config) -> None:
    records = [row("t1", "s", outcome="f2p_success", **{"has_diff": True})]
    report = render_report(summarise(records), records, experiment_id="e")
    assert "N_admitted" in report or "1/1" in report


def test_meta_marks_mock_runs_as_pipeline_validation(config, tmp_path: Path) -> None:
    """Protocol §6 promises this field; it must actually be written."""
    with RunRecord.create(
        config, root=tmp_path, experiment_id="m", provider_name="mock"
    ) as mock_rec:
        assert mock_rec.write_meta()["pipeline_validation"] is True
    with RunRecord.create(
        config, root=tmp_path, experiment_id="r", provider_name="openai"
    ) as real_rec:
        assert real_rec.write_meta()["pipeline_validation"] is False


def test_report_banner_fires_on_the_field_not_just_the_provider_name() -> None:
    records = [row("t1", "s", outcome="f2p_success", **{"has_diff": True})]
    report = render_report(
        summarise(records), records, experiment_id="pv", meta={"pipeline_validation": True}
    )
    assert "PIPELINE VALIDATION ONLY" in report
    assert "| pipeline_validation | `True` |" in report
