"""Tests for the seven invariant checks."""

import json
from pathlib import Path

import pytest

from src.models import ConvergenceState, LifecycleEvent
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


# -----------------------------------------------------------------------
# 1. Generation/Validation Separation
# -----------------------------------------------------------------------
class TestGenerationValidationSeparation:
    def test_pass_different_events(self):
        result = check_generation_validation_separation(
            LifecycleEvent.POST_GENERATION, LifecycleEvent.POST_VALIDATION
        )
        assert result.passed is True
        assert result.name == "generation_validation_separation"

    def test_fail_same_event(self):
        result = check_generation_validation_separation(
            LifecycleEvent.POST_GENERATION, LifecycleEvent.POST_GENERATION
        )
        assert result.passed is False


# -----------------------------------------------------------------------
# 2. Freeze Before Promote
# -----------------------------------------------------------------------
class TestFreezeBeforePromote:
    def test_pass_all_upstream_frozen(self, tmp_initiative):
        # TDD requires ACF and SAD -- ACF is frozen, SAD is draft
        # But let's test SAD which requires PRD (frozen)
        result = check_freeze_before_promote(
            tmp_initiative, "SAD", UPSTREAM_DEPENDENCIES
        )
        assert result.passed is True

    def test_fail_upstream_not_frozen(self, tmp_initiative):
        # TDD requires ACF and SAD; SAD is DRAFT
        result = check_freeze_before_promote(
            tmp_initiative, "TDD", UPSTREAM_DEPENDENCIES
        )
        assert result.passed is False
        assert "SAD" in result.reason

    def test_pass_no_dependencies(self, tmp_initiative):
        result = check_freeze_before_promote(
            tmp_initiative, "PRD", UPSTREAM_DEPENDENCIES
        )
        assert result.passed is True


# -----------------------------------------------------------------------
# 3. Human Freeze Decision
# -----------------------------------------------------------------------
class TestHumanFreezeDecision:
    def test_pass_no_auto_freeze(self):
        result = check_human_freeze_decision(auto_freeze_attempted=False)
        assert result.passed is True

    def test_fail_auto_freeze(self):
        result = check_human_freeze_decision(auto_freeze_attempted=True)
        assert result.passed is False


# -----------------------------------------------------------------------
# 4. Bounded Convergence
# -----------------------------------------------------------------------
class TestBoundedConvergence:
    def test_pass_within_bounds(self):
        cs = ConvergenceState(
            artifact_id="PRD-TEST-001",
            artifact_type="PRD",
            max_iterations=3,
            current_iteration=2,
        )
        result = check_bounded_convergence(cs)
        assert result.passed is True

    def test_pass_at_boundary(self):
        cs = ConvergenceState(
            artifact_id="PRD-TEST-001",
            artifact_type="PRD",
            max_iterations=3,
            current_iteration=3,
        )
        result = check_bounded_convergence(cs)
        assert result.passed is True

    def test_fail_exceeds_max(self):
        cs = ConvergenceState(
            artifact_id="PRD-TEST-001",
            artifact_type="PRD",
            max_iterations=3,
            current_iteration=4,
        )
        result = check_bounded_convergence(cs)
        assert result.passed is False


# -----------------------------------------------------------------------
# 5. Validator Output Format
# -----------------------------------------------------------------------
class TestValidatorOutputFormat:
    def test_pass_valid_output(self):
        data = {
            "status": "PASS",
            "summary": "All gates passed.",
            "hard_gates": {"gate1": "PASS"},
            "blocking_issues": [],
            "warnings": [],
            "completeness_score": 95,
        }
        check, result = check_validator_output_format(json.dumps(data))
        assert check.passed is True
        assert result is not None
        assert result.status == "PASS"
        assert result.completeness_score == 95

    def test_fail_invalid_json(self):
        check, result = check_validator_output_format("not json")
        assert check.passed is False
        assert result is None

    def test_fail_missing_fields(self):
        data = {"status": "PASS", "summary": "ok"}
        check, result = check_validator_output_format(json.dumps(data))
        assert check.passed is False
        assert result is None

    def test_fail_invalid_status(self):
        data = {
            "status": "MAYBE",
            "summary": "Unclear.",
            "hard_gates": {},
            "blocking_issues": [],
            "warnings": [],
            "completeness_score": 50,
        }
        check, result = check_validator_output_format(json.dumps(data))
        assert check.passed is False

    def test_fail_suggestion_language(self):
        data = {
            "status": "FAIL",
            "summary": "Consider adding more detail to section 3.",
            "hard_gates": {"gate1": "FAIL"},
            "blocking_issues": [{"gate": "gate1", "description": "missing", "location": "s3"}],
            "warnings": [],
            "completeness_score": 40,
        }
        check, result = check_validator_output_format(json.dumps(data))
        assert check.passed is False
        assert "suggestion" in check.reason.lower()


# -----------------------------------------------------------------------
# 6. Tool-Agnostic Policy
# -----------------------------------------------------------------------
class TestToolAgnosticPolicy:
    def test_pass_clean_content(self):
        result = check_tool_agnostic_policy(
            "This spec defines artifact validation rules for the framework."
        )
        assert result.passed is True

    def test_fail_openai_reference(self):
        result = check_tool_agnostic_policy("Use OpenAI for generation.")
        assert result.passed is False

    def test_fail_claude_reference(self):
        result = check_tool_agnostic_policy("Claude should be used as the primary model.")
        assert result.passed is False

    def test_fail_anthropic_reference(self):
        result = check_tool_agnostic_policy("Anthropic API key required.")
        assert result.passed is False


# -----------------------------------------------------------------------
# 7. Disk-Based State
# -----------------------------------------------------------------------
class TestDiskBasedState:
    def test_pass_both_exist(self, tmp_er_file, tmp_journal_file):
        result = check_disk_based_state(tmp_er_file, tmp_journal_file)
        assert result.passed is True

    def test_fail_er_missing(self, tmp_path, tmp_journal_file):
        result = check_disk_based_state(
            tmp_path / "nonexistent.md", tmp_journal_file
        )
        assert result.passed is False
        assert "ER" in result.reason

    def test_fail_both_missing(self, tmp_path):
        result = check_disk_based_state(
            tmp_path / "er.md", tmp_path / "journal.md"
        )
        assert result.passed is False
        assert "ER" in result.reason
        assert "journal" in result.reason


# -----------------------------------------------------------------------
# UPSTREAM_DEPENDENCIES sanity
# -----------------------------------------------------------------------
class TestUpstreamDependencies:
    def test_prd_has_no_deps(self):
        assert UPSTREAM_DEPENDENCIES["PRD"] == []

    def test_tdd_requires_acf_and_sad(self):
        assert "ACF" in UPSTREAM_DEPENDENCIES["TDD"]
        assert "SAD" in UPSTREAM_DEPENDENCIES["TDD"]

    def test_sad_requires_prd(self):
        assert UPSTREAM_DEPENDENCIES["SAD"] == ["PRD"]


# -----------------------------------------------------------------------
# 8. Authorized Freeze (ADR-0002 companion to human freeze decision)
# -----------------------------------------------------------------------
from src.invariants import check_authorized_freeze  # noqa: E402
from src.models import DecisionOutcome, FreezeGateDecision  # noqa: E402


def _fgd(**kw):
    return FreezeGateDecision(
        artifact_id=kw.get("artifact_id", "SAD-TEST-001"),
        outcome=kw.get("outcome", DecisionOutcome.APPROVE),
        content_hash=kw.get("content_hash", "abc"),
        decided_by=kw.get("decided_by", "Todd Linnertz"),
        auto_freeze_attempted=kw.get("auto_freeze_attempted", False),
    )


class TestAuthorizedFreeze:
    def test_pass_valid_human_decision(self):
        result = check_authorized_freeze(_fgd())
        assert result.passed is True
        assert result.name == "authorized_freeze"

    def test_fail_auto_freeze_attempted(self):
        result = check_authorized_freeze(_fgd(auto_freeze_attempted=True))
        assert result.passed is False
        assert "auto-freeze" in result.reason

    def test_fail_missing_decided_by(self):
        result = check_authorized_freeze(_fgd(decided_by="   "))
        assert result.passed is False
        assert "decided_by" in result.reason

    def test_conductor_freeze_passes(self):
        # A conductor submits a valid record with auto_freeze_attempted False.
        result = check_authorized_freeze(_fgd(decided_by="dark-factory-operator"))
        assert result.passed is True
