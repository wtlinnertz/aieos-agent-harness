"""Tests for the referential check tier (FR-012; FR-014 build spec section 4h, step A).

Fixtures are synthetic SAD fragments shaped like the EEK gold cases, kept
inline so this repo does not depend on another repo's files.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.calibration import GoldCase, run_calibration
from src.convergence import ConvergenceLoop
from src.models import (
    AgentRequest,
    AgentResponse,
    LifecycleEvent,
    ValidationResult,
)
from src.referential_checks import (
    TIER,
    ReferentialFinding,
    apply_referential_findings,
    check_deferred_placeholders,
    check_upstream_ids,
    run_referential_checks,
)

UPSTREAM = {
    "PRD-EX-001": "# PRD\n...",
    "ACF-EX-001": "# ACF\n...",
}

DEFERRED_OK = """\
## 11. Deferred Decisions (Explicit)

| Decision | Reason | Target Resolution Phase |
|----------|--------|------------------------|
| Storage technology selection | Not an architectural concern | TDD |
| Specific metrics tooling | Platform-dependent | TDD |

## 12. Risks and Assumptions
- TBD is fine outside the deferred section
"""

DEFERRED_TBD_TARGET = """\
## 11. Deferred Decisions (Explicit)

| Decision | Reason | Target Resolution Phase |
|----------|--------|------------------------|
| Storage technology selection | Still under discussion | TBD |
"""

DEFERRED_TODO_REASON = """\
## 11. Deferred Decisions

| Decision | Reason | Target Resolution Phase |
|---|---|---|
| Cache strategy | TODO | TDD |
"""

DEFERRED_EMPTY_TARGET = """\
## 11. Deferred Decisions

| Decision | Reason | Target Resolution Phase |
|---|---|---|
| Cache strategy | Waiting on load test results |  |
"""


def _sad(body: str, cites: str = "PRD-EX-001 and ACF-EX-001") -> str:
    return f"# SAD\n\n## 0. Document Control\n- Upstream: {cites}\n\n{body}"


def _result(gates: dict[str, str], issues: list[dict] | None = None) -> ValidationResult:
    status = "PASS" if all(v == "PASS" for v in gates.values()) else "FAIL"
    return ValidationResult(
        status=status,
        summary="judge summary",
        hard_gates=gates,
        blocking_issues=issues or [],
        warnings=[],
        completeness_score=90,
    )


# ---------------------------------------------------------------------------
# R2: upstream ID presence
# ---------------------------------------------------------------------------


class TestCheckUpstreamIds:
    def test_check_upstream_ids_both_ids_cited_returns_no_findings(self):
        artifact = _sad(DEFERRED_OK)

        findings = check_upstream_ids(artifact, UPSTREAM)

        assert findings == []

    def test_check_upstream_ids_prd_missing_returns_traceability_finding(self):
        artifact = _sad(DEFERRED_OK, cites="standard platform guardrails (ACF-EX-001)")

        findings = check_upstream_ids(artifact, UPSTREAM)

        assert [(f.gate, f.check) for f in findings] == [
            ("upstream_traceability", "R2-upstream-id")
        ]
        assert "PRD-EX-001" in findings[0].description

    def test_check_upstream_ids_longer_id_does_not_count_as_cited(self):
        artifact = _sad(DEFERRED_OK, cites="PRD-EX-0012 and ACF-EX-001")

        findings = check_upstream_ids(artifact, UPSTREAM)

        assert [f.gate for f in findings] == ["upstream_traceability"]

    def test_check_upstream_ids_ignores_upstream_types_spec_does_not_require(self):
        upstream = {**UPSTREAM, "DCF-EX-001": "# DCF"}
        artifact = _sad(DEFERRED_OK)

        findings = check_upstream_ids(artifact, upstream)

        assert findings == []

    def test_check_upstream_ids_nothing_delivered_returns_no_findings(self):
        artifact = _sad(DEFERRED_OK, cites="nothing")

        findings = check_upstream_ids(artifact, {})

        assert findings == []


# ---------------------------------------------------------------------------
# R3: deferred decision placeholders
# ---------------------------------------------------------------------------


class TestCheckDeferredPlaceholders:
    def test_check_deferred_placeholders_concrete_values_returns_no_findings(self):
        findings = check_deferred_placeholders(_sad(DEFERRED_OK))

        assert findings == []

    def test_check_deferred_placeholders_tbd_target_returns_finding(self):
        findings = check_deferred_placeholders(_sad(DEFERRED_TBD_TARGET))

        assert [(f.gate, f.check) for f in findings] == [
            ("deferred_decisions", "R3-deferred-placeholder")
        ]
        assert "Storage technology selection" in findings[0].description

    def test_check_deferred_placeholders_todo_reason_returns_finding(self):
        findings = check_deferred_placeholders(_sad(DEFERRED_TODO_REASON))

        assert [f.gate for f in findings] == ["deferred_decisions"]

    def test_check_deferred_placeholders_empty_target_returns_finding(self):
        findings = check_deferred_placeholders(_sad(DEFERRED_EMPTY_TARGET))

        assert [f.gate for f in findings] == ["deferred_decisions"]

    def test_check_deferred_placeholders_no_section_returns_no_findings(self):
        findings = check_deferred_placeholders(_sad("## 1. Intent\nTBD everywhere"))

        assert findings == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRunReferentialChecks:
    def test_run_referential_checks_sad_runs_both_checks(self):
        artifact = _sad(DEFERRED_TBD_TARGET, cites="ACF-EX-001 only")

        findings = run_referential_checks("SAD", artifact, UPSTREAM)

        assert sorted(f.gate for f in findings) == [
            "deferred_decisions",
            "upstream_traceability",
        ]

    def test_run_referential_checks_type_is_case_insensitive(self):
        artifact = _sad(DEFERRED_TBD_TARGET)

        findings = run_referential_checks("sad", artifact, UPSTREAM)

        assert [f.gate for f in findings] == ["deferred_decisions"]

    def test_run_referential_checks_unregistered_type_returns_empty(self):
        artifact = _sad(DEFERRED_TBD_TARGET, cites="nothing")

        findings = run_referential_checks("PRD", artifact, UPSTREAM)

        assert findings == []


# ---------------------------------------------------------------------------
# Combining rule
# ---------------------------------------------------------------------------


class TestApplyReferentialFindings:
    FINDING = ReferentialFinding(
        gate="deferred_decisions",
        check="R3-deferred-placeholder",
        description="placeholder",
        location="Deferred Decisions",
    )

    def test_apply_finding_on_judge_pass_fails_gate_and_status(self):
        result = _result({"deferred_decisions": "PASS", "scope_discipline": "PASS"})

        combined = apply_referential_findings(result, [self.FINDING])

        assert combined.status == "FAIL"
        assert combined.hard_gates == {
            "deferred_decisions": "FAIL",
            "scope_discipline": "PASS",
        }

    def test_apply_finding_adds_tiered_issue_the_correction_loop_can_read(self):
        result = _result({"deferred_decisions": "PASS"})

        combined = apply_referential_findings(result, [self.FINDING])

        assert combined.blocking_issues == [
            {
                "gate": "deferred_decisions",
                "description": "placeholder",
                "location": "Deferred Decisions",
                "tier": TIER,
                "check": "R3-deferred-placeholder",
            }
        ]

    def test_apply_finding_does_not_mutate_judge_result(self):
        result = _result({"deferred_decisions": "PASS"})

        apply_referential_findings(result, [self.FINDING])

        assert result.hard_gates == {"deferred_decisions": "PASS"}
        assert result.blocking_issues == []

    def test_apply_no_findings_keeps_judge_fail(self):
        issue = {"gate": "deferred_decisions", "description": "judge", "location": "s11"}
        result = _result({"deferred_decisions": "FAIL"}, [issue])

        combined = apply_referential_findings(result, [])

        assert combined.status == "FAIL"
        assert combined.hard_gates == {"deferred_decisions": "FAIL"}
        assert combined.blocking_issues == [issue]


# ---------------------------------------------------------------------------
# Wiring: production loop and calibration run the same checks
# ---------------------------------------------------------------------------

ALL_PASS = {
    "upstream_traceability": "PASS",
    "deferred_decisions": "PASS",
    "scope_discipline": "PASS",
}


def _judge_json(gates: dict[str, str]) -> str:
    return json.dumps(
        {
            "status": "PASS",
            "summary": "looks fine",
            "hard_gates": gates,
            "blocking_issues": [],
            "warnings": [],
            "completeness_score": 95,
        }
    )


class _Fixed:
    """Adapter fake: returns the same content on every call."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[AgentRequest] = []

    provider_name = "fixed"
    model_name = "fixed-v1"

    def invoke(self, request: AgentRequest) -> AgentResponse:
        self.calls.append(request)
        return AgentResponse(
            content=self._content,
            provider="fixed",
            model="fixed-v1",
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.0,
            latency_ms=1.0,
        )

    def health(self):  # pragma: no cover - protocol filler
        return None

    def cost_estimate(self, request: AgentRequest) -> float:  # pragma: no cover
        return 0.0


def _request(event: LifecycleEvent) -> AgentRequest:
    return AgentRequest(
        artifact_type="SAD",
        event=event,
        spec_content="spec",
        template_content="template",
        prompt_content="prompt",
        upstream_artifacts=dict(UPSTREAM),
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-EX-001"},
    )


class TestWiring:
    def test_convergence_loop_judge_pass_with_tbd_records_referential_fail(self):
        gen = _Fixed(_sad(DEFERRED_TBD_TARGET))
        val = _Fixed(_judge_json(ALL_PASS))
        loop = ConvergenceLoop(gen, val, max_iterations=1)

        _, result, state = loop.run(
            _request(LifecycleEvent.PRE_GENERATION),
            _request(LifecycleEvent.PRE_VALIDATION),
        )

        assert result.status == "FAIL"
        assert state.ledger[0]["hard_gates"]["deferred_decisions"] == "FAIL"
        assert state.ledger[0]["blocking_issues"][0]["tier"] == TIER

    def test_calibration_judge_pass_with_missing_prd_scores_referential_fail(self):
        content = _sad(DEFERRED_OK, cites="ACF-EX-001 only")
        case = GoldCase(
            case_id="mutant-upstream",
            artifact_type="SAD",
            validator="sad-validator",
            input_path=Path("inputs/mutant-upstream.md"),
            input_sha256="0" * 64,
            expected_gates={"upstream_traceability": "FAIL"},
            spec_exemption_case=False,
            labeled_by="synthetic",
            labeled_date="2026-11-16",
            source="synthetic test fixture",
            input_content=content,
        )
        judge = _Fixed(_judge_json(ALL_PASS))

        runs = run_calibration(
            [case], judge, validator_prompt="prompt", upstream_artifacts=UPSTREAM
        )

        verdicts = runs.case_runs[0].gate_verdicts
        assert [v["upstream_traceability"] for v in verdicts] == ["FAIL"] * 3
        evidence = runs.case_runs[0].run_evidence[0]
        assert evidence["blocking_issues"][0]["tier"] == TIER
