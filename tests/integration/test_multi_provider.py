"""Integration test: multi-provider routing with fallback and consensus.

Tests fallback when primary is down, parallel consensus agreement,
and parallel consensus disagreement.
"""

from __future__ import annotations

import pytest

from src.adapters.mock import MockAdapter
from src.models import (
    AgentRequest,
    HealthStatus,
    LifecycleEvent,
    RoutingStrategy,
)
from src.routing import CircuitBreaker, RoutingEngine


def _make_request() -> AgentRequest:
    return AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.POST_GENERATION,
        spec_content="# Spec",
        template_content="# Template",
        prompt_content="Generate artifact.",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-TEST-001"},
    )


class TestFallbackPrimaryDownSecondarySucceeds:
    """Primary adapter health() returns DOWN; routing engine falls back to secondary."""

    def test_fallback_primary_down_secondary_succeeds(self):
        primary = MockAdapter(
            provider_name="primary",
            model_name="primary-model",
            should_fail=True,
            failure_message="Primary is down",
        )
        secondary = MockAdapter(
            provider_name="secondary",
            model_name="secondary-model",
            preset_responses={"SAD": "# SAD from secondary provider"},
        )

        engine = RoutingEngine(circuit_breaker=CircuitBreaker())

        response = engine.route(
            strategy=RoutingStrategy.FALLBACK,
            adapters=[primary, secondary],
            request=_make_request(),
            config={},
        )

        # Secondary should have produced the response
        assert response.provider == "secondary"
        assert response.content == "# SAD from secondary provider"

        # Primary should have been attempted and failed
        primary_calls = [c for c in primary.call_history if c[0] == "invoke"]
        assert len(primary_calls) == 1

        # Secondary should have been invoked once
        secondary_calls = [c for c in secondary.call_history if c[0] == "invoke"]
        assert len(secondary_calls) == 1


class TestParallelConsensusTwoProvidersAgree:
    """Two mock adapters return similar content; consensus achieved."""

    def test_parallel_consensus_two_providers_agree(self):
        # Both return content of similar length (within 20%)
        content_a = "# Architecture\n\n## Components\n\nAPI, Service, Database layer.\n" * 3
        content_b = "# Architecture\n\n## Components\n\nGateway, Core, Storage layer.\n" * 3

        adapter_a = MockAdapter(
            provider_name="provider-a",
            model_name="model-a",
            preset_responses={"SAD": content_a},
        )
        adapter_b = MockAdapter(
            provider_name="provider-b",
            model_name="model-b",
            preset_responses={"SAD": content_b},
        )

        engine = RoutingEngine()

        response = engine.route(
            strategy=RoutingStrategy.PARALLEL_CONSENSUS,
            adapters=[adapter_a, adapter_b],
            request=_make_request(),
            config={"threshold": 0.67},
        )

        # Consensus should succeed — both responses are similar length
        assert response.content in (content_a, content_b)

        # Both adapters should have been invoked
        a_calls = [c for c in adapter_a.call_history if c[0] == "invoke"]
        b_calls = [c for c in adapter_b.call_history if c[0] == "invoke"]
        assert len(a_calls) == 1
        assert len(b_calls) == 1


class TestParallelConsensusDisagreementRaises:
    """Two mock adapters return very different content lengths; ValueError raised."""

    def test_parallel_consensus_disagreement_raises(self):
        # Provider A returns short content, provider B returns very long content
        short_content = "# Minimal"
        long_content = "# Very detailed architecture\n\n" + ("x" * 5000)

        adapter_a = MockAdapter(
            provider_name="provider-a",
            model_name="model-a",
            preset_responses={"SAD": short_content},
        )
        adapter_b = MockAdapter(
            provider_name="provider-b",
            model_name="model-b",
            preset_responses={"SAD": long_content},
        )

        engine = RoutingEngine()

        with pytest.raises(ValueError, match="disagreement"):
            engine.route(
                strategy=RoutingStrategy.PARALLEL_CONSENSUS,
                adapters=[adapter_a, adapter_b],
                request=_make_request(),
                config={"threshold": 0.67},
            )
