"""Tests for the ConvergingMockAdapter (offline Phase 5 provider)."""

import json

from src.adapters.converging_mock import ConvergingMockAdapter
from src.models import AgentRequest, HealthStatus, LifecycleEvent


def _req(event):
    return AgentRequest(
        artifact_type="PRD",
        event=event,
        spec_content="s",
        template_content="t",
        prompt_content="p",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={},
    )


class TestConvergingMock:
    def test_validation_event_returns_pass_json(self):
        a = ConvergingMockAdapter()
        resp = a.invoke(_req(LifecycleEvent.PRE_VALIDATION))
        data = json.loads(resp.content)
        assert data["status"] == "PASS"

    def test_generation_event_returns_content(self):
        a = ConvergingMockAdapter()
        resp = a.invoke(_req(LifecycleEvent.PRE_GENERATION))
        assert "PRD" in resp.content
        assert resp.content.strip().startswith("#")

    def test_health_ok_and_zero_cost(self):
        a = ConvergingMockAdapter()
        assert a.health() == HealthStatus.OK
        assert a.cost_estimate(_req(LifecycleEvent.PRE_GENERATION)) == 0.0
        assert a.provider_name == "mock"
