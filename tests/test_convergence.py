"""Tests for the convergence loop."""

from __future__ import annotations

import json

import pytest

from src.adapters.mock import MockAdapter
from src.convergence import ConvergenceLoop, parse_validation_result
from src.models import (
    AgentRequest,
    AgentResponse,
    ConvergenceState,
    LifecycleEvent,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS_VALIDATION = json.dumps(
    {
        "status": "PASS",
        "summary": "All gates passed.",
        "hard_gates": {"completeness": "PASS", "structure": "PASS"},
        "blocking_issues": [],
        "warnings": [],
        "completeness_score": 95,
    }
)

FAIL_GATE_A = json.dumps(
    {
        "status": "FAIL",
        "summary": "Gate A failed.",
        "hard_gates": {"gate_a": "FAIL", "gate_b": "PASS"},
        "blocking_issues": [
            {
                "gate": "gate_a",
                "description": "Missing required section",
                "location": "§3",
            }
        ],
        "warnings": [],
        "completeness_score": 40,
    }
)

FAIL_GATE_B = json.dumps(
    {
        "status": "FAIL",
        "summary": "Gate B failed.",
        "hard_gates": {"gate_a": "PASS", "gate_b": "FAIL"},
        "blocking_issues": [
            {
                "gate": "gate_b",
                "description": "Incomplete analysis",
                "location": "§5",
            }
        ],
        "warnings": [],
        "completeness_score": 50,
    }
)

FAIL_GATE_A_SAME = FAIL_GATE_A  # Identical to detect staleness


def _make_request(artifact_type: str = "PRD") -> AgentRequest:
    return AgentRequest(
        artifact_type=artifact_type,
        event=LifecycleEvent.PRE_GENERATION,
        spec_content="spec",
        template_content="template",
        prompt_content="prompt",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "PRD-TEST-001"},
    )


class SequentialMockAdapter:
    """Mock adapter that returns different responses on successive calls.

    Satisfies the AgentAdapter protocol.
    """

    def __init__(
        self,
        provider_name: str = "seq-mock",
        model_name: str = "seq-model",
        responses: list[str] | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._model_name = model_name
        self._responses = responses or []
        self._call_count = 0
        self.call_history: list[tuple[str, tuple]] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def invoke(self, request: AgentRequest) -> AgentResponse:
        self.call_history.append(("invoke", (request,)))
        idx = min(self._call_count, len(self._responses) - 1)
        content = self._responses[idx] if self._responses else "default"
        self._call_count += 1
        return AgentResponse(
            content=content,
            provider=self._provider_name,
            model=self._model_name,
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.001,
            latency_ms=50.0,
        )

    def health(self):
        from src.models import HealthStatus

        return HealthStatus.OK

    def cost_estimate(self, request: AgentRequest) -> float:
        return 0.001


# ---------------------------------------------------------------------------
# parse_validation_result tests
# ---------------------------------------------------------------------------


class TestParseValidationResult:
    def test_parses_raw_json(self) -> None:
        response = AgentResponse(
            content=PASS_VALIDATION,
            provider="test",
            model="test",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0,
            latency_ms=0,
        )
        result = parse_validation_result(response)
        assert result.status == "PASS"
        assert result.completeness_score == 95

    def test_parses_fenced_json(self) -> None:
        content = f"Here is the result:\n```json\n{PASS_VALIDATION}\n```\nDone."
        response = AgentResponse(
            content=content,
            provider="test",
            model="test",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0,
            latency_ms=0,
        )
        result = parse_validation_result(response)
        assert result.status == "PASS"

    def test_raises_on_no_json(self) -> None:
        response = AgentResponse(
            content="No JSON here at all",
            provider="test",
            model="test",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0,
            latency_ms=0,
        )
        with pytest.raises(ValueError, match="No JSON found"):
            parse_validation_result(response)


# ---------------------------------------------------------------------------
# ConvergenceLoop tests
# ---------------------------------------------------------------------------


class TestConvergenceLoop:
    def test_first_pass_success(self) -> None:
        """Generation passes validation on first try, 0 corrections."""
        gen = MockAdapter(
            provider_name="generator",
            preset_responses={"PRD": "Generated PRD content"},
        )
        val = SequentialMockAdapter(
            provider_name="validator",
            responses=[PASS_VALIDATION],
        )
        loop = ConvergenceLoop(gen, val, max_iterations=3)

        response, result, state = loop.run(_make_request(), _make_request())

        assert result.status == "PASS"
        assert state.current_iteration == 1
        assert len(state.ledger) == 1
        assert state.ledger[0]["status"] == "PASS"

    def test_convergence_after_one_correction(self) -> None:
        """First validation fails, correction succeeds."""
        gen = SequentialMockAdapter(
            provider_name="generator",
            responses=["attempt 1", "attempt 2 corrected"],
        )
        val = SequentialMockAdapter(
            provider_name="validator",
            responses=[FAIL_GATE_A, PASS_VALIDATION],
        )
        loop = ConvergenceLoop(gen, val, max_iterations=3)

        response, result, state = loop.run(_make_request(), _make_request())

        assert result.status == "PASS"
        assert state.current_iteration == 2
        assert len(state.ledger) == 2
        assert state.ledger[0]["status"] == "FAIL"
        assert state.ledger[1]["status"] == "PASS"

    def test_convergence_after_two_corrections(self) -> None:
        """Two corrections needed before pass."""
        gen = SequentialMockAdapter(
            provider_name="generator",
            responses=["attempt 1", "attempt 2", "attempt 3"],
        )
        val = SequentialMockAdapter(
            provider_name="validator",
            responses=[FAIL_GATE_A, FAIL_GATE_B, PASS_VALIDATION],
        )
        loop = ConvergenceLoop(gen, val, max_iterations=3)

        response, result, state = loop.run(_make_request(), _make_request())

        assert result.status == "PASS"
        assert state.current_iteration == 3
        assert len(state.ledger) == 3

    def test_escalation_at_max_iterations(self) -> None:
        """3 failures, state indicates escalation needed."""
        gen = SequentialMockAdapter(
            provider_name="generator",
            responses=["attempt 1", "attempt 2", "attempt 3"],
        )
        val = SequentialMockAdapter(
            provider_name="validator",
            responses=[FAIL_GATE_A, FAIL_GATE_A, FAIL_GATE_A],
        )
        loop = ConvergenceLoop(gen, val, max_iterations=3)

        response, result, state = loop.run(_make_request(), _make_request())

        assert result.status == "FAIL"
        assert state.current_iteration == 3
        assert len(state.ledger) == 3
        # All ledger entries should be FAIL
        assert all(entry["status"] == "FAIL" for entry in state.ledger)

    def test_staleness_detection(self) -> None:
        """Same gate fails identically twice — staleness detected."""
        loop = ConvergenceLoop.__new__(ConvergenceLoop)
        loop._gen = None  # type: ignore[assignment]
        loop._val = None  # type: ignore[assignment]
        loop._max = 3

        state = ConvergenceState(
            artifact_id="PRD-TEST-001",
            artifact_type="PRD",
            max_iterations=3,
            current_iteration=2,
            ledger=[
                {
                    "iteration": 1,
                    "status": "FAIL",
                    "hard_gates": {"gate_a": "FAIL"},
                    "blocking_issues": [
                        {
                            "gate": "gate_a",
                            "description": "Missing required section",
                        }
                    ],
                    "completeness_score": 40,
                },
                {
                    "iteration": 2,
                    "status": "FAIL",
                    "hard_gates": {"gate_a": "FAIL"},
                    "blocking_issues": [
                        {
                            "gate": "gate_a",
                            "description": "Missing required section",
                        }
                    ],
                    "completeness_score": 40,
                },
            ],
        )

        assert loop._detect_staleness(state) is True

    def test_oscillation_detection(self) -> None:
        """Gate A/B alternating failures — oscillation detected."""
        loop = ConvergenceLoop.__new__(ConvergenceLoop)
        loop._gen = None  # type: ignore[assignment]
        loop._val = None  # type: ignore[assignment]
        loop._max = 3

        state = ConvergenceState(
            artifact_id="PRD-TEST-001",
            artifact_type="PRD",
            max_iterations=3,
            current_iteration=3,
            ledger=[
                {
                    "iteration": 1,
                    "status": "FAIL",
                    "hard_gates": {"gate_a": "FAIL", "gate_b": "PASS"},
                    "blocking_issues": [],
                    "completeness_score": 40,
                },
                {
                    "iteration": 2,
                    "status": "FAIL",
                    "hard_gates": {"gate_a": "PASS", "gate_b": "FAIL"},
                    "blocking_issues": [],
                    "completeness_score": 50,
                },
                {
                    "iteration": 3,
                    "status": "FAIL",
                    "hard_gates": {"gate_a": "FAIL", "gate_b": "PASS"},
                    "blocking_issues": [],
                    "completeness_score": 40,
                },
            ],
        )

        assert loop._detect_oscillation(state) is True

    def test_generation_validation_separation(self) -> None:
        """Verify generate and validate are separate invoke() calls."""
        gen = SequentialMockAdapter(
            provider_name="generator",
            responses=["generated content"],
        )
        val = SequentialMockAdapter(
            provider_name="validator",
            responses=[PASS_VALIDATION],
        )
        loop = ConvergenceLoop(gen, val, max_iterations=3)

        response, result, state = loop.run(_make_request(), _make_request())

        # Generator should have exactly 1 invoke call
        assert len(gen.call_history) == 1
        assert gen.call_history[0][0] == "invoke"

        # Validator should have exactly 1 invoke call
        assert len(val.call_history) == 1
        assert val.call_history[0][0] == "invoke"

        # Validator's request should have the generated content as current_artifact
        val_request: AgentRequest = val.call_history[0][1][0]
        assert val_request.current_artifact == "generated content"

        # Generator and validator are separate adapter instances
        assert gen is not val


# ---------------------------------------------------------------------------
# Lens-driven convergence tests
# ---------------------------------------------------------------------------


class TestLensReviewConvergence:
    """Lens reviews that FAIL feed back into the convergence loop."""

    def _make_request(self):
        return AgentRequest(
            artifact_type="SAD",
            event=LifecycleEvent.POST_GENERATION,
            spec_content="spec",
            template_content="template",
            prompt_content="prompt",
            upstream_artifacts={},
            current_artifact=None,
            correction_constraints=[],
            metadata={"artifact_id": "SAD-TEST-001"},
        )

    def _make_val_request(self):
        return AgentRequest(
            artifact_type="SAD",
            event=LifecycleEvent.POST_VALIDATION,
            spec_content="spec",
            template_content="",
            prompt_content="validate",
            upstream_artifacts={},
            current_artifact=None,
            correction_constraints=[],
            metadata={},
        )

    def _pass_json(self):
        return json.dumps({
            "status": "PASS", "summary": "OK",
            "hard_gates": {"g1": "PASS"},
            "blocking_issues": [], "warnings": [],
            "completeness_score": 90,
        })

    def _fail_json(self, gate="threat_boundary", desc="Missing threat boundary"):
        return json.dumps({
            "status": "FAIL", "summary": "Lens failed",
            "hard_gates": {gate: "FAIL"},
            "blocking_issues": [{"gate": gate, "description": desc, "location": "§4"}],
            "warnings": [],
            "completeness_score": 40,
        })

    def test_lens_pass_no_re_generation(self):
        """All lenses pass — no re-generation needed."""
        gen = MockAdapter(preset_responses={"SAD": "architecture content"})
        val = SequentialMockAdapter("val", "v1", [self._pass_json()] * 10)
        sec_lens = SequentialMockAdapter("sec", "v1", [self._pass_json()] * 10)

        loop = ConvergenceLoop(gen, val, lens_adapters={"security": sec_lens})
        response, result, state = loop.run(self._make_request(), self._make_val_request())

        assert result.status == "PASS"
        assert state.current_iteration == 1
        assert len(gen.call_history) == 1  # Generated once, no re-generation

    def test_lens_fail_triggers_re_generation(self):
        """Lens FAIL feeds back as correction constraint and re-generates."""
        gen = MockAdapter(preset_responses={"SAD": "architecture content"})
        # Validator always passes
        val = SequentialMockAdapter("val", "v1", [self._pass_json()] * 10)
        # Security lens: FAIL first time, PASS second time
        sec_lens = SequentialMockAdapter(
            "sec", "v1",
            [self._fail_json(), self._pass_json()]
        )

        loop = ConvergenceLoop(gen, val, lens_adapters={"security": sec_lens})
        response, result, state = loop.run(self._make_request(), self._make_val_request())

        assert result.status == "PASS"
        assert state.current_iteration == 2  # Required 2 iterations
        assert len(gen.call_history) == 2  # Generated twice
        # Second generation should have correction constraint from lens
        second_request = gen.call_history[1][1][0]
        assert any("threat_boundary" in c for c in second_request.correction_constraints)

    def test_lens_fail_at_max_iterations_returns_fail(self):
        """Lens keeps failing through all iterations — result is FAIL."""
        gen = MockAdapter(preset_responses={"SAD": "architecture content"})
        val = SequentialMockAdapter("val", "v1", [self._pass_json()] * 10)
        # Security lens always fails
        sec_lens = SequentialMockAdapter(
            "sec", "v1",
            [self._fail_json()] * 10
        )

        loop = ConvergenceLoop(gen, val, max_iterations=3, lens_adapters={"security": sec_lens})
        response, result, state = loop.run(self._make_request(), self._make_val_request())

        assert result.status == "FAIL"
        assert "Lens review failures" in result.summary
        assert state.current_iteration == 3

    def test_multiple_lenses_independent(self):
        """Multiple lenses run independently — one fail doesn't affect others."""
        gen = MockAdapter(preset_responses={"SAD": "architecture content"})
        val = SequentialMockAdapter("val", "v1", [self._pass_json()] * 10)
        sec_lens = SequentialMockAdapter("sec", "v1", [self._fail_json(), self._pass_json()])
        rel_lens = SequentialMockAdapter("rel", "v1", [self._pass_json()] * 10)

        loop = ConvergenceLoop(
            gen, val,
            lens_adapters={"security": sec_lens, "reliability": rel_lens},
        )
        response, result, state = loop.run(self._make_request(), self._make_val_request())

        assert result.status == "PASS"
        # Both lenses should have been invoked
        assert len(sec_lens.call_history) >= 1
        assert len(rel_lens.call_history) >= 1

    def test_no_lenses_behaves_as_before(self):
        """Without lenses, convergence loop works exactly as before."""
        gen = MockAdapter(preset_responses={"SAD": "architecture content"})
        val = SequentialMockAdapter("val", "v1", [self._pass_json()])

        loop = ConvergenceLoop(gen, val)  # No lens_adapters
        response, result, state = loop.run(self._make_request(), self._make_val_request())

        assert result.status == "PASS"
        assert state.current_iteration == 1


class TestParseValidationResultErrors:
    def test_invalid_json_raises(self) -> None:
        resp = AgentResponse(
            content="```json\n{not: valid, json,}\n```",
            provider="p", model="m", tokens_in=1, tokens_out=1, cost_usd=0.0, latency_ms=1.0,
        )
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_validation_result(resp)

    def test_missing_required_fields_raises(self) -> None:
        resp = AgentResponse(
            content='{"status": "PASS", "summary": "ok"}',
            provider="p", model="m", tokens_in=1, tokens_out=1, cost_usd=0.0, latency_ms=1.0,
        )
        with pytest.raises(ValueError, match="missing required fields"):
            parse_validation_result(resp)


class TestConvergenceLoopEdgeInternals:
    def test_oscillation_detected_terminates_loop(self) -> None:
        """A gate that fails, passes, then fails again trips oscillation detection."""
        gen = MockAdapter(provider_name="gen", preset_responses={"PRD": "content"})
        val = SequentialMockAdapter(
            provider_name="validator",
            responses=[FAIL_GATE_A, FAIL_GATE_B, FAIL_GATE_A],
        )
        loop = ConvergenceLoop(gen, val, max_iterations=4)

        _response, result, state = loop.run(_make_request(), _make_request())

        assert result.status == "FAIL"
        # Oscillation needs >= 3 ledger entries; the loop terminated without converging.
        assert len(state.ledger) >= 3

    def test_lens_error_is_logged_and_does_not_block(self) -> None:
        """A lens adapter that raises is caught; the loop still converges."""
        gen = MockAdapter(provider_name="gen", preset_responses={"PRD": "content"})
        val = SequentialMockAdapter(provider_name="validator", responses=[PASS_VALIDATION])
        bad_lens = MockAdapter(provider_name="security-lens", should_fail=True)
        loop = ConvergenceLoop(gen, val, max_iterations=3, lens_adapters={"security": bad_lens})

        _response, result, _state = loop.run(_make_request(), _make_request())

        assert result.status == "PASS"

    def test_detect_staleness_false_without_matching_issue(self) -> None:
        loop = ConvergenceLoop(MockAdapter(), MockAdapter())
        state = ConvergenceState(
            artifact_id="PRD-X-001",
            artifact_type="PRD",
            ledger=[
                {"hard_gates": {"g": "FAIL"}, "blocking_issues": []},
                {"hard_gates": {"g": "FAIL"}, "blocking_issues": []},
            ],
        )
        assert loop._detect_staleness(state) is False

    def test_detect_oscillation_false_when_gate_fails_throughout(self) -> None:
        loop = ConvergenceLoop(MockAdapter(), MockAdapter())
        state = ConvergenceState(
            artifact_id="PRD-X-001",
            artifact_type="PRD",
            ledger=[
                {"hard_gates": {"g": "FAIL"}},
                {"hard_gates": {"g": "FAIL"}},
                {"hard_gates": {"g": "FAIL"}},
            ],
        )
        assert loop._detect_oscillation(state) is False
