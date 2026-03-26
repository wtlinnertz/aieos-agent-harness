"""Base adapter protocol for the AIEOS Agent Harness."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.models import AgentRequest, AgentResponse, HealthStatus


@runtime_checkable
class AgentAdapter(Protocol):
    """Interface contract for all agent adapters."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def invoke(self, request: AgentRequest) -> AgentResponse: ...

    def health(self) -> HealthStatus: ...

    def cost_estimate(self, request: AgentRequest) -> float: ...
