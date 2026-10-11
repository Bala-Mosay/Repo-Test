"""CLI smoke tests. Typer's runner, no shell, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repotest_bridge.cli import app

pytestmark = pytest.mark.integration

runner = CliRunner()
MANIFEST = "examples/demo_task/manifest.yaml"
MOCK_FIXTURE = "examples/demo_task/fixtures/mock_response.diff"


def test_doctor_with_mock_provider() -> None:
    result = runner.invoke(app, ["doctor", "--provider", "mock"])
    assert result.exit_code == 0, result.output
    assert "mock" in result.output.lower()


def test_doctor_reports_missing_provider_configuration() -> None:
    result = runner.invoke(app, ["doctor", "--provider", "nope"])
    assert result.exit_code != 0


def test_retrieve_prints_bundle_for_every_strategy() -> None:
    for strategy in ("issue_only", "ctx_source_only", "repo_aware"):
        result = runner.invoke(
            app,
            [
                "retrieve",
                "--manifest",
                MANIFEST,
                "--strategy",
                strategy,
                "--split",
                "demo",
            ],
        )
        assert result.exit_code == 0, f"{strategy}: {result.output}"


def test_retrieve_rejects_unknown_strategy() -> None:
    result = runner.invoke(
        app,
        ["retrieve", "--manifest", MANIFEST, "--strategy", "gold_context"],
    )
    assert result.exit_code != 0


def test_validate_accepts_a_real_patch(tmp_path: Path) -> None:
    diff = Path(MOCK_FIXTURE)
    if not diff.is_file():
        pytest.skip("mock fixture missing")
    result = runner.invoke(app, ["validate", str(diff)])
    assert result.exit_code == 0, result.output
    assert "valid  : True" in result.output


def test_validate_rejects_prose(tmp_path: Path) -> None:
    bad = tmp_path / "not_a_patch.txt"
    bad.write_text("I changed the comparison operator.\n", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 1
    assert "valid  : False" in result.output


def test_report_round_trips_an_experiment(tmp_path: Path) -> None:
    from repotest_bridge.config import load_config
    from repotest_bridge.reporting import RunRecord

    config = load_config(None, environ={})
    with RunRecord.create(
        config, root=tmp_path, experiment_id="exp-cli", strategies=["repo_aware"]
    ) as record:
        record.append(
            {
                "task_id": "t1",
                "strategy": "repo_aware",
                "has_diff": True,
                "validation": {"valid": True},
                "outcome": "f2p_success",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "context": {"total_tokens": 100},
            }
        )

    result = runner.invoke(
        app, ["report", "exp-cli", "--root", str(tmp_path), "--print"]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "exp-cli" / "report.md").is_file()
    assert (tmp_path / "exp-cli" / "summary.csv").is_file()


def test_research_defaults_to_dry_run() -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "--manifest",
            MANIFEST,
            "--provider",
            "mock",
            "--strategy",
            "issue_only",
            "--strategy",
            "ctx_source_only",
            "--limit",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dry" in result.output.lower()


def test_generate_requires_at_least_one_task() -> None:
    result = runner.invoke(
        app,
        [
            "generate",
            "--manifest",
            MANIFEST,
            "--provider",
            "mock",
            "--task",
            "does-not-exist",
        ],
    )
    assert result.exit_code != 0


def test_research_generates_each_arm_exactly_once(monkeypatch, tmp_path: Path) -> None:
    """Regression: the matched control must not re-bill `repo_aware`.

    `ctx_random_matched` is sized from the `repo_aware` arm's measured token
    spend. If that spend is not carried over from the arm already run in the
    same loop, the CLI calls the provider for `repo_aware` a second time and
    silently pays for a duplicate request.
    """
    import repotest_bridge.generation as generation_pkg

    calls: list[str] = []
    original = generation_pkg.run_generation

    def counting(*args, **kwargs):
        calls.append(str(args[3]) if len(args) > 3 else str(kwargs.get("strategy")))
        return original(*args, **kwargs)

    monkeypatch.setattr(generation_pkg, "run_generation", counting)

    arms = ["issue_only", "ctx_source_only", "repo_aware", "ctx_random_matched"]
    result = runner.invoke(
        app,
        [
            "research",
            "--manifest",
            MANIFEST,
            "--provider",
            "mock",
            *(flag for arm in arms for flag in ("--strategy", arm)),
            "--split",
            "demo",
            "--limit",
            "1",
            "--experiment-id",
            "exp-arm-count",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    # Exactly one provider call per arm -- no duplicate `repo_aware` request.
    assert sorted(calls) == sorted(arms), calls
    assert len(calls) == len(arms)

    # `repo_aware` must run before the matched control so its spend can size it.
    assert calls.index("repo_aware") < calls.index("ctx_random_matched")

    rows = [
        json.loads(line)
        for line in (tmp_path / "exp-arm-count" / "tasks.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(rows) == len(arms)

    # H4: the control is capped at the exact spend of the `repo_aware` arm.
    full = next(r for r in rows if r["strategy"] == "repo_aware")
    control = next(r for r in rows if r["strategy"] == "ctx_random_matched")
    assert control["context"]["ceiling_tokens"] == full["context"]["total_tokens"]

    # Every arm stays under the ceiling it declared.
    for row in rows:
        assert row["context"]["total_tokens"] <= row["context"]["ceiling_tokens"]
