"""Tests for agent adapter implementations."""

from __future__ import annotations

import pytest

from src.adapters.base import AgentAdapter
from src.adapters.mock import MockAdapter
from src.models import AgentRequest, HealthStatus, LifecycleEvent


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


class TestPricingIsNeverGuessed:
    """G-12: an unpriced model must not be billed at another model's rate.

    The fallback used to be a hardcoded {0.003, 0.015} -- Sonnet's price -- so
    every cost reported for a cheaper model was silently ~3x inflated. Measured
    2026-07-14: $0.0179 reported for a Haiku call costing ~$0.006, because
    3975*0.003 + 401*0.015 is exactly the Sonnet arithmetic. A plausible wrong
    number gets believed; an obviously wrong one gets investigated.
    """

    def test_known_model_is_priced(self):
        from src.adapters.anthropic import _pricing_for

        assert _pricing_for("claude-sonnet-4-20250514") == {
            "input": 0.003,
            "output": 0.015,
        }

    def test_the_model_the_dogfood_used_is_priced(self):
        """claude-haiku-4-5 was absent, which is how the 3x inflation happened."""
        from src.adapters.anthropic import _pricing_for

        assert _pricing_for("claude-haiku-4-5-20251001")["input"] == 0.001

    def test_unknown_model_prices_zero_not_sonnet(self, caplog):
        from src.adapters.anthropic import _pricing_for

        pricing = _pricing_for("claude-something-not-released-yet")
        assert pricing == {"input": 0.0, "output": 0.0}
        # Zero is a deliberate tell, so it must be loud.
        assert "No pricing entry" in caplog.text

    def test_openai_unknown_model_prices_zero(self, caplog):
        from src.adapters.openai import _pricing_for

        assert _pricing_for("gpt-not-a-model") == {"input": 0.0, "output": 0.0}
        assert "No pricing entry" in caplog.text


class TestMockAdapterProtocol:
    """MockAdapter satisfies the AgentAdapter protocol."""

    def test_isinstance_check(self) -> None:
        adapter = MockAdapter()
        assert isinstance(adapter, AgentAdapter)

    def test_provider_name(self) -> None:
        adapter = MockAdapter(provider_name="test-provider")
        assert adapter.provider_name == "test-provider"

    def test_model_name(self) -> None:
        adapter = MockAdapter(model_name="test-model")
        assert adapter.model_name == "test-model"


class TestMockAdapterInvoke:
    """invoke() returns preset responses or raises on failure."""

    def test_returns_preset_response(self) -> None:
        adapter = MockAdapter(preset_responses={"PRD": "Generated PRD content"})
        request = _make_request("PRD")
        response = adapter.invoke(request)
        assert response.content == "Generated PRD content"
        assert response.provider == adapter.provider_name
        assert response.model == adapter.model_name

    def test_returns_default_when_no_preset(self) -> None:
        adapter = MockAdapter()
        request = _make_request("TDD")
        response = adapter.invoke(request)
        assert "TDD" in response.content

    def test_should_fail_raises_runtime_error(self) -> None:
        adapter = MockAdapter(should_fail=True, failure_message="boom")
        request = _make_request()
        with pytest.raises(RuntimeError, match="boom"):
            adapter.invoke(request)


class TestMockAdapterHealth:
    """health() returns configured status."""

    def test_default_ok(self) -> None:
        adapter = MockAdapter()
        assert adapter.health() == HealthStatus.OK

    def test_configured_degraded(self) -> None:
        adapter = MockAdapter(health_status=HealthStatus.DEGRADED)
        assert adapter.health() == HealthStatus.DEGRADED

    def test_configured_down(self) -> None:
        adapter = MockAdapter(health_status=HealthStatus.DOWN)
        assert adapter.health() == HealthStatus.DOWN


class TestMockAdapterCostEstimate:
    """cost_estimate() returns expected value."""

    def test_returns_fixed_value(self) -> None:
        adapter = MockAdapter()
        request = _make_request()
        assert adapter.cost_estimate(request) == 0.001


class TestMockAdapterCallHistory:
    """Call history tracks all invocations."""

    def test_invoke_tracked(self) -> None:
        adapter = MockAdapter()
        request = _make_request()
        adapter.invoke(request)
        assert len(adapter.call_history) == 1
        assert adapter.call_history[0][0] == "invoke"
        assert adapter.call_history[0][1] == (request,)

    def test_health_tracked(self) -> None:
        adapter = MockAdapter()
        adapter.health()
        assert len(adapter.call_history) == 1
        assert adapter.call_history[0] == ("health", ())

    def test_cost_estimate_tracked(self) -> None:
        adapter = MockAdapter()
        request = _make_request()
        adapter.cost_estimate(request)
        assert len(adapter.call_history) == 1
        assert adapter.call_history[0][0] == "cost_estimate"

    def test_multiple_calls_tracked(self) -> None:
        adapter = MockAdapter()
        request = _make_request()
        adapter.invoke(request)
        adapter.health()
        adapter.cost_estimate(request)
        assert len(adapter.call_history) == 3
        methods = [call[0] for call in adapter.call_history]
        assert methods == ["invoke", "health", "cost_estimate"]
