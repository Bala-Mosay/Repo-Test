"""OpenAI-compatible chat-completions client.

Works against Groq, OpenRouter, Together, vLLM, Ollama's OpenAI shim, or
OpenAI itself -- any service exposing ``POST {base}/chat/completions``.
Provider choice is made purely by ``OPENAI_BASE_URL`` + ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from repotest_bridge.errors import ProviderError
from repotest_bridge.providers.base import (
    KEY_ENV_VARS,
    ProviderRequest,
    ProviderResponse,
    discover_credentials,
    require_credentials,
)
from repotest_bridge.providers.mock import MockProvider

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def _is_retryable(exc: BaseException) -> bool:
    transport = (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)
    if isinstance(exc, transport):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return False


@dataclass
class OpenAICompatProvider:
    """Minimal, non-streaming chat-completions client."""

    base_url: str
    api_key: str
    default_model: str
    timeout_seconds: float = 120.0
    max_retries: int = 3
    backoff_seconds: float = 2.0
    name: str = "openai-compatible"
    extra_headers: dict[str, str] = field(default_factory=dict)
    _client: httpx.Client | None = field(default=None, repr=False)

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        timeout_seconds: float = 120.0,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
    ) -> OpenAICompatProvider:
        base, key, model = require_credentials(env)
        return cls(
            base_url=base.rstrip("/"),
            api_key=key,
            default_model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    **self.extra_headers,
                },
                timeout=httpx.Timeout(self.timeout_seconds, connect=15.0),
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- API ---------------------------------------------------------------

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        payload = {
            "model": request.model or self.default_model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        started = time.perf_counter()
        attempts = 0

        attempt_runner = Retrying(
            stop=stop_after_attempt(max(1, self.max_retries)),
            wait=wait_exponential_jitter(initial=self.backoff_seconds, max=30.0),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )

        try:
            for _ in attempt_runner:
                attempts += 1
                response = self._http().post("/chat/completions", json=payload)
                if response.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"{response.status_code}: {response.text[:500]}",
                        request=response.request,
                        response=response,
                    )
                data = response.json()
                break
        except ProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ProviderError(
                    f"authentication rejected by {self.base_url} "
                    f"(HTTP {exc.response.status_code}); check OPENAI_API_KEY"
                ) from exc
            if exc.response.status_code == 404:
                raise ProviderError(
                    f"model {payload['model']!r} not found at {self.base_url} "
                    f"(HTTP 404); check OPENAI_MODEL"
                ) from exc
            raise ProviderError(f"provider request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider transport error: {exc}") from exc

        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape from provider: {data}") from exc

        usage = data.get("usage") or {}
        latency = (time.perf_counter() - started) * 1000
        return ProviderResponse(
            text=text,
            model=data.get("model", payload["model"]),
            provider=self.name,
            latency_ms=latency,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            finish_reason=finish,
            request_id=data.get("id"),
            attempts=attempts,
        )

    def healthcheck(self) -> tuple[bool, str]:
        try:
            response = self._http().get("/models", timeout=httpx.Timeout(15.0))
        except httpx.HTTPError as exc:
            return False, f"cannot reach {self.base_url}: {exc}"
        if response.status_code in {401, 403}:
            return False, f"{self.base_url} rejected credentials (HTTP {response.status_code})"
        if response.status_code >= 400:
            return False, f"{self.base_url} returned HTTP {response.status_code}"
        try:
            names = [m.get("id") for m in response.json().get("data", [])]
        except (ValueError, AttributeError):
            names = []
        available = self.default_model in names if names else None
        detail = f"{self.base_url}: {len(names)} models" if names else f"{self.base_url}: ok"
        if available is False:
            detail += f"; configured model {self.default_model!r} not in list"
        return True, detail


def build_provider(
    provider_name: str,
    *,
    config: object | None = None,
    env: dict[str, str] | None = None,
    mock_fixture: Path | None = None,
) -> MockProvider | OpenAICompatProvider:
    """Factory used by the CLI. ``provider_name`` is ``mock`` or ``openai``.

    ``mock_fixture`` points at a checked-in ``fixtures/mock_response.diff``
    next to a manifest, so the offline smoke test can exercise the whole
    pipeline with a patch known to reproduce the demo issue.
    """
    if provider_name == "mock":
        canned = ""
        if mock_fixture is not None and Path(mock_fixture).is_file():
            canned = Path(mock_fixture).read_text(encoding="utf-8")
        return MockProvider(canned=canned)
    if provider_name in {"openai", "groq", "openrouter", "auto"}:
        gen = getattr(config, "generation", None)
        return OpenAICompatProvider.from_env(
            env,
            timeout_seconds=getattr(gen, "request_timeout_seconds", 120.0),
            max_retries=getattr(gen, "max_retries", 3),
            backoff_seconds=getattr(gen, "retry_backoff_seconds", 2.0),
        )
    raise ProviderError(f"unknown provider {provider_name!r} (expected 'mock' or 'openai')")


def env_summary(env: dict[str, str] | None = None) -> dict[str, str]:
    """Redacted environment view for `doctor`."""
    source = os.environ if env is None else env
    found = discover_credentials(source)
    if found is None:
        return {
            "credential": f"(none set; tried {', '.join(KEY_ENV_VARS)})",
            "OPENAI_BASE_URL": "(unset)",
            "OPENAI_MODEL": "(unset)",
        }
    base, key, model = found
    return {
        "credential": f"set, {len(key)} chars",
        "OPENAI_BASE_URL": base,
        "OPENAI_MODEL": model,
    }


def load_env_file(path: Path | None = None, *, override: bool = False) -> bool:
    """Read a project ``.env`` into the process environment if present.

    Values already present in the environment win unless ``override`` is set,
    so shell-exported credentials always take precedence.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a hard dependency
        return False
    target = path if path is not None else Path.cwd() / ".env"
    if not target.is_file():
        return False
    load_dotenv(target, override=override)
    return True
