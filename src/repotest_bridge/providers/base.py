"""Provider abstraction.

Provider choice is configuration-only (docs/architecture.md invariant 6):
swapping Groq for OpenRouter or a local mock never touches retrieval,
prompting, or evaluation code.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from repotest_bridge.errors import ProviderError


@dataclass
class ProviderRequest:
    """A single chat completion request."""

    messages: list[dict[str, str]]
    temperature: float = 0.0
    max_tokens: int = 2048
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def prompt_text(self) -> str:
        return "\n".join(m.get("content", "") for m in self.messages)


@dataclass
class ProviderResponse:
    """A single completion, with enough provenance for a reproducible run."""

    text: str
    model: str
    provider: str
    latency_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    request_id: str | None = None
    attempts: int = 1

    @property
    def usage(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": round(self.latency_ms, 2),
            "finish_reason": self.finish_reason,
            "request_id": self.request_id,
            "attempts": self.attempts,
        }


@runtime_checkable
class Provider(Protocol):
    """Minimal interface every backend implements."""

    name: str

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Run one completion, raising :class:`ProviderError` on failure."""
        ...

    def healthcheck(self) -> tuple[bool, str]:
        """Return ``(ok, detail)``; must be safe to call before any spend."""
        ...


# Credential variable names, in the order they are consulted. `.env` may use any
# of these; `OPENAI_API_KEY` wins when several are set so an explicit override
# always beats a discovered provider.
KEY_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OPEN_ROUTER_API_KEY",
    "GROQ_API_KEY",
    "QROQ_API_KEY",
)

DEFAULT_ENDPOINTS: dict[str, tuple[str, str]] = {
    # key-var -> (base url, default model)
    "OPENAI_API_KEY": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "OPENROUTER_API_KEY": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    "OPEN_ROUTER_API_KEY": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    "GROQ_API_KEY": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "QROQ_API_KEY": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
}


def discover_credentials(
    source: Mapping[str, str],
) -> tuple[str, str, str] | None:
    """Find ``(base_url, api_key, model)`` from whichever credential is set."""
    for var in KEY_ENV_VARS:
        key = (source.get(var) or "").strip()
        if not key:
            continue
        default_base, default_model = DEFAULT_ENDPOINTS[var]
        base = (source.get("OPENAI_BASE_URL") or "").strip() or default_base
        model = (source.get("OPENAI_MODEL") or "").strip() or default_model
        return base, key, model
    return None


def require_credentials(env: dict[str, str] | None = None) -> tuple[str, str, str]:
    """Resolve ``(base_url, api_key, model)`` from the environment."""
    source = os.environ if env is None else env
    found = discover_credentials(source)
    if found is None:
        raise ProviderError(
            "no API key found. Set one of "
            + ", ".join(KEY_ENV_VARS)
            + " (a `.env` file in the project root is read automatically), or use "
            "`--provider mock` for the offline smoke test."
        )
    return found
