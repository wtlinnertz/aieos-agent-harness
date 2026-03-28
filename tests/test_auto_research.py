"""Tests for the auto-research optimization engine."""

from __future__ import annotations

import json
import pytest

from src.adapters.mock import MockAdapter
from src.auto_research import AutoResearchEngine, Experiment, ResearchResult
from src.models import AgentRequest, AgentResponse, LifecycleEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SequentialMockAdapter:
    """Mock adapter that returns a sequence of preset responses in order.

    Each call to invoke() returns the next response in the sequence.
    After exhausting the sequence, cycles back to the last response.
    """

    def __init__(self, responses: list[AgentResponse]) -> None:
        self._responses = responses
        self._call_index = 0
        self.call_history: list[AgentRequest] = []

    @property
    def provider_name(self) -> str:
        return "sequential-mock"

    @property
    def model_name(self) -> str:
        return "sequential-mock-v1"

    def invoke(self, request: AgentRequest) -> AgentResponse:
        self.call_history.append(request)
        idx = min(self._call_index, len(self._responses) - 1)
        self._call_index += 1
        return self._responses[idx]

    def health(self):
        from src.models import HealthStatus
        return HealthStatus.OK

    def cost_estimate(self, request: AgentRequest) -> float:
        return 0.001


def _make_base_request(**overrides) -> AgentRequest:
    """Create a minimal AgentRequest for testing."""
    defaults = dict(
        artifact_type="SAD",
        event=LifecycleEvent.PRE_GENERATION,
        spec_content="spec content here",
        template_content="template content here",
        prompt_content="Generate a system architecture document.",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"initiative": "test"},
    )
    defaults.update(overrides)
    return AgentRequest(**defaults)


def _make_response(content: str = "mock output", **overrides) -> AgentResponse:
    """Create a minimal AgentResponse for testing."""
    defaults = dict(
        content=content,
        provider="mock",
        model="mock-v1",
        tokens_in=100,
        tokens_out=200,
        cost_usd=0.001,
        latency_ms=50.0,
    )
    defaults.update(overrides)
    return AgentResponse(**defaults)


def _validation_json(status: str = "PASS", score: int = 85) -> str:
    """Build a validation JSON response body."""
    return json.dumps({
        "status": status,
        "summary": f"Score {score}",
        "hard_gates": {"gate_1": status},
        "blocking_issues": [] if status == "PASS" else [{"gate": "gate_1", "description": "issue"}],
        "warnings": [],
        "completeness_score": score,
    })


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------


class TestParseTarget:
    def test_parse_target_gte(self):
        op, val = AutoResearchEngine._parse_target(">= 85")
        assert op == ">="
        assert val == 85.0

    def test_parse_target_lte(self):
        op, val = AutoResearchEngine._parse_target("<= 0.05")
        assert op == "<="
        assert val == 0.05

    def test_parse_target_eq(self):
        op, val = AutoResearchEngine._parse_target("== 100")
        assert op == "=="
        assert val == 100.0

    def test_parse_target_invalid(self):
        with pytest.raises(ValueError, match="Cannot parse target"):
            AutoResearchEngine._parse_target("about 50")


# ---------------------------------------------------------------------------
# Target met / is_better
# ---------------------------------------------------------------------------


class TestTargetMet:
    def test_target_met_true(self):
        adapter = MockAdapter()
        engine = AutoResearchEngine(adapter, "completeness_score", ">= 85")
        assert engine._target_met(90.0) is True

    def test_target_met_false(self):
        adapter = MockAdapter()
        engine = AutoResearchEngine(adapter, "completeness_score", ">= 85")
        assert engine._target_met(70.0) is False

    def test_target_met_lte(self):
        adapter = MockAdapter()
        engine = AutoResearchEngine(adapter, "cost_usd", "<= 0.05")
        assert engine._target_met(0.03) is True
        assert engine._target_met(0.10) is False

    def test_target_met_eq(self):
        adapter = MockAdapter()
        engine = AutoResearchEngine(adapter, "cost_usd", "== 100")
        assert engine._target_met(100.0) is True
        assert engine._target_met(100.01) is False


# ---------------------------------------------------------------------------
# Metric measurement
# ---------------------------------------------------------------------------


class TestMeasureMetric:
    def test_measure_cost(self):
        response = _make_response(cost_usd=0.05)
        assert AutoResearchEngine._measure_metric(response, "cost_usd") == 0.05

    def test_measure_token_efficiency(self):
        response = _make_response(tokens_in=100, tokens_out=200)
        assert AutoResearchEngine._measure_metric(response, "token_efficiency") == 2.0

    def test_measure_latency(self):
        response = _make_response(latency_ms=123.4)
        assert AutoResearchEngine._measure_metric(response, "latency_ms") == 123.4

    def test_measure_completeness_score(self):
        content = _validation_json("PASS", score=92)
        response = _make_response(content=content)
        assert AutoResearchEngine._measure_metric(response, "completeness_score") == 92.0

    def test_measure_first_pass_rate_pass(self):
        content = _validation_json("PASS", score=90)
        response = _make_response(content=content)
        assert AutoResearchEngine._measure_metric(response, "first_pass_rate") == 1.0

    def test_measure_first_pass_rate_fail(self):
        content = _validation_json("FAIL", score=40)
        response = _make_response(content=content)
        assert AutoResearchEngine._measure_metric(response, "first_pass_rate") == 0.0

    def test_measure_unknown_metric(self):
        response = _make_response()
        with pytest.raises(ValueError, match="Unknown metric"):
            AutoResearchEngine._measure_metric(response, "nonexistent")


# ---------------------------------------------------------------------------
# Optimization loop
# ---------------------------------------------------------------------------


class TestOptimize:
    def test_optimize_meets_target(self):
        """Engine stops when a variation meets the target metric."""
        # Sequential responses with increasing completeness scores
        responses = [
            _make_response(content=_validation_json("FAIL", 60), cost_usd=0.01),
            _make_response(content=_validation_json("FAIL", 70), cost_usd=0.01),
            _make_response(content=_validation_json("FAIL", 80), cost_usd=0.01),
            _make_response(content=_validation_json("PASS", 90), cost_usd=0.01),
        ]
        exec_adapter = SequentialMockAdapter(responses)

        # Variation adapter returns formatted variations
        var_lines = "\n".join(
            f"VARIATION {i}: Alternative prompt version {i}"
            for i in range(1, 6)
        )
        var_adapter = MockAdapter(preset_responses={"meta-variation": var_lines})

        engine = AutoResearchEngine(
            adapter=exec_adapter,
            metric_name="completeness_score",
            target=">= 85",
            max_experiments=20,
            variation_adapter=var_adapter,
            batch_size=5,
        )

        result = engine.optimize(_make_base_request())

        assert result.target_met is True
        assert result.best_value >= 85
        assert result.best_experiment is not None
        assert result.best_experiment.metric_value >= 85
        assert result.experiments_run <= 20

    def test_optimize_exhausts_budget(self):
        """Engine returns target_met=False after exhausting max_experiments."""
        # Always return score=50 (below target of 85)
        low_response = _make_response(
            content=_validation_json("FAIL", 50), cost_usd=0.01
        )
        exec_adapter = SequentialMockAdapter([low_response])

        var_lines = "\n".join(
            f"VARIATION {i}: Alternative prompt version {i}"
            for i in range(1, 6)
        )
        var_adapter = MockAdapter(preset_responses={"meta-variation": var_lines})

        engine = AutoResearchEngine(
            adapter=exec_adapter,
            metric_name="completeness_score",
            target=">= 85",
            max_experiments=5,
            variation_adapter=var_adapter,
            batch_size=5,
        )

        result = engine.optimize(_make_base_request())

        assert result.target_met is False
        assert result.experiments_run == 5
        assert result.best_value == 50.0
        assert result.best_experiment is not None

    def test_experiment_history_recorded(self):
        """All experiments appear in result.experiment_history."""
        low_response = _make_response(
            content=_validation_json("FAIL", 50), cost_usd=0.002
        )
        exec_adapter = SequentialMockAdapter([low_response])

        var_lines = "\n".join(
            f"VARIATION {i}: Alt prompt {i}" for i in range(1, 4)
        )
        var_adapter = MockAdapter(preset_responses={"meta-variation": var_lines})

        engine = AutoResearchEngine(
            adapter=exec_adapter,
            metric_name="completeness_score",
            target=">= 85",
            max_experiments=3,
            variation_adapter=var_adapter,
            batch_size=3,
        )

        result = engine.optimize(_make_base_request())

        assert len(result.experiment_history) == 3
        assert all(e.id.startswith("EXP-") for e in result.experiment_history)
        assert result.experiment_history[0].id == "EXP-001"
        assert result.experiment_history[2].id == "EXP-003"
        assert result.total_cost_usd > 0


# ---------------------------------------------------------------------------
# Variation strategies
# ---------------------------------------------------------------------------


class TestVariationStrategies:
    def test_context_pruning_variations(self):
        """3 upstream artifacts produce 4 variations (3 pruned + 1 baseline)."""
        adapter = MockAdapter()
        engine = AutoResearchEngine(adapter, "cost_usd", "<= 0.05")

        base = _make_base_request(upstream_artifacts={
            "PRD-TEST-001": "prd content",
            "SAD-TEST-001": "sad content",
            "TDD-TEST-001": "tdd content",
        })

        variations = engine._vary_context_pruning(base)

        assert len(variations) == 4  # 1 baseline + 3 pruned
        # First is baseline
        assert variations[0][1] == "baseline (no pruning)"
        assert len(variations[0][0].upstream_artifacts) == 3
        # Each pruned variation removes exactly one artifact
        for var_request, desc in variations[1:]:
            assert len(var_request.upstream_artifacts) == 2
            assert desc.startswith("pruned upstream:")

    def test_constraint_ordering_with_insufficient_constraints(self):
        """With fewer than 2 constraints, return baseline only."""
        adapter = MockAdapter()
        engine = AutoResearchEngine(adapter, "cost_usd", "<= 0.05")

        base = _make_base_request(correction_constraints=["only one"])
        variations = engine._vary_constraint_ordering(base, count=3)

        assert len(variations) == 1
        assert "no reordering" in variations[0][1]

    def test_unknown_strategy_raises(self):
        """Unknown variation strategy raises ValueError."""
        adapter = MockAdapter()
        engine = AutoResearchEngine(adapter, "cost_usd", "<= 0.05")
        base = _make_base_request()

        with pytest.raises(ValueError, match="Unknown variation strategy"):
            engine._generate_variations(base, "nonexistent_strategy")

    def test_model_comparison_returns_duplicates(self):
        """model_comparison strategy returns N identical requests."""
        adapter = MockAdapter()
        engine = AutoResearchEngine(adapter, "cost_usd", "<= 0.05")
        base = _make_base_request()

        variations = engine._generate_variations(base, "model_comparison", count=3)

        assert len(variations) == 3
        for var_request, desc in variations:
            assert var_request is base
            assert "baseline" in desc
