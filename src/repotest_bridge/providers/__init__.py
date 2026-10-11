"""Model providers (Groq / OpenRouter / OpenAI-compatible, plus an offline mock)."""

from __future__ import annotations

from repotest_bridge.providers.base import (
    Provider,
    ProviderRequest,
    ProviderResponse,
    require_credentials,
)
from repotest_bridge.providers.mock import MockProvider
from repotest_bridge.providers.openai_compat import (
    OpenAICompatProvider,
    build_provider,
    env_summary,
    load_env_file,
)

__all__ = [
    "MockProvider",
    "OpenAICompatProvider",
    "Provider",
    "ProviderRequest",
    "ProviderResponse",
    "build_provider",
    "env_summary",
    "load_env_file",
    "require_credentials",
]
