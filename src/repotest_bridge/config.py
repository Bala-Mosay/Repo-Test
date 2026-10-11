"""Configuration loading and validation.

Precedence (lowest to highest):

1. built-in defaults (the field defaults on the models below)
2. a YAML file -- ``configs/default.yaml`` unless ``--config`` is given
3. environment variables prefixed ``REPB_`` with ``__`` nesting,
   e.g. ``REPB_GENERATION__MAX_OUTPUT_TOKENS=4096``
4. explicit overrides passed by the CLI

Everything is validated by Pydantic; an invalid value raises ``ConfigError``
naming the offending key rather than failing later at use time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from repotest_bridge.errors import ConfigError

ENV_PREFIX = "REPB_"
ENV_NESTED = "__"

DEFAULT_CONFIG_CANDIDATES = (
    Path("configs") / "default.yaml",
    Path.cwd() / "configs" / "default.yaml",
)


class RetrievalConfig(BaseModel):
    """Budgets and limits applied while building prompt context."""

    max_context_tokens: int = Field(default=6000, ge=256, le=200_000)
    per_category_tokens: dict[str, int] = Field(
        default_factory=lambda: {
            "source": 2500,
            "related_test": 2000,
            "fixture": 1000,
            "conftest": 800,
            "helper": 800,
            "project_config": 600,
        }
    )
    max_items_per_category: int = Field(default=4, ge=1, le=64)
    max_issue_entities: int = Field(default=40, ge=1, le=500)

    @field_validator("per_category_tokens")
    @classmethod
    def _positive_budgets(cls, value: dict[str, int]) -> dict[str, int]:
        for key, tokens in value.items():
            if tokens <= 0:
                raise ValueError(f"per_category_tokens[{key!r}] must be > 0, got {tokens}")
        return value


class GenerationConfig(BaseModel):
    """Model inference settings; identical across every experimental arm."""

    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=2048, ge=64, le=131_072)
    max_retries: int = Field(default=3, ge=0, le=10)
    request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)
    retry_backoff_seconds: float = Field(default=2.0, ge=0.0, le=120.0)


class ValidationConfig(BaseModel):
    patch_apply_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    allow_symlink_targets: bool = False


class EvaluationConfig(BaseModel):
    test_timeout_seconds: int = Field(default=300, ge=1, le=7200)
    max_output_bytes: int = Field(default=65_536, ge=1024, le=10_485_760)
    require_explicit_command: bool = True
    workdir_name: str = ".repotest-eval"

    @field_validator("workdir_name")
    @classmethod
    def _not_a_path(cls, value: str) -> str:
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("workdir_name must be a single plain directory name")
        return value


class LoggingConfig(BaseModel):
    model_config = {"populate_by_name": True}

    level: str = "INFO"
    json_output: bool = Field(default=False, alias="json")

    @field_validator("level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"level must be one of {sorted(allowed)}, got {value!r}")
        return upper


class AppConfig(BaseModel):
    """Root configuration object."""

    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _env_overrides(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Translate ``REPB_A__B=1`` into ``{"a": {"b": "1"}}`` (keys lower-cased)."""
    source = os.environ if environ is None else environ
    out: dict[str, Any] = {}
    for raw_key, raw_value in source.items():
        if not raw_key.startswith(ENV_PREFIX):
            continue
        path = raw_key[len(ENV_PREFIX) :]
        if not path:
            continue
        parts = [p.lower() for p in path.split(ENV_NESTED) if p]
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):  # pragma: no cover - malformed nesting
                raise ConfigError(f"conflicting environment override {raw_key}")
        node[parts[-1]] = raw_value
    return out


def _resolve_config_path(explicit: str | Path | None) -> Path | None:
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    for candidate in DEFAULT_CONFIG_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def load_config(
    explicit: str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> AppConfig:
    """Load, merge, and validate configuration."""
    data: dict[str, Any] = {}

    path = _resolve_config_path(explicit)
    if path is not None:
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
        if loaded is not None and not isinstance(loaded, dict):
            raise ConfigError(
                f"config root in {path} must be a mapping, got {type(loaded).__name__}"
            )
        if isinstance(loaded, dict):
            data = _deep_merge(data, loaded)

    data = _deep_merge(data, _env_overrides(environ))
    if overrides:
        data = _deep_merge(data, overrides)

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"configuration invalid: {problems}") from exc


def find_default_config() -> Path | None:
    """Return the default config path if present (used by `doctor`)."""
    return _resolve_config_path(None)
