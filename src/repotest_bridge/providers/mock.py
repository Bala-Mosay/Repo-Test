"""Offline, deterministic provider.

Used by the offline demo and by the test suite. It produces a syntactically
valid response *without* touching the network, and never fabricates results
for a real evaluation -- it is a plumbing check, not a measurement.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from repotest_bridge.errors import ProviderError
from repotest_bridge.providers.base import ProviderRequest, ProviderResponse

DEFAULT_FIXTURE_PATCH = '''```diff
--- a/tests/test_pricing.py
+++ b/tests/test_pricing.py
@@ -1,6 +1,16 @@
+"""Regression test for the boundary condition in the issue."""
+import pytest
+
+from pricinglib.pricing import price_with_discount
+
+
+def test_discount_applies_at_exact_threshold():
+    """The issue: a 25% discount must apply *at* the threshold, not above it."""
+    assert price_with_discount(100.0, 0.25, 25.0) == 75.0
 ```
'''


@dataclass
class MockProvider:
    """Canned responses, keyed by whether the prompt looks like generation."""

    name: str = "mock"
    canned: str = DEFAULT_FIXTURE_PATCH
    latency_ms: float = 1.0
    fail_first_n: int = 0
    _calls: int = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self._calls += 1
        if self._calls <= self.fail_first_n:
            raise ProviderError(f"mock failure on call {self._calls} (induced)")
        text = self._render(request)
        return ProviderResponse(
            text=text,
            model=request.model or "mock-model",
            provider=self.name,
            latency_ms=self.latency_ms,
            input_tokens=max(1, len(request.prompt_text) // 4),
            output_tokens=max(1, len(text) // 4),
            finish_reason="stop",
            request_id=f"mock-{self._calls:04d}",
            attempts=1,
        )

    def _render(self, request: ProviderRequest) -> str:
        """Return a different shape when the prompt asks for something else."""
        joined = request.prompt_text.lower()
        if "return only json" in joined or '"patch"' in joined:
            import json

            return json.dumps({"patch": self.canned, "rationale": "mock response"})
        if self.canned:
            return self.canned
        time.sleep(0)
        return "```diff\n# mock provider produced no fixture\n```"

    def healthcheck(self) -> tuple[bool, str]:
        return True, "mock provider: offline, deterministic, no credentials required"
