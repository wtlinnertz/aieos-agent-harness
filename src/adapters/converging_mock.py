"""A mock provider that CONVERGES -- for offline/CI end-to-end runs (Phase 5).

Unlike ``MockAdapter`` (a generic test double), this adapter branches on the
lifecycle event: on a validation event it returns a well-formed PASS validation
JSON, so the convergence loop reaches CONVERGED on the first iteration; on a
generation event it returns plausible artifact content. This lets the dark
factory drive a real harness end-to-end (the Phase 5 three-way switch proof)
with no API keys and no budget.

Enabled via ``providers: {mock: {enabled: true}}`` in harness.yaml.
"""

from __future__ import annotations

import json

from src.models import AgentRequest, AgentResponse, HealthStatus, LifecycleEvent

_VALIDATION_EVENTS = {LifecycleEvent.PRE_VALIDATION, LifecycleEvent.POST_VALIDATION}

_PASS_VALIDATION = json.dumps(
    {
        "status": "PASS",
        "summary": "All hard gates satisfied.",
        "hard_gates": {"completeness": "PASS", "structure": "PASS"},
        "blocking_issues": [],
        "warnings": [],
        "completeness_score": 92,
    }
)


class ConvergingMockAdapter:
    """AgentAdapter that generates plausible content and always validates PASS."""

    def __init__(
        self,
        provider_name: str = "mock",
        model_name: str = "converging-mock-v1",
    ) -> None:
        self._provider_name = provider_name
        self._model_name = model_name

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def invoke(self, request: AgentRequest) -> AgentResponse:
        if request.event in _VALIDATION_EVENTS:
            content = _PASS_VALIDATION
        else:
            content = (
                f"# {request.artifact_type}\n\n"
                f"Mock-generated content for {request.artifact_type}.\n"
            )
        return AgentResponse(
            content=content,
            provider=self._provider_name,
            model=self._model_name,
            tokens_in=10,
            tokens_out=20,
            cost_usd=0.0,
            latency_ms=1.0,
            human_author=request.metadata.get("human_author"),
        )

    def health(self) -> HealthStatus:
        return HealthStatus.OK

    def cost_estimate(self, request: AgentRequest) -> float:
        return 0.0
