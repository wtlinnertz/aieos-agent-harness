"""Tests for core data models."""

from src.models import (
    AgentRequest,
    AgentResponse,
    ArtifactStatus,
    ConvergenceState,
    DecisionOutcome,
    ERStateBlock,
    HealthStatus,
    InvariantCheck,
    InvocationRecord,
    LifecycleEvent,
    RoutingStrategy,
    ValidationResult,
)


class TestEnums:
    def test_artifact_status_values(self):
        assert ArtifactStatus.DRAFT.value == "DRAFT"
        assert ArtifactStatus.VALIDATED.value == "VALIDATED"
        assert ArtifactStatus.FREEZE_PENDING.value == "FREEZE_PENDING"
        assert ArtifactStatus.FROZEN.value == "FROZEN"
        assert len(ArtifactStatus) == 4

    def test_lifecycle_event_values(self):
        assert LifecycleEvent.PRE_GENERATION.value == "PRE_GENERATION"
        assert LifecycleEvent.POST_GENERATION.value == "POST_GENERATION"
        assert LifecycleEvent.PRE_VALIDATION.value == "PRE_VALIDATION"
        assert LifecycleEvent.POST_VALIDATION.value == "POST_VALIDATION"
        assert LifecycleEvent.POST_FREEZE.value == "POST_FREEZE"
        assert LifecycleEvent.ON_FAILURE.value == "ON_FAILURE"
        assert len(LifecycleEvent) == 6

    def test_routing_strategy_values(self):
        assert RoutingStrategy.PARALLEL_CONSENSUS.value == "PARALLEL_CONSENSUS"
        assert RoutingStrategy.PIPELINE.value == "PIPELINE"
        assert RoutingStrategy.FALLBACK.value == "FALLBACK"
        assert RoutingStrategy.COST_AWARE.value == "COST_AWARE"
        assert len(RoutingStrategy) == 4

    def test_health_status_values(self):
        assert HealthStatus.OK.value == "OK"
        assert HealthStatus.DEGRADED.value == "DEGRADED"
        assert HealthStatus.DOWN.value == "DOWN"
        assert len(HealthStatus) == 3

    def test_decision_outcome_values(self):
        assert DecisionOutcome.APPROVE.value == "APPROVE"
        assert DecisionOutcome.APPROVE_WITH_CONDITIONS.value == "APPROVE_WITH_CONDITIONS"
        assert DecisionOutcome.BLOCK.value == "BLOCK"
        assert DecisionOutcome.REMEDIATE_AND_RETRY.value == "REMEDIATE_AND_RETRY"
        assert DecisionOutcome.REQUIRE_REDESIGN.value == "REQUIRE_REDESIGN"
        assert DecisionOutcome.ROLLBACK.value == "ROLLBACK"
        assert len(DecisionOutcome) == 6


class TestAgentRequest:
    def test_construction(self):
        req = AgentRequest(
            artifact_type="PRD",
            event=LifecycleEvent.PRE_GENERATION,
            spec_content="spec",
            template_content="template",
            prompt_content="prompt",
            upstream_artifacts={"DPRD": "content"},
            current_artifact=None,
            correction_constraints=[],
            metadata={"initiative": "TEST-001"},
        )
        assert req.artifact_type == "PRD"
        assert req.event == LifecycleEvent.PRE_GENERATION
        assert req.current_artifact is None
        assert req.metadata["initiative"] == "TEST-001"


class TestAgentResponse:
    def test_construction_with_defaults(self):
        resp = AgentResponse(
            content="output",
            provider="anthropic",
            model="claude-3",
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.01,
            latency_ms=1500.0,
        )
        assert resp.raw_response is None
        assert resp.cost_usd == 0.01

    def test_construction_with_raw_response(self):
        resp = AgentResponse(
            content="output",
            provider="openai",
            model="gpt-4",
            tokens_in=50,
            tokens_out=100,
            cost_usd=0.005,
            latency_ms=800.0,
            raw_response={"id": "resp-123"},
        )
        assert resp.raw_response == {"id": "resp-123"}

    def test_provenance_fields_default_none(self):
        resp = AgentResponse(
            content="output",
            provider="test",
            model="test",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            latency_ms=0.0,
        )
        assert resp.human_author is None
        assert resp.input_content_hash is None
        assert resp.modification_record is None
        assert resp.compliance_attestation is None

    def test_provenance_fields_populated(self):
        resp = AgentResponse(
            content="output",
            provider="test",
            model="test",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            latency_ms=0.0,
            human_author="Todd Linnertz",
            input_content_hash="abc123",
            modification_record=[{"action": "reviewed", "by": "Todd"}],
            compliance_attestation="SOX-2026-Q1",
        )
        assert resp.human_author == "Todd Linnertz"
        assert resp.input_content_hash == "abc123"
        assert len(resp.modification_record) == 1
        assert resp.compliance_attestation == "SOX-2026-Q1"


class TestMockAdapterProvenance:
    def test_mock_adapter_populates_provenance(self):
        from src.adapters.mock import MockAdapter
        from src.models import AgentRequest, LifecycleEvent

        adapter = MockAdapter()
        request = AgentRequest(
            artifact_type="SAD",
            event=LifecycleEvent.POST_GENERATION,
            spec_content="spec",
            template_content="template",
            prompt_content="prompt",
            upstream_artifacts={"PRD-TEST-001": "prd content"},
            current_artifact=None,
            correction_constraints=[],
            metadata={"human_author": "Todd Linnertz"},
        )
        response = adapter.invoke(request)
        assert response.human_author == "Todd Linnertz"
        assert response.input_content_hash is not None
        assert len(response.input_content_hash) == 64  # SHA256 hex digest


class TestValidationResult:
    def test_construction(self):
        vr = ValidationResult(
            status="PASS",
            summary="All gates passed",
            hard_gates={"gate1": "PASS"},
            blocking_issues=[],
            warnings=[],
            completeness_score=95,
        )
        assert vr.status == "PASS"
        assert vr.completeness_score == 95


class TestERStateBlock:
    def test_construction(self):
        block = ERStateBlock(
            current_layer="Layer 4",
            current_artifact="TDD-TEST-001",
            current_step="Generation",
            frozen_count=3,
            next_action="Generate TDD",
            blocking_on="None",
            last_updated="2026-03-25",
        )
        assert block.frozen_count == 3
        assert block.current_layer == "Layer 4"


class TestInvocationRecord:
    def test_construction_with_defaults(self):
        rec = InvocationRecord(
            timestamp="2026-03-25T10:00:00Z",
            artifact_type="PRD",
            artifact_id="PRD-TEST-001",
            event=LifecycleEvent.POST_GENERATION,
            provider="anthropic",
            model="claude-3",
            strategy=RoutingStrategy.FALLBACK,
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.01,
            latency_ms=1500.0,
            result="success",
            validation_status="PASS",
            convergence_iteration=0,
        )
        assert rec.error is None
        assert rec.strategy == RoutingStrategy.FALLBACK


class TestConvergenceState:
    def test_defaults(self):
        cs = ConvergenceState(artifact_id="PRD-TEST-001", artifact_type="PRD")
        assert cs.max_iterations == 3
        assert cs.current_iteration == 0
        assert cs.ledger == []

    def test_ledger_independence(self):
        cs1 = ConvergenceState(artifact_id="A", artifact_type="PRD")
        cs2 = ConvergenceState(artifact_id="B", artifact_type="PRD")
        cs1.ledger.append({"iter": 1})
        assert cs2.ledger == []


class TestInvariantCheck:
    def test_construction(self):
        ic = InvariantCheck(name="test", passed=True, reason="ok")
        assert ic.passed is True
        assert ic.name == "test"
