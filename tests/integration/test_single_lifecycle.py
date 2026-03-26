"""Integration test: full artifact lifecycle with mock provider.

Covers generation, validation, state updates, observability, and invariant checks
through a complete SAD artifact lifecycle using mock adapters.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.adapters.mock import MockAdapter
from src.convergence import parse_validation_result
from src.invariants import (
    UPSTREAM_DEPENDENCIES,
    check_bounded_convergence,
    check_disk_based_state,
    check_freeze_before_promote,
    check_generation_validation_separation,
    check_human_freeze_decision,
    check_tool_agnostic_policy,
    check_validator_output_format,
)
from src.lifecycle import EventBinding, LifecycleBinder
from src.models import (
    AgentRequest,
    ArtifactStatus,
    ConvergenceState,
    InvocationRecord,
    LifecycleEvent,
    RoutingStrategy,
)
from src.observability import ObservabilityLayer
from src.state import (
    append_journal_entry,
    read_er_state_block,
    read_frozen_artifacts,
    write_er_state_block,
)

# ---------------------------------------------------------------------------
# Fixture content
# ---------------------------------------------------------------------------

FIXTURE_ER = """\
# Engagement Record: ER-INTEG-001

## 1a. Initiative Summary

| Field | Value |
|-------|-------|
| Initiative | INTEG-001 |
| Description | Integration test initiative |

## 1b. Current State

| Field | Value |
|-------|-------|
| Current Layer | Layer 4 (EEK) |
| Current Artifact | SAD-INTEG-001 |
| Current Step | Generation |
| Frozen Count | 2 |
| Next Action | Generate SAD |
| Blocking On | None |
| Last Updated | 2026-03-25T10:00:00Z |

## 2. Layer 2 Artifacts

| Artifact | ID | Status |
|----------|----|--------|
| PRD | PRD-INTEG-001 | FROZEN |
"""

FIXTURE_JOURNAL = """\
# Sherpa Journal: INTEG-001
"""

FIXTURE_SPEC = """\
# SAD Specification

## Hard Gates
1. component_identified — At least one major component defined
2. dependency_map — All component dependencies documented
"""

FIXTURE_TEMPLATE = """\
# System Architecture Document

## Document Control
| Field | Value |
|-------|-------|
| Artifact ID | SAD-INTEG-001 |
| Status | DRAFT |

## 1. Major Components
<!-- component table here -->

## 2. Dependencies
<!-- dependency map here -->
"""

FIXTURE_PROMPT = """\
You are an architecture generation agent.
Read the spec and template. Produce a completed SAD.
"""

FIXTURE_SAD_CONTENT = """\
# System Architecture Document

## Document Control
| Field | Value |
|-------|-------|
| Artifact ID | SAD-INTEG-001 |
| Status | DRAFT |

## 1. Major Components

| Component | Responsibility |
|-----------|---------------|
| API Gateway | Request routing and auth |
| Core Service | Business logic |
| Data Store | Persistence layer |

## 2. Dependencies

API Gateway -> Core Service -> Data Store
"""

FIXTURE_VALIDATION_PASS = json.dumps({
    "status": "PASS",
    "summary": "All hard gates satisfied.",
    "hard_gates": {
        "component_identified": "PASS",
        "dependency_map": "PASS",
    },
    "blocking_issues": [],
    "warnings": [],
    "completeness_score": 95,
})


@pytest.fixture()
def workspace(tmp_path: Path):
    """Set up a tmp directory with ER, journal, spec/template/prompt, and 2 frozen upstreams."""
    # ER and journal in engagement dir
    engagement_dir = tmp_path / "docs" / "engagement"
    engagement_dir.mkdir(parents=True)
    er_path = engagement_dir / "er-INTEG-001.md"
    er_path.write_text(FIXTURE_ER)
    journal_path = engagement_dir / "journal-INTEG-001.md"
    journal_path.write_text(FIXTURE_JOURNAL)

    # SDLC artifacts (2 frozen upstreams for SAD: PRD is required)
    sdlc_dir = tmp_path / "docs" / "sdlc"
    sdlc_dir.mkdir(parents=True)

    (sdlc_dir / "01-prd.md").write_text(
        "# PRD\n\n## Document Control\n\n"
        "| Field | Value |\n|-------|-------|\n"
        "| Artifact ID | PRD-INTEG-001 |\n| Status | FROZEN |\n"
    )
    (sdlc_dir / "02-acf.md").write_text(
        "# ACF\n\n## Document Control\n\n"
        "| Field | Value |\n|-------|-------|\n"
        "| Artifact ID | ACF-INTEG-001 |\n| Status | FROZEN |\n"
    )

    # Spec, template, prompt files in a governance-like location
    gov_dir = tmp_path / "governance"
    gov_dir.mkdir()
    (gov_dir / "sad-spec.md").write_text(FIXTURE_SPEC)
    (gov_dir / "sad-template.md").write_text(FIXTURE_TEMPLATE)
    (gov_dir / "sad-prompt.md").write_text(FIXTURE_PROMPT)

    # Observability log
    obs_path = tmp_path / "harness-metrics.jsonl"

    return {
        "root": tmp_path,
        "er_path": er_path,
        "journal_path": journal_path,
        "sdlc_dir": sdlc_dir,
        "gov_dir": gov_dir,
        "obs_path": obs_path,
    }


def _make_gen_request() -> AgentRequest:
    return AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.POST_GENERATION,
        spec_content=FIXTURE_SPEC,
        template_content=FIXTURE_TEMPLATE,
        prompt_content=FIXTURE_PROMPT,
        upstream_artifacts={"PRD-INTEG-001": "frozen PRD content"},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-INTEG-001", "initiative": "INTEG-001"},
    )


def _make_val_request(artifact_content: str) -> AgentRequest:
    return AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.POST_VALIDATION,
        spec_content=FIXTURE_SPEC,
        template_content=FIXTURE_TEMPLATE,
        prompt_content=FIXTURE_PROMPT,
        upstream_artifacts={"PRD-INTEG-001": "frozen PRD content"},
        current_artifact=artifact_content,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-INTEG-001", "initiative": "INTEG-001"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFullLifecycleGeneratesAndValidates:
    """Steps 1-7: generate artifact, validate, confirm no auto-freeze."""

    def test_full_lifecycle_generates_and_validates(self, workspace):
        ws = workspace
        initiative_path = ws["root"]

        # Step 2: Read spec, template, prompt from fixture governance files
        spec = (ws["gov_dir"] / "sad-spec.md").read_text()
        template = (ws["gov_dir"] / "sad-template.md").read_text()
        prompt = (ws["gov_dir"] / "sad-prompt.md").read_text()

        assert "Hard Gates" in spec
        assert "Document Control" in template
        assert "architecture" in prompt

        # Step 3: Check upstream frozen status
        frozen = read_frozen_artifacts(initiative_path)
        assert frozen["PRD-INTEG-001"] == ArtifactStatus.FROZEN
        assert frozen["ACF-INTEG-001"] == ArtifactStatus.FROZEN

        # Step 4: Create lifecycle binder with mock adapter for generation
        gen_adapter = MockAdapter(
            provider_name="gen-provider",
            model_name="gen-model",
            preset_responses={"SAD": FIXTURE_SAD_CONTENT},
        )
        gen_binding = EventBinding(
            event=LifecycleEvent.POST_GENERATION,
            artifact_type="SAD",
            adapter_names=["gen-provider"],
            strategy=RoutingStrategy.FALLBACK,
        )
        binder = LifecycleBinder(
            bindings=[gen_binding],
            adapters={"gen-provider": gen_adapter},
        )

        # Step 5: Generate artifact
        gen_request = _make_gen_request()
        gen_response = binder.execute(LifecycleEvent.POST_GENERATION, gen_request)
        assert gen_response.content == FIXTURE_SAD_CONTENT
        assert gen_response.provider == "gen-provider"

        # Step 6: Create separate mock adapter for validation
        val_adapter = MockAdapter(
            provider_name="val-provider",
            model_name="val-model",
            preset_responses={"SAD": FIXTURE_VALIDATION_PASS},
        )
        val_binding = EventBinding(
            event=LifecycleEvent.POST_VALIDATION,
            artifact_type="SAD",
            adapter_names=["val-provider"],
            strategy=RoutingStrategy.FALLBACK,
        )
        val_binder = LifecycleBinder(
            bindings=[val_binding],
            adapters={"val-provider": val_adapter},
        )

        # Validate in separate invoke call
        val_request = _make_val_request(gen_response.content)
        val_response = val_binder.execute(LifecycleEvent.POST_VALIDATION, val_request)

        # Parse validation result
        result = parse_validation_result(val_response)
        assert result.status == "PASS"
        assert result.completeness_score == 95

        # Step 7: Verify NO auto-freeze
        # The SAD artifact file on disk should still be DRAFT
        sad_file = ws["sdlc_dir"] / "03-sad.md"
        sad_file.write_text(gen_response.content)
        artifacts = read_frozen_artifacts(initiative_path)
        assert artifacts.get("SAD-INTEG-001", ArtifactStatus.DRAFT) != ArtifactStatus.FROZEN


class TestLifecycleUpdatesState:
    """Steps 8-10: ER state block update and journal append."""

    def test_lifecycle_updates_state(self, workspace):
        ws = workspace
        er_path = ws["er_path"]
        journal_path = ws["journal_path"]

        # Step 8: Read current state
        state = read_er_state_block(er_path)
        assert state.current_artifact == "SAD-INTEG-001"
        assert state.frozen_count == 2

        # Update state after validation
        from src.models import ERStateBlock

        new_state = ERStateBlock(
            current_layer="Layer 4 (EEK)",
            current_artifact="SAD-INTEG-001",
            current_step="Validated",
            frozen_count=2,
            next_action="Human freeze decision",
            blocking_on="Human approval",
            last_updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        write_er_state_block(er_path, new_state)

        # Verify write took effect
        updated = read_er_state_block(er_path)
        assert updated.current_step == "Validated"
        assert updated.next_action == "Human freeze decision"
        assert updated.blocking_on == "Human approval"

        # Step 9: Append journal entry
        append_journal_entry(journal_path, "Generation", {
            "Artifact": "SAD-INTEG-001",
            "Event": "POST_GENERATION",
            "Provider": "gen-provider",
            "Result": "success",
        })
        append_journal_entry(journal_path, "Validation", {
            "Artifact": "SAD-INTEG-001",
            "Event": "POST_VALIDATION",
            "Status": "PASS",
            "Score": "95",
        })

        # Step 10: Verify journal entries
        from src.state import read_journal_entries

        entries = read_journal_entries(journal_path)
        # Should have the new entries appended
        gen_entries = [e for e in entries if e.get("entry_type") == "Generation"]
        val_entries = [e for e in entries if e.get("entry_type") == "Validation"]
        assert len(gen_entries) >= 1
        assert gen_entries[-1]["Artifact"] == "SAD-INTEG-001"
        assert len(val_entries) >= 1
        assert val_entries[-1]["Status"] == "PASS"


class TestLifecycleRecordsObservability:
    """Step 11: observability record written."""

    def test_lifecycle_records_observability(self, workspace):
        ws = workspace
        obs = ObservabilityLayer(ws["obs_path"])

        record = InvocationRecord(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            artifact_type="SAD",
            artifact_id="SAD-INTEG-001",
            event=LifecycleEvent.POST_GENERATION,
            provider="gen-provider",
            model="gen-model",
            strategy=RoutingStrategy.FALLBACK,
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.001,
            latency_ms=50.0,
            result="success",
            validation_status=None,
            convergence_iteration=0,
        )
        obs.record(record)

        # Verify record written
        records = obs.read_records()
        assert len(records) == 1
        assert records[0].artifact_id == "SAD-INTEG-001"
        assert records[0].provider == "gen-provider"
        assert records[0].cost_usd == 0.001

        # Record validation invocation too
        val_record = InvocationRecord(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            artifact_type="SAD",
            artifact_id="SAD-INTEG-001",
            event=LifecycleEvent.POST_VALIDATION,
            provider="val-provider",
            model="val-model",
            strategy=RoutingStrategy.FALLBACK,
            tokens_in=150,
            tokens_out=50,
            cost_usd=0.0005,
            latency_ms=30.0,
            result="success",
            validation_status="PASS",
            convergence_iteration=0,
        )
        obs.record(val_record)

        all_records = obs.read_records()
        assert len(all_records) == 2

        summary = obs.cost_summary()
        assert summary["invocation_count"] == 2
        assert summary["total_cost"] == pytest.approx(0.0015, abs=1e-6)


class TestLifecycleInvariantsHold:
    """Step 12: run all 7 invariant checks and verify all pass."""

    def test_lifecycle_invariants_hold(self, workspace):
        ws = workspace
        initiative_path = ws["root"]
        er_path = ws["er_path"]
        journal_path = ws["journal_path"]

        # 1. Generation/validation separation
        check1 = check_generation_validation_separation(
            LifecycleEvent.POST_GENERATION,
            LifecycleEvent.POST_VALIDATION,
        )
        assert check1.passed, check1.reason

        # 2. Freeze-before-promote (SAD requires PRD, which is frozen)
        check2 = check_freeze_before_promote(
            initiative_path, "SAD", UPSTREAM_DEPENDENCIES
        )
        assert check2.passed, check2.reason

        # 3. Human freeze decision (no auto-freeze attempted)
        check3 = check_human_freeze_decision(auto_freeze_attempted=False)
        assert check3.passed, check3.reason

        # 4. Bounded convergence
        cstate = ConvergenceState(
            artifact_id="SAD-INTEG-001",
            artifact_type="SAD",
            max_iterations=3,
            current_iteration=1,
        )
        check4 = check_bounded_convergence(cstate)
        assert check4.passed, check4.reason

        # 5. Validator output format
        check5, vresult = check_validator_output_format(FIXTURE_VALIDATION_PASS)
        assert check5.passed, check5.reason
        assert vresult is not None
        assert vresult.status == "PASS"

        # 6. Tool-agnostic policy (our spec/template/prompt have no provider refs)
        check6 = check_tool_agnostic_policy(FIXTURE_SPEC + FIXTURE_TEMPLATE + FIXTURE_PROMPT)
        assert check6.passed, check6.reason

        # 7. Disk-based state
        check7 = check_disk_based_state(er_path, journal_path)
        assert check7.passed, check7.reason
