"""Integration test: full convergence loop with mock providers.

Tests the bounded generate-validate cycle through 3 iterations,
verifying ledger recording, separate sessions, and escalation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.adapters.mock import MockAdapter
from src.convergence import ConvergenceLoop
from src.models import (
    AgentRequest,
    AgentResponse,
    HealthStatus,
    LifecycleEvent,
)


# ---------------------------------------------------------------------------
# SequentialMockAdapter — returns different responses on successive calls
# ---------------------------------------------------------------------------

class SequentialMockAdapter:
    """Mock adapter that returns a different response for each successive invoke() call."""

    def __init__(
        self,
        responses: list[str],
        provider_name: str = "sequential-mock",
        model_name: str = "seq-model-v1",
    ) -> None:
        self._responses = responses
        self._provider_name = provider_name
        self._model_name = model_name
        self._call_index = 0
        self.call_history: list[tuple[str, tuple[Any, ...]]] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def invoke(self, request: AgentRequest) -> AgentResponse:
        self.call_history.append(("invoke", (request,)))
        if self._call_index >= len(self._responses):
            raise RuntimeError(
                f"SequentialMockAdapter exhausted after {len(self._responses)} calls"
            )
        content = self._responses[self._call_index]
        self._call_index += 1
        return AgentResponse(
            content=content,
            provider=self._provider_name,
            model=self._model_name,
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.001,
            latency_ms=50.0,
        )

    def health(self) -> HealthStatus:
        return HealthStatus.OK

    def cost_estimate(self, request: AgentRequest) -> float:
        return 0.001


# ---------------------------------------------------------------------------
# Fixture validation JSON responses
# ---------------------------------------------------------------------------

ARTIFACT_V1 = "# SAD v1\n\nInitial architecture with missing dependency map."

VALIDATION_FAIL_2_ISSUES = json.dumps({
    "status": "FAIL",
    "summary": "Two hard gates failed.",
    "hard_gates": {
        "component_identified": "PASS",
        "dependency_map": "FAIL",
        "layer_assignment": "FAIL",
    },
    "blocking_issues": [
        {"gate": "dependency_map", "description": "No dependency map provided", "location": "Section 2"},
        {"gate": "layer_assignment", "description": "Components not assigned to layers", "location": "Section 3"},
    ],
    "warnings": [],
    "completeness_score": 40,
})

ARTIFACT_V2 = "# SAD v2\n\nArchitecture with dependency map added but layer assignment still missing."

VALIDATION_FAIL_1_ISSUE = json.dumps({
    "status": "FAIL",
    "summary": "One hard gate failed.",
    "hard_gates": {
        "component_identified": "PASS",
        "dependency_map": "PASS",
        "layer_assignment": "FAIL",
    },
    "blocking_issues": [
        {"gate": "layer_assignment", "description": "Components not assigned to layers", "location": "Section 3"},
    ],
    "warnings": [],
    "completeness_score": 70,
})

ARTIFACT_V3 = "# SAD v3\n\nFull architecture with deps and layer assignment."

VALIDATION_PASS = json.dumps({
    "status": "PASS",
    "summary": "All hard gates satisfied.",
    "hard_gates": {
        "component_identified": "PASS",
        "dependency_map": "PASS",
        "layer_assignment": "PASS",
    },
    "blocking_issues": [],
    "warnings": [],
    "completeness_score": 95,
})

# Always-fail response for escalation test
VALIDATION_ALWAYS_FAIL = json.dumps({
    "status": "FAIL",
    "summary": "Gate still failing.",
    "hard_gates": {"dependency_map": "FAIL"},
    "blocking_issues": [
        {"gate": "dependency_map", "description": "Still missing", "location": "Section 2"},
    ],
    "warnings": [],
    "completeness_score": 30,
})


def _make_gen_request() -> AgentRequest:
    return AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.POST_GENERATION,
        spec_content="# SAD Spec\n## Hard Gates\n1. component_identified\n2. dependency_map\n3. layer_assignment",
        template_content="# SAD Template",
        prompt_content="Generate a SAD.",
        upstream_artifacts={"PRD-TEST-001": "frozen PRD"},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-TEST-001", "initiative": "TEST-001"},
    )


def _make_val_request() -> AgentRequest:
    return AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.POST_VALIDATION,
        spec_content="# SAD Spec\n## Hard Gates\n1. component_identified\n2. dependency_map\n3. layer_assignment",
        template_content="# SAD Template",
        prompt_content="Validate the SAD.",
        upstream_artifacts={"PRD-TEST-001": "frozen PRD"},
        current_artifact=None,  # Will be replaced by convergence loop
        correction_constraints=[],
        metadata={"artifact_id": "SAD-TEST-001", "initiative": "TEST-001"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConvergenceCompletesInTwoCorrections:
    """Run the loop: generate -> FAIL(2) -> correct -> FAIL(1) -> correct -> PASS."""

    def test_convergence_completes_in_two_corrections(self):
        # Sequence: generate, validate(FAIL), correct, validate(FAIL), correct, validate(PASS)
        gen_adapter = SequentialMockAdapter(
            responses=[ARTIFACT_V1, ARTIFACT_V2, ARTIFACT_V3],
            provider_name="gen-mock",
        )
        val_adapter = SequentialMockAdapter(
            responses=[VALIDATION_FAIL_2_ISSUES, VALIDATION_FAIL_1_ISSUE, VALIDATION_PASS],
            provider_name="val-mock",
        )

        loop = ConvergenceLoop(
            generate_adapter=gen_adapter,
            validate_adapter=val_adapter,
            max_iterations=3,
        )

        gen_response, val_result, cstate = loop.run(
            _make_gen_request(), _make_val_request()
        )

        # Final result should be PASS
        assert val_result.status == "PASS"
        assert val_result.completeness_score == 95

        # Ledger should have 3 entries (iterations 1, 2, 3)
        assert len(cstate.ledger) == 3
        assert cstate.ledger[0]["status"] == "FAIL"
        assert cstate.ledger[1]["status"] == "FAIL"
        assert cstate.ledger[2]["status"] == "PASS"

        # Two correction iterations (iterations 2 and 3 were corrections)
        assert cstate.current_iteration == 3

        # Final artifact content should be v3
        assert gen_response.content == ARTIFACT_V3


class TestConvergenceUsesSeparateSessions:
    """Verify generate and validate are separate invoke calls (check call counts)."""

    def test_convergence_uses_separate_sessions(self):
        gen_adapter = SequentialMockAdapter(
            responses=[ARTIFACT_V1, ARTIFACT_V2, ARTIFACT_V3],
            provider_name="gen-mock",
        )
        val_adapter = SequentialMockAdapter(
            responses=[VALIDATION_FAIL_2_ISSUES, VALIDATION_FAIL_1_ISSUE, VALIDATION_PASS],
            provider_name="val-mock",
        )

        loop = ConvergenceLoop(
            generate_adapter=gen_adapter,
            validate_adapter=val_adapter,
            max_iterations=3,
        )

        loop.run(_make_gen_request(), _make_val_request())

        # Generate adapter should have been invoked 3 times (initial + 2 corrections)
        gen_calls = [c for c in gen_adapter.call_history if c[0] == "invoke"]
        assert len(gen_calls) == 3

        # Validate adapter should have been invoked 3 times (one per iteration)
        val_calls = [c for c in val_adapter.call_history if c[0] == "invoke"]
        assert len(val_calls) == 3

        # Each validate call should have received the generated content as current_artifact
        for i, (_, args) in enumerate(val_calls):
            request = args[0]
            assert request.current_artifact is not None
            assert request.event == LifecycleEvent.POST_VALIDATION


class TestConvergenceEscalation:
    """Mock always returns FAIL; verify escalation after max iterations."""

    def test_convergence_escalation(self):
        gen_adapter = SequentialMockAdapter(
            responses=["artifact v1", "artifact v2", "artifact v3"],
            provider_name="gen-mock",
        )
        val_adapter = SequentialMockAdapter(
            responses=[VALIDATION_ALWAYS_FAIL, VALIDATION_ALWAYS_FAIL, VALIDATION_ALWAYS_FAIL],
            provider_name="val-mock",
        )

        loop = ConvergenceLoop(
            generate_adapter=gen_adapter,
            validate_adapter=val_adapter,
            max_iterations=3,
        )

        gen_response, val_result, cstate = loop.run(
            _make_gen_request(), _make_val_request()
        )

        # Result should still be FAIL (escalation needed)
        assert val_result.status == "FAIL"

        # All 3 iterations consumed
        assert cstate.current_iteration == 3
        assert len(cstate.ledger) == 3

        # All ledger entries should be FAIL
        for entry in cstate.ledger:
            assert entry["status"] == "FAIL"

        # Convergence state indicates max was hit (current == max)
        assert cstate.current_iteration == cstate.max_iterations
