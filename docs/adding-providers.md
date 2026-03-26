# Adding a New Provider

This guide walks through implementing a new adapter for the AIEOS Agent Harness.

## Step 1: Create the Adapter File

Create a new file in `src/adapters/`:

```
src/adapters/your_provider.py
```

## Step 2: Implement the AgentAdapter Protocol

Your adapter must implement three methods defined in `src/adapters/base.py`:

- `invoke(request: AgentRequest) -> AgentResponse` -- send a request to the provider and return the response
- `health() -> HealthStatus` -- return the current provider health status
- `cost_estimate(request: AgentRequest) -> float` -- estimate the cost in USD for a given request

Plus two read-only properties:

- `provider_name: str` -- unique identifier for this provider
- `model_name: str` -- model identifier

## Step 3: Minimal Example Adapter

```python
"""Example adapter for the AIEOS Agent Harness."""

from __future__ import annotations

import os
import time

from src.models import AgentRequest, AgentResponse, HealthStatus


class ExampleAdapter:
    """Adapter for the Example AI provider."""

    def __init__(self, model: str = "example-v1", max_tokens: int = 8192) -> None:
        self._model = model
        self._max_tokens = max_tokens
        api_key = os.environ.get("EXAMPLE_API_KEY", "")
        if not api_key:
            raise ValueError("EXAMPLE_API_KEY environment variable not set")
        self._api_key = api_key

    @property
    def provider_name(self) -> str:
        return "example"

    @property
    def model_name(self) -> str:
        return self._model

    def invoke(self, request: AgentRequest) -> AgentResponse:
        start = time.monotonic()
        # Call your provider API here
        content = self._call_api(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        return AgentResponse(
            content=content,
            provider=self.provider_name,
            model=self._model,
            tokens_in=len(request.prompt_content.split()),
            tokens_out=len(content.split()),
            cost_usd=self.cost_estimate(request),
            latency_ms=elapsed_ms,
        )

    def health(self) -> HealthStatus:
        try:
            # Lightweight API ping
            return HealthStatus.OK
        except Exception:
            return HealthStatus.DOWN

    def cost_estimate(self, request: AgentRequest) -> float:
        # Estimate based on prompt length
        tokens = len(request.prompt_content.split())
        return tokens * 0.00001

    def _call_api(self, request: AgentRequest) -> str:
        # Replace with actual API call
        raise NotImplementedError("Wire up your provider API here")
```

## Step 4: Add Provider Config to harness.yaml

Add a section under `providers`:

```yaml
providers:
  example:
    enabled: true
    model: example-v1
    max_tokens: 8192
```

Set the API key as an environment variable:

```bash
export EXAMPLE_API_KEY="your-key-here"
```

## Step 5: Test with a Mock Request

Write a quick integration test to verify your adapter works:

```python
import os
import pytest
from src.adapters.your_provider import ExampleAdapter
from src.models import AgentRequest, LifecycleEvent

@pytest.mark.slow
def test_example_adapter_invoke():
    if not os.environ.get("EXAMPLE_API_KEY"):
        pytest.skip("EXAMPLE_API_KEY not set")

    adapter = ExampleAdapter(model="example-v1")
    request = AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.POST_GENERATION,
        spec_content="# Spec",
        template_content="# Template",
        prompt_content="Generate a brief architecture summary.",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-TEST-001"},
    )
    response = adapter.invoke(request)
    assert response.provider == "example"
    assert len(response.content) > 0
```

Mark the test with `@pytest.mark.slow` so it only runs when `--run-slow` is passed.

## Step 6: Checklist

Before merging your adapter, verify:

- [ ] **Idempotency**: the same request produces deterministic behavior (set temperature to 0 or use seed if available)
- [ ] **Auth externalized**: API keys come from environment variables, never from config files or source code
- [ ] **Error handling**: network errors, rate limits, and malformed responses raise clear exceptions (not silent failures)
- [ ] **Audit logging**: the `invoke()` method returns accurate `tokens_in`, `tokens_out`, `cost_usd`, and `latency_ms` so the Observability Layer can record them
- [ ] **Health check**: `health()` returns `HealthStatus.DOWN` when the provider is unreachable, `DEGRADED` when partially available
- [ ] **Cost estimate**: `cost_estimate()` returns a reasonable USD estimate so cost-aware routing can compare providers
- [ ] **Protocol compliance**: the adapter passes `isinstance(adapter, AgentAdapter)` (the Protocol is `runtime_checkable`)
