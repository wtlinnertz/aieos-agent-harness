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


import json as _json  # noqa: E402


def _fake_kit(aieos_root, artifact_type, with_validator=False):
    """Create a minimal kit dir tree for run_artifact resolution."""
    kit = aieos_root / "aieos-eek"
    (kit / "docs" / "specs").mkdir(parents=True, exist_ok=True)
    (kit / "docs" / "artifacts").mkdir(parents=True, exist_ok=True)
    (kit / "docs" / "prompts").mkdir(parents=True, exist_ok=True)
    t = artifact_type.lower()
    (kit / "docs" / "specs" / f"{t}-spec.md").write_text(f"# {artifact_type} spec")
    (kit / "docs" / "artifacts" / f"{t}-template.md").write_text("template")
    (kit / "docs" / "prompts" / f"{t}-prompt.md").write_text("prompt")
    if with_validator:
        (kit / "docs" / "validators").mkdir(parents=True, exist_ok=True)
        (kit / "docs" / "validators" / f"{t}-validator.md").write_text("validate")


_PASS = _json.dumps({
    "status": "PASS", "summary": "ok", "hard_gates": {"g": "PASS"},
    "blocking_issues": [], "warnings": [], "completeness_score": 90,
})
_FAIL = _json.dumps({
    "status": "FAIL", "summary": "no", "hard_gates": {"g": "FAIL"},
    "blocking_issues": [{"gate": "g", "description": "x", "location": "y"}],
    "warnings": [], "completeness_score": 10,
})


class TestRunArtifact:
    def test_converged(self, tmp_path):
        aieos_root = tmp_path / "aieos"
        aieos_root.mkdir()
        _fake_kit(aieos_root, "PRD")
        (aieos_root / "not-a-kit").mkdir()  # exercises the non-kit skip
        initiative = tmp_path / "init"
        initiative.mkdir()
        driver = HarnessDriver(
            initiative, MockAdapter(),
            MockAdapter(preset_responses={"PRD": _PASS}),
            aieos_root=aieos_root,
        )
        assert driver.run_artifact("PRD") == LifecycleResult.CONVERGED

    def test_escalation(self, tmp_path):
        aieos_root = tmp_path / "aieos"
        aieos_root.mkdir()
        _fake_kit(aieos_root, "PRD", with_validator=True)
        initiative = tmp_path / "init"
        initiative.mkdir()
        driver = HarnessDriver(
            initiative, MockAdapter(),
            MockAdapter(preset_responses={"PRD": _FAIL}),
            max_iterations=2, aieos_root=aieos_root,
        )
        assert driver.run_artifact("PRD") == LifecycleResult.ESCALATION_NEEDED

    def test_requires_aieos_root(self, tmp_path):
        driver = HarnessDriver(tmp_path, MockAdapter(), MockAdapter())
        with pytest.raises(ValueError, match="aieos_root"):
            driver.run_artifact("PRD")

    def test_missing_spec_raises(self, tmp_path):
        aieos_root = tmp_path / "aieos"
        aieos_root.mkdir()
        driver = HarnessDriver(
            tmp_path / "init", MockAdapter(), MockAdapter(), aieos_root=aieos_root
        )
        (tmp_path / "init").mkdir()
        with pytest.raises(ValueError, match="No kit spec"):
            driver.run_artifact("PRD")


class TestFrozenArtifactsAreImmutable:
    """G-13: a FROZEN artifact must never be regenerated over.

    Found by the real-AI dogfood 2026-07-15: the conductor skipped nodes only
    via its own state file and _persist_freeze_pending wrote unconditionally, so
    a converged run over a FROZEN artifact silently destroyed a human approval.
    """

    def _frozen_artifact(self, initiative, artifact_type="PRD"):
        sdlc = initiative / "docs" / "sdlc"
        sdlc.mkdir(parents=True, exist_ok=True)
        slug = initiative.name.upper().replace(" ", "-")
        path = sdlc / f"{artifact_type.lower()}.md"
        path.write_text(
            "## Document Control\n\n"
            f"| Artifact ID | {artifact_type}-{slug}-001 |\n"
            "| Status | FROZEN |\n\n"
            "Human-approved content. Must survive.\n"
        )
        return path

    def test_run_artifact_returns_already_frozen_without_calling_provider(self, tmp_path):
        """The guard must fire BEFORE generation -- free and safe, not just safe."""
        aieos_root = tmp_path / "aieos"
        aieos_root.mkdir()
        _fake_kit(aieos_root, "PRD")
        initiative = tmp_path / "init"
        initiative.mkdir()
        self._frozen_artifact(initiative, "PRD")

        class ExplodingAdapter(MockAdapter):
            def invoke(self, request):  # pragma: no cover - must never run
                raise AssertionError("provider called against a FROZEN artifact")

        driver = HarnessDriver(
            initiative, ExplodingAdapter(), ExplodingAdapter(), aieos_root=aieos_root
        )
        assert driver.run_artifact("PRD") == LifecycleResult.ALREADY_FROZEN

    def test_frozen_content_is_untouched(self, tmp_path):
        aieos_root = tmp_path / "aieos"
        aieos_root.mkdir()
        _fake_kit(aieos_root, "PRD")
        initiative = tmp_path / "init"
        initiative.mkdir()
        path = self._frozen_artifact(initiative, "PRD")
        before = path.read_text()

        driver = HarnessDriver(
            initiative, MockAdapter(),
            MockAdapter(preset_responses={"PRD": _PASS}),
            aieos_root=aieos_root,
        )
        driver.run_artifact("PRD")
        assert path.read_text() == before
        assert read_frozen_artifacts(initiative)[
            f"PRD-{initiative.name.upper()}-001"
        ] == ArtifactStatus.FROZEN

    def test_persist_guard_refuses_and_writes_nothing(self, tmp_path):
        """Defense in depth: even called directly, it must not clobber FROZEN."""
        from src.driver import FrozenArtifactError

        initiative = tmp_path / "init"
        initiative.mkdir()
        path = self._frozen_artifact(initiative, "SAD")
        before = path.read_text()

        driver = HarnessDriver(initiative, MockAdapter(), MockAdapter())
        with pytest.raises(FrozenArtifactError):
            driver._persist_freeze_pending("SAD", "malicious replacement")
        assert path.read_text() == before

    def test_non_frozen_artifact_is_still_regenerated(self, tmp_path):
        """The guard must not over-fire: FREEZE_PENDING is fair game."""
        aieos_root = tmp_path / "aieos"
        aieos_root.mkdir()
        _fake_kit(aieos_root, "PRD")
        initiative = tmp_path / "init"
        initiative.mkdir()
        sdlc = initiative / "docs" / "sdlc"
        sdlc.mkdir(parents=True)
        slug = initiative.name.upper()
        (sdlc / "prd.md").write_text(
            f"| Artifact ID | PRD-{slug}-001 |\n| Status | FREEZE_PENDING |\n"
        )
        driver = HarnessDriver(
            initiative, MockAdapter(),
            MockAdapter(preset_responses={"PRD": _PASS}),
            aieos_root=aieos_root,
        )
        assert driver.run_artifact("PRD") == LifecycleResult.CONVERGED


class TestRunArtifactPersistsFreezePending:
    def test_converged_writes_freeze_pending_artifact(self, tmp_path):
        from src.adapters.converging_mock import ConvergingMockAdapter
        from src.models import ArtifactStatus
        from src.state import read_frozen_artifacts

        aieos_root = tmp_path / "aieos"
        aieos_root.mkdir()
        _fake_kit(aieos_root, "PRD")
        initiative = tmp_path / "init"
        initiative.mkdir()
        mock = ConvergingMockAdapter()
        driver = HarnessDriver(initiative, mock, mock, aieos_root=aieos_root)

        assert driver.run_artifact("PRD") == LifecycleResult.CONVERGED
        # artifact persisted at FREEZE_PENDING (conductor never writes FROZEN)
        arts = read_frozen_artifacts(initiative)
        assert any(v == ArtifactStatus.FREEZE_PENDING for v in arts.values())
        pending_id = next(k for k, v in arts.items() if v == ArtifactStatus.FREEZE_PENDING)
        assert pending_id.startswith("PRD-")
