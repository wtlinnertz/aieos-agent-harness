"""Tests for the routing engine and circuit breaker."""

from __future__ import annotations

import pytest

from src.adapters.mock import MockAdapter
from src.models import AgentRequest, AgentResponse, LifecycleEvent, RoutingStrategy
from src.routing import CircuitBreaker, RoutingEngine


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


# ---------------------------------------------------------------------------
# Fallback strategy
# ---------------------------------------------------------------------------


class TestFallbackStrategy:
    """Tests for the FALLBACK routing strategy."""

    def test_fallback_primary_succeeds(self) -> None:
        """First adapter works, second not called."""
        primary = MockAdapter(
            provider_name="primary",
            preset_responses={"PRD": "primary result"},
        )
        secondary = MockAdapter(
            provider_name="secondary",
            preset_responses={"PRD": "secondary result"},
        )
        engine = RoutingEngine()
        request = _make_request()

        response = engine.route(
            RoutingStrategy.FALLBACK, [primary, secondary], request, {}
        )

        assert response.content == "primary result"
        assert response.provider == "primary"
        assert len(primary.call_history) == 1
        assert len(secondary.call_history) == 0

    def test_fallback_primary_fails(self) -> None:
        """First adapter raises, second invoked and succeeds."""
        primary = MockAdapter(
            provider_name="primary",
            should_fail=True,
            failure_message="primary down",
        )
        secondary = MockAdapter(
            provider_name="secondary",
            preset_responses={"PRD": "secondary result"},
        )
        engine = RoutingEngine()
        request = _make_request()

        response = engine.route(
            RoutingStrategy.FALLBACK, [primary, secondary], request, {}
        )

        assert response.content == "secondary result"
        assert response.provider == "secondary"

    def test_fallback_all_fail(self) -> None:
        """All adapters fail, RuntimeError raised."""
        a1 = MockAdapter(provider_name="a1", should_fail=True)
        a2 = MockAdapter(provider_name="a2", should_fail=True)
        engine = RoutingEngine()
        request = _make_request()

        with pytest.raises(RuntimeError, match="All adapters failed"):
            engine.route(RoutingStrategy.FALLBACK, [a1, a2], request, {})

    def test_fallback_circuit_breaker(self) -> None:
        """After N failures, adapter is skipped via circuit breaker."""
        cb = CircuitBreaker(max_failures=2, reset_seconds=300.0)
        failing = MockAdapter(
            provider_name="failing", should_fail=True
        )
        backup = MockAdapter(
            provider_name="backup",
            preset_responses={"PRD": "backup result"},
        )
        engine = RoutingEngine(circuit_breaker=cb)
        request = _make_request()

        # First two calls: failing adapter tries and fails, backup succeeds
        engine.route(RoutingStrategy.FALLBACK, [failing, backup], request, {})
        engine.route(RoutingStrategy.FALLBACK, [failing, backup], request, {})

        # Circuit breaker should now be open for "failing" (2 failures = max)
        assert cb.is_open("failing")

        # Third call: failing adapter skipped entirely, backup invoked directly
        failing.call_history.clear()
        response = engine.route(
            RoutingStrategy.FALLBACK, [failing, backup], request, {}
        )

        assert response.content == "backup result"
        assert len(failing.call_history) == 0  # Skipped, not invoked


# ---------------------------------------------------------------------------
# Parallel consensus strategy
# ---------------------------------------------------------------------------


class TestParallelConsensusStrategy:
    """Tests for the PARALLEL_CONSENSUS routing strategy."""

    def test_parallel_consensus_agreement(self) -> None:
        """All adapters agree, returns result."""
        # Same-length responses so they agree within 20%
        a1 = MockAdapter(
            provider_name="a1",
            preset_responses={"PRD": "Generated PRD content here"},
        )
        a2 = MockAdapter(
            provider_name="a2",
            preset_responses={"PRD": "Generated PRD content done"},
        )
        a3 = MockAdapter(
            provider_name="a3",
            preset_responses={"PRD": "Generated PRD content good"},
        )
        engine = RoutingEngine()
        request = _make_request()

        response = engine.route(
            RoutingStrategy.PARALLEL_CONSENSUS,
            [a1, a2, a3],
            request,
            {"threshold": 0.67},
        )

        assert response.content in [
            "Generated PRD content here",
            "Generated PRD content done",
            "Generated PRD content good",
        ]

    def test_parallel_consensus_disagreement(self) -> None:
        """Below threshold, raises ValueError."""
        # Very different content lengths to force disagreement
        a1 = MockAdapter(
            provider_name="a1",
            preset_responses={"PRD": "short"},
        )
        a2 = MockAdapter(
            provider_name="a2",
            preset_responses={"PRD": "x" * 1000},
        )
        a3 = MockAdapter(
            provider_name="a3",
            preset_responses={"PRD": "y" * 500},
        )
        engine = RoutingEngine()
        request = _make_request()

        with pytest.raises(ValueError, match="disagreement"):
            engine.route(
                RoutingStrategy.PARALLEL_CONSENSUS,
                [a1, a2, a3],
                request,
                {"threshold": 0.67},
            )


# ---------------------------------------------------------------------------
# Pipeline strategy
# ---------------------------------------------------------------------------


class TestPipelineStrategy:
    """Tests for the PIPELINE routing strategy."""

    def test_pipeline_chains_output(self) -> None:
        """adapter[0] output becomes adapter[1] input's current_artifact."""
        step1 = MockAdapter(
            provider_name="step1",
            preset_responses={"PRD": "step1 output"},
        )
        step2 = MockAdapter(
            provider_name="step2",
            preset_responses={"PRD": "step2 output"},
        )
        engine = RoutingEngine()
        request = _make_request()

        response = engine.route(
            RoutingStrategy.PIPELINE, [step1, step2], request, {}
        )

        assert response.content == "step2 output"
        assert response.provider == "step2"

        # Verify step2 received step1's output as current_artifact
        step2_call = step2.call_history[0]
        step2_request: AgentRequest = step2_call[1][0]
        assert step2_request.current_artifact == "step1 output"

    def test_pipeline_step_fails(self) -> None:
        """Middle step fails, raises RuntimeError."""
        step1 = MockAdapter(
            provider_name="step1",
            preset_responses={"PRD": "step1 output"},
        )
        step2 = MockAdapter(
            provider_name="step2",
            should_fail=True,
            failure_message="step2 exploded",
        )
        step3 = MockAdapter(
            provider_name="step3",
            preset_responses={"PRD": "step3 output"},
        )
        engine = RoutingEngine()
        request = _make_request()

        with pytest.raises(RuntimeError, match="Pipeline step 1.*step2.*failed"):
            engine.route(
                RoutingStrategy.PIPELINE, [step1, step2, step3], request, {}
            )

        # step3 should never have been called
        assert len(step3.call_history) == 0


# ---------------------------------------------------------------------------
# Cost-aware strategy
# ---------------------------------------------------------------------------


class TestCostAwareStrategy:
    """Tests for the COST_AWARE routing strategy."""

    def test_cost_aware_cheapest_first(self) -> None:
        """Cheapest adapter invoked first."""
        cheap = MockAdapter(
            provider_name="cheap",
            preset_responses={"PRD": "cheap result"},
        )
        expensive = MockAdapter(
            provider_name="expensive",
            preset_responses={"PRD": "expensive result"},
        )
        # Override cost_estimate via monkey-patching for test
        cheap.cost_estimate = lambda req: 0.001  # type: ignore[assignment]
        expensive.cost_estimate = lambda req: 0.1  # type: ignore[assignment]

        engine = RoutingEngine()
        request = _make_request()

        response = engine.route(
            RoutingStrategy.COST_AWARE,
            [expensive, cheap],  # expensive listed first
            request,
            {},
        )

        assert response.content == "cheap result"
        assert response.provider == "cheap"

    def test_cost_aware_cheapest_fails_fallback(self) -> None:
        """Cheapest fails, next cheapest invoked."""
        cheap = MockAdapter(
            provider_name="cheap",
            should_fail=True,
        )
        mid = MockAdapter(
            provider_name="mid",
            preset_responses={"PRD": "mid result"},
        )
        expensive = MockAdapter(
            provider_name="expensive",
            preset_responses={"PRD": "expensive result"},
        )
        cheap.cost_estimate = lambda req: 0.001  # type: ignore[assignment]
        mid.cost_estimate = lambda req: 0.01  # type: ignore[assignment]
        expensive.cost_estimate = lambda req: 0.1  # type: ignore[assignment]

        engine = RoutingEngine()
        request = _make_request()

        response = engine.route(
            RoutingStrategy.COST_AWARE,
            [expensive, cheap, mid],  # unordered
            request,
            {},
        )

        assert response.content == "mid result"
        assert response.provider == "mid"


# ---------------------------------------------------------------------------
# Edge cases: half-open breaker, unknown strategy, all-fail, consensus counting,
# pipeline / cost-aware guards
# ---------------------------------------------------------------------------


def _resp(content: str) -> AgentResponse:
    return AgentResponse(
        content=content, provider="p", model="m",
        tokens_in=1, tokens_out=1, cost_usd=0.0, latency_ms=1.0,
    )


class TestCircuitBreakerHalfOpen:
    def test_half_opens_after_reset_window(self) -> None:
        """reset_seconds=0 -> breaker half-opens on the next check and clears state."""
        cb = CircuitBreaker(max_failures=1, reset_seconds=0.0)
        cb.record_failure("p")
        assert cb.is_open("p") is False

    def test_stays_open_before_reset(self) -> None:
        cb = CircuitBreaker(max_failures=1, reset_seconds=60.0)
        cb.record_failure("p")
        assert cb.is_open("p") is True


class TestRouteDispatchErrors:
    def test_unknown_strategy_raises(self) -> None:
        engine = RoutingEngine()
        with pytest.raises(ValueError, match="Unknown routing strategy"):
            engine.route("not-a-strategy", [MockAdapter()], _make_request(), {})


class TestParallelConsensusAllFail:
    def test_all_adapters_fail_raises_runtime(self) -> None:
        engine = RoutingEngine()
        adapters = [
            MockAdapter(provider_name="a", should_fail=True),
            MockAdapter(provider_name="b", should_fail=True),
        ]
        with pytest.raises(RuntimeError, match="parallel consensus"):
            engine.route(RoutingStrategy.PARALLEL_CONSENSUS, adapters, _make_request(), {})


class TestCountAgreeing:
    def test_empty_returns_zero(self) -> None:
        assert RoutingEngine._count_agreeing([]) == 0

    def test_single_returns_one(self) -> None:
        assert RoutingEngine._count_agreeing([_resp("x")]) == 1

    def test_all_empty_content_counts_zero_length(self) -> None:
        assert RoutingEngine._count_agreeing([_resp(""), _resp("")]) == 2


class TestPipelineGuard:
    def test_pipeline_requires_at_least_one_adapter(self) -> None:
        engine = RoutingEngine()
        with pytest.raises(ValueError, match="at least one adapter"):
            engine.route(RoutingStrategy.PIPELINE, [], _make_request(), {})


class TestCostAwareEdge:
    def test_min_tier_filters_all_then_raises(self) -> None:
        # MockAdapter.cost_estimate is 0.001; min_tier above it filters everything out.
        engine = RoutingEngine()
        with pytest.raises(RuntimeError, match="cost-aware"):
            engine.route(
                RoutingStrategy.COST_AWARE, [MockAdapter()], _make_request(), {"min_tier": 1.0}
            )

    def test_open_breaker_skips_adapter(self) -> None:
        cb = CircuitBreaker(max_failures=1, reset_seconds=60.0)
        cb.record_failure("only")
        engine = RoutingEngine(circuit_breaker=cb)
        with pytest.raises(RuntimeError, match="circuit breaker open"):
            engine.route(
                RoutingStrategy.COST_AWARE, [MockAdapter(provider_name="only")], _make_request(), {}
            )

    def test_all_adapters_fail_raises(self) -> None:
        engine = RoutingEngine()
        with pytest.raises(RuntimeError, match="cost-aware"):
            engine.route(
                RoutingStrategy.COST_AWARE,
                [MockAdapter(provider_name="a", should_fail=True)],
                _make_request(), {},
            )
