"""Tests for R1, the focused non-goal coverage check (FR-014 build spec 4h/4i).

Fixtures are synthetic PRD and SAD fragments shaped like the EEK gold cases,
kept inline so this repo does not depend on another repo's files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.calibration import CalibrationError, GoldCase, run_calibration
from src.convergence import ConvergenceLoop
from src.focused_checks import (
    CHECK_ID,
    TIER,
    non_goal_findings,
    plan_non_goal_checks,
    prd_non_goals,
    run_non_goal_checks,
    sad_non_goals,
)
from src.models import AgentRequest, AgentResponse, LifecycleEvent, ValidationResult
from src.referential_checks import apply_referential_findings

PRD_NUMBERED = """\
# PRD - Reference Data Service

## 0. Document Control
- PRD ID: PRD-EX-001

## 3. Non-Goals (Hard Exclusions)
- No UI or human-facing interface
- No write or mutation capability
- No external exposure outside the internal network

## 8. Out of Scope by Default
- Caching
"""

PRD_PLAIN = """\
# PRD - Internal Link Shortener

## 4. Non-goals

- Public or anonymous link shortening. Internal, authenticated use only.
- Custom vanity domains.

## 5. Requirements
- Shorten links
"""

PRD_NO_SECTION = "# PRD\n\n## 1. Problem\n- Something\n"

ACF = "# ACF\n\n## Non-Goals\n- Not a PRD exclusion\n"

SAD_ALL = """\
Upstream: PRD-EX-001, ACF-EX-001

## 2. Scope

### Explicit Non-Goals
- Data mutation or write operations
- User interface of any kind
- External network exposure

## 3. Layers
- Not a non-goal
"""

SAD_MISSING_WRITE = """\
Upstream: PRD-EX-001, ACF-EX-001

## 2. Scope

### Explicit Non-Goals
- User interface of any kind
- External network exposure

## 3. Layers
"""

SAD_NO_SECTION = "## 2. Scope\n\nNothing here.\n"

UPSTREAM = {"PRD-EX-001": PRD_NUMBERED, "ACF-EX-001": ACF}

# The human-labeled correspondence a correct judge would find.
ORACLE = {
    "No UI or human-facing interface": "User interface of any kind",
    "No write or mutation capability": "Data mutation or write operations",
    "No external exposure outside the internal network": "External network exposure",
}


def _answer_for(request: AgentRequest, sad: str) -> str:
    for exclusion, line in ORACLE.items():
        if exclusion in request.prompt_content:
            return line if line in sad else "NONE"
    raise AssertionError("R1 request names no known exclusion")


def _judge_json(status: str = "PASS") -> str:
    gates = {g: "PASS" for g in ("scope_discipline", "upstream_traceability", "deferred_decisions")}
    return json.dumps(
        {
            "status": status,
            "summary": "judge",
            "hard_gates": gates,
            "blocking_issues": [],
            "warnings": [],
            "completeness_score": 95,
        }
    )


class ScriptedAdapter:
    """Main judge calls get judge JSON; R1 calls get the oracle's answer."""

    provider_name = "scripted"
    model_name = "scripted-v1"

    def __init__(self, sad: str, fail_on_call: int | None = None) -> None:
        self._sad = sad
        self._fail_on_call = fail_on_call
        self.calls: list[AgentRequest] = []

    def invoke(self, request: AgentRequest) -> AgentResponse:
        self.calls.append(request)
        if self._fail_on_call is not None and len(self.calls) == self._fail_on_call:
            raise RuntimeError("provider down")
        if request.metadata.get("check") == CHECK_ID:
            content = _answer_for(request, self._sad)
        elif request.event == LifecycleEvent.PRE_GENERATION:
            content = self._sad
        else:
            content = _judge_json()
        return AgentResponse(
            content=content,
            provider="scripted",
            model="scripted-v1",
            tokens_in=10,
            tokens_out=2,
            cost_usd=0.001,
            latency_ms=1.0,
        )

    def health(self):  # pragma: no cover - protocol filler
        return None

    def cost_estimate(self, request: AgentRequest) -> float:  # pragma: no cover
        return 0.0


class TestParsers:
    def test_numbered_prd_heading_yields_only_its_bullets(self):
        assert prd_non_goals(PRD_NUMBERED) == list(ORACLE)

    def test_plain_prd_heading_with_blank_line(self):
        assert prd_non_goals(PRD_PLAIN) == [
            "Public or anonymous link shortening. Internal, authenticated use only.",
            "Custom vanity domains.",
        ]

    def test_prd_without_non_goals_section_returns_none(self):
        assert prd_non_goals(PRD_NO_SECTION) is None

    def test_sad_explicit_non_goals_bullets(self):
        assert sad_non_goals(SAD_ALL) == [
            "Data mutation or write operations",
            "User interface of any kind",
            "External network exposure",
        ]

    def test_sad_bold_bullets_keep_text(self):
        sad = "### Explicit Non-Goals\n\n- **Custom vanity domains:** none (PRD §4)\n"
        assert sad_non_goals(sad) == ["**Custom vanity domains:** none (PRD §4)"]

    def test_sad_without_section_returns_empty(self):
        assert sad_non_goals(SAD_NO_SECTION) == []


class TestPlan:
    def test_non_sad_type_is_skipped(self):
        plan = plan_non_goal_checks("PRD", SAD_ALL, UPSTREAM, {})
        assert plan.queries == () and plan.skipped

    def test_no_prd_upstream_is_skipped(self):
        plan = plan_non_goal_checks("SAD", SAD_ALL, {"ACF-EX-001": ACF}, {})
        assert plan.queries == () and plan.findings == () and plan.skipped

    def test_prd_without_section_is_skipped(self):
        plan = plan_non_goal_checks("SAD", SAD_ALL, {"PRD-X": PRD_NO_SECTION}, {})
        assert plan.queries == () and plan.findings == () and plan.skipped

    def test_one_pinned_query_per_prd_exclusion(self):
        plan = plan_non_goal_checks("SAD", SAD_ALL, UPSTREAM, {"case_id": "c1"})
        assert plan.skipped is None
        assert [q.exclusion for q in plan.queries] == list(ORACLE)
        for query in plan.queries:
            req = query.request
            assert req.temperature == 0.0
            assert req.metadata["case_id"] == "c1"
            assert req.metadata["check"] == CHECK_ID
            assert req.upstream_artifacts == {}
            assert query.exclusion in req.prompt_content
            assert "User interface of any kind" in (req.current_artifact or "")
            assert "Not a non-goal" not in (req.current_artifact or "")

    def test_sad_without_section_fails_every_exclusion_without_calls(self):
        plan = plan_non_goal_checks("SAD", SAD_NO_SECTION, UPSTREAM, {})
        assert plan.queries == ()
        assert len(plan.findings) == 3
        assert {f.gate for f in plan.findings} == {"scope_discipline"}


class TestFindings:
    def _plan(self):
        return plan_non_goal_checks("SAD", SAD_ALL, UPSTREAM, {})

    def test_none_answer_fails_only_that_exclusion(self):
        findings = non_goal_findings(
            self._plan(),
            ["User interface of any kind", "NONE", "External network exposure"],
        )
        assert len(findings) == 1
        f = findings[0]
        assert (f.gate, f.check, f.tier) == ("scope_discipline", CHECK_ID, TIER)
        assert "No write or mutation capability" in f.description

    def test_quote_with_formatting_noise_matches(self):
        findings = non_goal_findings(
            self._plan(),
            [
                '"- User interface of any kind"',
                "`Data mutation or write operations.`",
                "**External network exposure**",
            ],
        )
        assert findings == []

    @pytest.mark.parametrize("answer", ["NONE.", "none", "None - not restated"])
    def test_none_variants_fail(self, answer):
        findings = non_goal_findings(
            self._plan(), [answer, "Data mutation or write operations", "External network exposure"]
        )
        assert len(findings) == 1

    def test_quote_not_in_sad_fails(self):
        findings = non_goal_findings(
            self._plan(),
            ["No admin console", "Data mutation or write operations", "External network exposure"],
        )
        assert len(findings) == 1
        assert "did not quote" in findings[0].description

    def test_answer_count_mismatch_raises(self):
        with pytest.raises(ValueError):
            non_goal_findings(self._plan(), ["NONE"])

    def test_run_non_goal_checks_invokes_adapter_once_per_query(self):
        adapter = ScriptedAdapter(SAD_MISSING_WRITE)
        plan = plan_non_goal_checks("SAD", SAD_MISSING_WRITE, UPSTREAM, {})
        findings, responses = run_non_goal_checks(plan, adapter)
        assert len(adapter.calls) == 3 and len(responses) == 3
        assert [f.check for f in findings] == [CHECK_ID]


class TestCombining:
    def _result(self, status: str) -> ValidationResult:
        gate = "PASS" if status == "PASS" else "FAIL"
        return ValidationResult(
            status=status,
            summary="judge",
            hard_gates={"scope_discipline": gate},
            blocking_issues=[],
            warnings=[],
            completeness_score=90,
        )

    def test_focused_fail_overrides_judge_pass_and_records_tier(self):
        plan = plan_non_goal_checks("SAD", SAD_NO_SECTION, UPSTREAM, {})
        result = apply_referential_findings(self._result("PASS"), list(plan.findings))
        assert result.status == "FAIL"
        assert result.hard_gates["scope_discipline"] == "FAIL"
        assert {i["tier"] for i in result.blocking_issues} == {TIER}

    def test_no_findings_never_passes_a_judge_fail(self):
        result = apply_referential_findings(self._result("FAIL"), [])
        assert result.status == "FAIL"
        assert result.hard_gates["scope_discipline"] == "FAIL"


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


def _case(content: str) -> GoldCase:
    return GoldCase(
        case_id="baseline",
        artifact_type="SAD",
        validator="sad-validator",
        input_path=Path("inputs/baseline.md"),
        input_sha256="0" * 64,
        expected_gates={"scope_discipline": "FAIL"},
        spec_exemption_case=False,
        labeled_by="synthetic",
        labeled_date="2026-11-26",
        source="synthetic test fixture",
        input_content=content,
    )


class TestConvergenceWiring:
    def test_r1_fail_overrides_judge_pass_and_lands_in_ledger(self):
        gen = ScriptedAdapter(SAD_MISSING_WRITE)
        val = ScriptedAdapter(SAD_MISSING_WRITE)
        loop = ConvergenceLoop(gen, val, max_iterations=1)

        _, result, state = loop.run(
            _request(LifecycleEvent.PRE_GENERATION),
            _request(LifecycleEvent.PRE_VALIDATION),
        )

        assert result.status == "FAIL"
        entry = state.ledger[0]
        assert entry["hard_gates"]["scope_discipline"] == "FAIL"
        assert [i["tier"] for i in entry["blocking_issues"]] == [TIER]
        assert len(val.calls) == 4  # main judge + 3 exclusions
        assert all(c.temperature == 0.0 for c in val.calls)
        assert len(entry["usage"]["focused"]) == 3
        assert entry["focused_checks"] == {"r1_calls": 3, "r1_skipped": None}

    def test_complete_non_goals_pass(self):
        gen = ScriptedAdapter(SAD_ALL)
        val = ScriptedAdapter(SAD_ALL)
        _, result, _ = ConvergenceLoop(gen, val, max_iterations=1).run(
            _request(LifecycleEvent.PRE_GENERATION),
            _request(LifecycleEvent.PRE_VALIDATION),
        )
        assert result.status == "PASS"

    def test_skip_is_recorded(self):
        gen = ScriptedAdapter(SAD_ALL)
        val = ScriptedAdapter(SAD_ALL)
        req_g = _request(LifecycleEvent.PRE_GENERATION)
        req_v = _request(LifecycleEvent.PRE_VALIDATION)
        req_v.upstream_artifacts = {"ACF-EX-001": ACF}
        _, _, state = ConvergenceLoop(gen, val, max_iterations=1).run(req_g, req_v)
        entry = state.ledger[0]
        assert entry["focused_checks"]["r1_calls"] == 0
        assert entry["focused_checks"]["r1_skipped"]
        assert entry["usage"]["focused"] == []


class TestCalibrationWiring:
    def test_r1_fail_scored_every_run_with_case_ids(self):
        judge = ScriptedAdapter(SAD_MISSING_WRITE)
        runs = run_calibration(
            [_case(SAD_MISSING_WRITE)], judge, validator_prompt="p", upstream_artifacts=UPSTREAM
        )
        verdicts = runs.case_runs[0].gate_verdicts
        assert [v["scope_discipline"] for v in verdicts] == ["FAIL"] * 3
        assert len(judge.calls) == 3 * (1 + 3)
        r1_calls = [c for c in judge.calls if c.metadata.get("check") == CHECK_ID]
        assert {c.metadata["case_id"] for c in r1_calls} == {"baseline"}
        evidence = runs.case_runs[0].run_evidence[0]
        assert evidence["focused_checks"]["r1_calls"] == 3
        assert len(evidence["focused_usage"]) == 3

    def test_provider_error_on_r1_call_refuses_with_full_call_count(self):
        judge = ScriptedAdapter(SAD_MISSING_WRITE, fail_on_call=3)
        with pytest.raises(CalibrationError) as err:
            run_calibration(
                [_case(SAD_MISSING_WRITE)], judge, validator_prompt="p", upstream_artifacts=UPSTREAM
            )
        assert err.value.code == "provider_error"
        assert "call 3 of 12" in str(err.value)
        assert "2 completed call(s)" in str(err.value)
