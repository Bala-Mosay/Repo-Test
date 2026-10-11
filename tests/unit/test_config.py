"""Configuration loading: defaults, YAML, environment overrides, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from repotest_bridge.config import AppConfig, find_default_config, load_config
from repotest_bridge.errors import ConfigError


def test_defaults_are_valid(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no shipped YAML on this cwd: pure built-ins
    cfg = load_config(None, environ={})
    assert isinstance(cfg, AppConfig)
    assert cfg.retrieval.max_context_tokens > 0
    assert cfg.generation.temperature == 0.0
    assert cfg.generation.max_output_tokens == 2048
    assert cfg.logging.level == "INFO"


def test_shipped_default_yaml_matches_models(repo_root: Path) -> None:
    cfg = load_config(repo_root / "configs" / "default.yaml", environ={})
    assert cfg.retrieval.per_category_tokens["related_test"] > 0
    assert cfg.evaluation.workdir_name == ".repotest-eval"


def test_environment_overrides_nested_key() -> None:
    cfg = load_config(
        None, environ={"REPB_GENERATION__MAX_OUTPUT_TOKENS": "4096"}
    )
    assert cfg.generation.max_output_tokens == 4096


def test_unknown_environment_key_is_ignored_not_fatal() -> None:
    cfg = load_config(None, environ={"REPB_GENERATION__MAX_TOKENS": "4096"})
    assert cfg.generation.max_output_tokens == 2048


def test_environment_can_raise_budget() -> None:
    cfg = load_config(None, environ={"REPB_RETRIEVAL__MAX_CONTEXT_TOKENS": "12345"})
    assert cfg.retrieval.max_context_tokens == 12345


def test_invalid_value_is_config_error() -> None:
    with pytest.raises(ConfigError, match="configuration invalid"):
        load_config(None, environ={"REPB_GENERATION__TEMPERATURE": "9.9"})


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ConfigError):
        load_config(None, environ={"REPB_LOGGING__LEVEL": "shout"})


def test_missing_explicit_file_raises() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config("does/not/exist.yaml")


def test_scalar_root_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(bad, environ={})


def test_overrides_beat_environment() -> None:
    cfg = load_config(
        None,
        environ={"REPB_GENERATION__MAX_OUTPUT_TOKENS": "111"},
        overrides={"generation": {"max_output_tokens": 222}},
    )
    assert cfg.generation.max_output_tokens == 222


def test_find_default_config_locates_repo_file(repo_root: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo_root)
    found = find_default_config()
    assert found is not None and found.name == "default.yaml"


def test_per_category_budget_must_be_positive() -> None:
    with pytest.raises(ConfigError):
        load_config(None, environ={"REPB_RETRIEVAL__PER_CATEGORY_TOKENS__FIXTURE": "0"})
