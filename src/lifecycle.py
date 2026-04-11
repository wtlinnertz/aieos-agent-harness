"""Lifecycle event binding and dispatch for the AIEOS Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.adapters.base import AgentAdapter
from src.models import AgentRequest, AgentResponse, AgentSpecies, LifecycleEvent, RoutingStrategy


@dataclass
class EventBinding:
    """Maps a lifecycle event + artifact type to one or more adapters."""

    event: LifecycleEvent
    artifact_type: str  # "*" for all types
    adapter_names: list[str] = field(default_factory=list)
    strategy: RoutingStrategy = RoutingStrategy.FALLBACK
    species: AgentSpecies = AgentSpecies.DARK_FACTORY
    config: dict = field(default_factory=dict)


class LifecycleBinder:
    """Resolves lifecycle events to adapter bindings and dispatches invocations."""

    def __init__(
        self,
        bindings: list[EventBinding],
        adapters: dict[str, AgentAdapter],
    ) -> None:
        self._bindings = bindings
        self._adapters = adapters

    def resolve(
        self,
        event: LifecycleEvent,
        artifact_type: str,
    ) -> list[tuple[AgentAdapter, RoutingStrategy, dict]]:
        """Find adapters bound to this event + artifact_type.

        Exact artifact_type match takes priority over wildcard "*".
        Returns list of (adapter, strategy, config) tuples.
        """
        exact: list[tuple[AgentAdapter, RoutingStrategy, dict]] = []
        wildcard: list[tuple[AgentAdapter, RoutingStrategy, dict]] = []

        for binding in self._bindings:
            if binding.event != event:
                continue

            target = exact if binding.artifact_type == artifact_type else (
                wildcard if binding.artifact_type == "*" else None
            )
            if target is None:
                continue

            for name in binding.adapter_names:
                adapter = self._adapters.get(name)
                if adapter is not None:
                    target.append((adapter, binding.strategy, binding.config))

        return exact if exact else wildcard

    def execute(
        self,
        event: LifecycleEvent,
        request: AgentRequest,
    ) -> AgentResponse:
        """Resolve bindings and invoke the first matching adapter.

        Phase 3 will add routing engine integration for multi-adapter
        strategies. For now, simple single-adapter dispatch.

        Raises:
            RuntimeError: When no binding matches the event + artifact_type.
        """
        resolved = self.resolve(event, request.artifact_type)
        if not resolved:
            raise RuntimeError(
                f"No binding found for event={event.value} "
                f"artifact_type={request.artifact_type}"
            )
        adapter, _strategy, _config = resolved[0]
        return adapter.invoke(request)
