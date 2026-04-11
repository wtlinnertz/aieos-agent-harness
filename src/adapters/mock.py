"""Mock adapter for testing the AIEOS Agent Harness."""

from __future__ import annotations

import hashlib
from typing import Any

from src.models import AgentRequest, AgentResponse, HealthStatus


class MockAdapter:
    """Test double implementing the AgentAdapter protocol."""

    def __init__(
        self,
        provider_name: str = "mock-provider",
        model_name: str = "mock-model-v1",
        preset_responses: dict[str, str] | None = None,
        health_status: HealthStatus = HealthStatus.OK,
        should_fail: bool = False,
        failure_message: str = "Mock adapter configured to fail",
    ) -> None:
        self._provider_name = provider_name
        self._model_name = model_name
        self._preset_responses = preset_responses or {}
        self._health_status = health_status
        self._should_fail = should_fail
        self._failure_message = failure_message
        self.call_history: list[tuple[str, tuple[Any, ...]]] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def invoke(self, request: AgentRequest) -> AgentResponse:
        self.call_history.append(("invoke", (request,)))
        if self._should_fail:
            raise RuntimeError(self._failure_message)
        content = self._preset_responses.get(
            request.artifact_type,
            f"Mock response for {request.artifact_type}",
        )
        hash_input = (
            request.spec_content
            + request.template_content
            + request.prompt_content
            + "".join(request.upstream_artifacts.values())
        )
        input_hash = hashlib.sha256(hash_input.encode()).hexdigest()
        return AgentResponse(
            content=content,
            provider=self._provider_name,
            model=self._model_name,
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.001,
            latency_ms=50.0,
            human_author=request.metadata.get("human_author"),
            input_content_hash=input_hash,
        )

    def health(self) -> HealthStatus:
        self.call_history.append(("health", ()))
        return self._health_status

    def cost_estimate(self, request: AgentRequest) -> float:
        self.call_history.append(("cost_estimate", (request,)))
        return 0.001
