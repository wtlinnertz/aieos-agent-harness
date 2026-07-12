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


class TestAlwaysFail:
    def test_validation_returns_fail(self):
        a = ConvergingMockAdapter(always_fail=True)
        resp = a.invoke(_req(LifecycleEvent.PRE_VALIDATION))
        assert json.loads(resp.content)["status"] == "FAIL"

    def test_generation_still_returns_content(self):
        a = ConvergingMockAdapter(always_fail=True)
        resp = a.invoke(_req(LifecycleEvent.PRE_GENERATION))
        assert "PRD" in resp.content

    def test_run_artifact_escalates_with_failing_mock(self, tmp_path):
        from src.driver import HarnessDriver
        from src.models import LifecycleResult

        aieos_root = tmp_path / "aieos"
        kit = aieos_root / "aieos-eek"
        for sub in ("specs", "artifacts", "prompts"):
            (kit / "docs" / sub).mkdir(parents=True)
        (kit / "docs" / "specs" / "prd-spec.md").write_text("# prd spec")
        (kit / "docs" / "artifacts" / "prd-template.md").write_text("t")
        (kit / "docs" / "prompts" / "prd-prompt.md").write_text("p")
        init = tmp_path / "init"
        init.mkdir()
        failing = ConvergingMockAdapter(always_fail=True)
        driver = HarnessDriver(init, failing, failing, max_iterations=2, aieos_root=aieos_root)
        assert driver.run_artifact("PRD") == LifecycleResult.ESCALATION_NEEDED
        # nothing persisted on escalation
        assert not (init / "docs" / "sdlc" / "prd.md").exists()
