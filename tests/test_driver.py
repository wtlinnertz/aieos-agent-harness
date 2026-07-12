"""Tests for the HarnessDriver facade (ADR-0002 -- the dark factory's only seam)."""

import json
from pathlib import Path

import pytest

from src.adapters.mock import MockAdapter
from src.driver import HarnessDriver
from src.freeze import FreezeError, hash_artifact_content
from src.models import (
    AgentRequest,
    ArtifactStatus,
    DecisionOutcome,
    FreezeGateDecision,
    LifecycleEvent,
    LifecycleResult,
)
from src.state import read_frozen_artifacts


PASS_JSON = json.dumps(
    {
        "status": "PASS",
        "summary": "All gates passed.",
        "hard_gates": {"g1": "PASS"},
        "blocking_issues": [],
        "warnings": [],
        "completeness_score": 95,
    }
)
FAIL_JSON = json.dumps(
    {
        "status": "FAIL",
        "summary": "A gate failed.",
        "hard_gates": {"g1": "FAIL"},
        "blocking_issues": [{"gate": "g1", "description": "missing section", "location": "s1"}],
        "warnings": [],
        "completeness_score": 40,
    }
)


def _requests():
    gen = AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.POST_GENERATION,
        spec_content="spec",
        template_content="tmpl",
        prompt_content="prompt",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-TEST-001"},
    )
    val = AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.POST_VALIDATION,
        spec_content="spec",
        template_content="",
        prompt_content="validate",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-TEST-001"},
    )
    return gen, val


class TestRunArtifactLifecycle:
    def test_converged_on_validation_pass(self, tmp_path):
        gen = MockAdapter()
        val = MockAdapter(preset_responses={"SAD": PASS_JSON})
        driver = HarnessDriver(tmp_path, gen, val)
        gen_req, val_req = _requests()
        assert driver.run_artifact_lifecycle(gen_req, val_req) == LifecycleResult.CONVERGED

    def test_escalation_when_budget_exhausted(self, tmp_path):
        gen = MockAdapter()
        val = MockAdapter(preset_responses={"SAD": FAIL_JSON})
        driver = HarnessDriver(tmp_path, gen, val, max_iterations=2)
        gen_req, val_req = _requests()
        assert driver.run_artifact_lifecycle(gen_req, val_req) == LifecycleResult.ESCALATION_NEEDED

    def test_lifecycle_never_freezes(self, tmp_path):
        # A converged lifecycle must not write FROZEN -- promotion needs a decision.
        sdlc = tmp_path / "docs" / "sdlc"
        sdlc.mkdir(parents=True)
        (sdlc / "05-sad.md").write_text(
            "| Artifact ID | SAD-TEST-001 |\n| Status | FREEZE_PENDING |\n"
        )
        driver = HarnessDriver(tmp_path, MockAdapter(), MockAdapter(preset_responses={"SAD": PASS_JSON}))
        gen_req, val_req = _requests()
        driver.run_artifact_lifecycle(gen_req, val_req)
        assert read_frozen_artifacts(tmp_path)["SAD-TEST-001"] == ArtifactStatus.FREEZE_PENDING


def _initiative_with_artifact(tmp_path):
    sdlc = tmp_path / "docs" / "sdlc"
    sdlc.mkdir(parents=True)
    (sdlc / "05-sad.md").write_text(
        "## Document Control\n\n"
        "| Artifact ID | SAD-TEST-001 |\n| Status | FREEZE_PENDING |\n"
    )
    return hash_artifact_content((sdlc / "05-sad.md").read_text())


class TestReadLayerState:
    def test_reads_er_state_block(self, tmp_path):
        eng = tmp_path / "docs" / "engagement"
        eng.mkdir(parents=True)
        (eng / "er.md").write_text(
            "## 1b\n\n| Field | Value |\n|--|--|\n"
            "| Current Layer | Layer 4 |\n| Frozen Count | 5 |\n"
        )
        driver = HarnessDriver(tmp_path, MockAdapter(), MockAdapter())
        state = driver.read_layer_state()
        assert state.current_layer == "Layer 4"
        assert state.frozen_count == 5


class TestApplyFreezeThroughFacade:
    def test_facade_freezes(self, tmp_path):
        h = _initiative_with_artifact(tmp_path)
        driver = HarnessDriver(tmp_path, MockAdapter(), MockAdapter())
        decision = FreezeGateDecision(
            artifact_id="SAD-TEST-001",
            outcome=DecisionOutcome.APPROVE,
            content_hash=h,
            decided_by="Todd",
        )
        result = driver.apply_freeze_decision(decision)
        assert result.status == ArtifactStatus.FROZEN
        assert read_frozen_artifacts(tmp_path)["SAD-TEST-001"] == ArtifactStatus.FROZEN

    def test_facade_propagates_freeze_error(self, tmp_path):
        _initiative_with_artifact(tmp_path)
        driver = HarnessDriver(tmp_path, MockAdapter(), MockAdapter())
        decision = FreezeGateDecision(
            artifact_id="SAD-TEST-001",
            outcome=DecisionOutcome.APPROVE,
            content_hash="wronghash" * 7,
            decided_by="Todd",
        )
        with pytest.raises(FreezeError):
            driver.apply_freeze_decision(decision)
