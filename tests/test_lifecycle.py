"""Tests for lifecycle event binding and dispatch."""

from __future__ import annotations

import pytest

from src.adapters.mock import MockAdapter
from src.lifecycle import EventBinding, LifecycleBinder
from src.models import AgentRequest, LifecycleEvent, RoutingStrategy


def _make_request(artifact_type: str = "PRD") -> AgentRequest:
    """Create a minimal AgentRequest for testing."""
    return AgentRequest(
        artifact_type=artifact_type,
        event=LifecycleEvent.PRE_GENERATION,
        spec_content="spec",
        template_content="template",
        prompt_content="prompt",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={},
    )


def _make_adapter(name: str = "adapter-a") -> MockAdapter:
    return MockAdapter(provider_name=name, model_name=f"{name}-model")


class TestBindingResolution:
    """resolve() finds adapters bound to event + artifact_type."""

    def test_exact_match_returns_adapters(self) -> None:
        adapter = _make_adapter("a")
        binding = EventBinding(
            event=LifecycleEvent.PRE_GENERATION,
            artifact_type="PRD",
            adapter_names=["a"],
            strategy=RoutingStrategy.FALLBACK,
        )
        binder = LifecycleBinder([binding], {"a": adapter})
        result = binder.resolve(LifecycleEvent.PRE_GENERATION, "PRD")
        assert len(result) == 1
        assert result[0][0] is adapter
        assert result[0][1] == RoutingStrategy.FALLBACK

    def test_wildcard_matches_any_type(self) -> None:
        adapter = _make_adapter("a")
        binding = EventBinding(
            event=LifecycleEvent.PRE_GENERATION,
            artifact_type="*",
            adapter_names=["a"],
            strategy=RoutingStrategy.PIPELINE,
        )
        binder = LifecycleBinder([binding], {"a": adapter})
        result = binder.resolve(LifecycleEvent.PRE_GENERATION, "TDD")
        assert len(result) == 1
        assert result[0][0] is adapter

    def test_exact_match_takes_priority_over_wildcard(self) -> None:
        exact_adapter = _make_adapter("exact")
        wildcard_adapter = _make_adapter("wildcard")
        bindings = [
            EventBinding(
                event=LifecycleEvent.PRE_GENERATION,
                artifact_type="*",
                adapter_names=["wildcard"],
                strategy=RoutingStrategy.FALLBACK,
            ),
            EventBinding(
                event=LifecycleEvent.PRE_GENERATION,
                artifact_type="PRD",
                adapter_names=["exact"],
                strategy=RoutingStrategy.PIPELINE,
            ),
        ]
        binder = LifecycleBinder(
            bindings, {"exact": exact_adapter, "wildcard": wildcard_adapter}
        )
        result = binder.resolve(LifecycleEvent.PRE_GENERATION, "PRD")
        assert len(result) == 1
        assert result[0][0] is exact_adapter

    def test_no_match_returns_empty_list(self) -> None:
        adapter = _make_adapter("a")
        binding = EventBinding(
            event=LifecycleEvent.PRE_GENERATION,
            artifact_type="PRD",
            adapter_names=["a"],
            strategy=RoutingStrategy.FALLBACK,
        )
        binder = LifecycleBinder([binding], {"a": adapter})
        # Different event
        result = binder.resolve(LifecycleEvent.POST_VALIDATION, "PRD")
        assert result == []

    def test_no_match_wrong_artifact_type(self) -> None:
        adapter = _make_adapter("a")
        binding = EventBinding(
            event=LifecycleEvent.PRE_GENERATION,
            artifact_type="PRD",
            adapter_names=["a"],
            strategy=RoutingStrategy.FALLBACK,
        )
        binder = LifecycleBinder([binding], {"a": adapter})
        result = binder.resolve(LifecycleEvent.PRE_GENERATION, "TDD")
        assert result == []


class TestLifecycleExecute:
    """execute() dispatches to the correct adapter based on binding."""

    def test_dispatches_to_correct_adapter(self) -> None:
        adapter = _make_adapter("a")
        adapter._preset_responses["PRD"] = "PRD output"
        binding = EventBinding(
            event=LifecycleEvent.PRE_GENERATION,
            artifact_type="PRD",
            adapter_names=["a"],
            strategy=RoutingStrategy.FALLBACK,
        )
        binder = LifecycleBinder([binding], {"a": adapter})
        request = _make_request("PRD")
        response = binder.execute(LifecycleEvent.PRE_GENERATION, request)
        assert response.content == "PRD output"
        assert response.provider == "a"

    def test_raises_when_no_binding_matches(self) -> None:
        binder = LifecycleBinder([], {})
        request = _make_request("PRD")
        with pytest.raises(RuntimeError, match="No binding found"):
            binder.execute(LifecycleEvent.PRE_GENERATION, request)

    def test_wildcard_binding_dispatches(self) -> None:
        adapter = _make_adapter("catch-all")
        binding = EventBinding(
            event=LifecycleEvent.POST_GENERATION,
            artifact_type="*",
            adapter_names=["catch-all"],
            strategy=RoutingStrategy.FALLBACK,
        )
        binder = LifecycleBinder([binding], {"catch-all": adapter})
        request = _make_request("SAD")
        response = binder.execute(LifecycleEvent.POST_GENERATION, request)
        assert response.provider == "catch-all"
        assert "SAD" in response.content

    def test_exact_binding_preferred_during_execute(self) -> None:
        exact = _make_adapter("exact")
        exact._preset_responses["PRD"] = "exact PRD"
        wild = _make_adapter("wild")
        wild._preset_responses["PRD"] = "wild PRD"
        bindings = [
            EventBinding(
                event=LifecycleEvent.PRE_GENERATION,
                artifact_type="*",
                adapter_names=["wild"],
                strategy=RoutingStrategy.FALLBACK,
            ),
            EventBinding(
                event=LifecycleEvent.PRE_GENERATION,
                artifact_type="PRD",
                adapter_names=["exact"],
                strategy=RoutingStrategy.PIPELINE,
            ),
        ]
        binder = LifecycleBinder(bindings, {"exact": exact, "wild": wild})
        request = _make_request("PRD")
        response = binder.execute(LifecycleEvent.PRE_GENERATION, request)
        assert response.content == "exact PRD"
