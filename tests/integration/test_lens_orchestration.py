"""Integration tests: multi-persona lens orchestration for PRK review patterns.

Exercises the multi-lens review scenario where a frozen artifact (e.g. SAD)
is reviewed by multiple independent lenses (security, reliability, cost,
operability), each with its own spec, adapter, and convergence loop.

Tests compose behavior from existing primitives — LifecycleBinder,
RoutingEngine, ConvergenceLoop — the same way the sherpa would orchestrate
a peer review.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.adapters.mock import MockAdapter
from src.convergence import ConvergenceLoop, parse_validation_result
from src.lifecycle import EventBinding, LifecycleBinder
from src.models import (
    AgentRequest,
    AgentResponse,
    ConvergenceState,
    HealthStatus,
    InvocationRecord,
    LifecycleEvent,
    RoutingStrategy,
    ValidationResult,
)
from src.observability import ObservabilityLayer
from src.routing import CircuitBreaker, RoutingEngine
from src.state import (
    append_journal_entry,
    read_er_state_block,
    read_journal_entries,
    write_er_state_block,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# PRK lens names used throughout tests
LENS_NAMES = ["security", "reliability", "cost", "operability"]

# Lens-specific spec content (each lens reviews against different criteria)
LENS_SPECS = {
    "security": "# Security Lens Spec\n\nGates: threat_model_coverage, auth_review",
    "reliability": "# Reliability Lens Spec\n\nGates: slo_coverage, failure_mode_analysis",
    "cost": "# Cost Lens Spec\n\nGates: resource_efficiency, cost_projection",
    "operability": "# Operability Lens Spec\n\nGates: runbook_coverage, alert_design",
}


def _pass_validation(score: int = 90) -> str:
    return json.dumps({
        "status": "PASS",
        "summary": "All gates pass",
        "hard_gates": {"gate_1": "PASS", "gate_2": "PASS"},
        "blocking_issues": [],
        "warnings": [],
        "completeness_score": score,
    })


def _fail_validation(score: int = 65, gate: str = "gate_2",
                      description: str = "Missing runbook") -> str:
    return json.dumps({
        "status": "FAIL",
        "summary": "Gate failed",
        "hard_gates": {"gate_1": "PASS", gate: "FAIL"},
        "blocking_issues": [
            {"gate": gate, "description": description, "location": "§4"}
        ],
        "warnings": [],
        "completeness_score": score,
    })


PASS_VALIDATION = _pass_validation()
FAIL_VALIDATION = _fail_validation()


class SequentialMockAdapter:
    """Mock that returns different responses on successive invoke() calls."""

    def __init__(
        self,
        provider_name: str,
        model_name: str,
        responses: list[str],
        cost: float = 0.01,
    ) -> None:
        self._provider = provider_name
        self._model = model_name
        self._responses = responses
        self._call_count = 0
        self._cost = cost
        self.call_history: list[tuple[str, tuple]] = []

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    def invoke(self, request: AgentRequest) -> AgentResponse:
        idx = min(self._call_count, len(self._responses) - 1)
        content = self._responses[idx]
        self._call_count += 1
        self.call_history.append(("invoke", (request,)))
        return AgentResponse(
            content=content,
            provider=self._provider,
            model=self._model,
            tokens_in=100,
            tokens_out=200,
            cost_usd=self._cost,
            latency_ms=100.0,
        )

    def health(self) -> HealthStatus:
        return HealthStatus.OK

    def cost_estimate(self, request: AgentRequest) -> float:
        return self._cost


class RequestCapturingAdapter:
    """Adapter that captures requests for inspection while returning preset content."""

    def __init__(self, provider_name: str, model_name: str, content: str) -> None:
        self._provider = provider_name
        self._model = model_name
        self._content = content
        self.captured_requests: list[AgentRequest] = []
        self.call_history: list[tuple[str, tuple]] = []

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    def invoke(self, request: AgentRequest) -> AgentResponse:
        self.captured_requests.append(request)
        self.call_history.append(("invoke", (request,)))
        return AgentResponse(
            content=self._content,
            provider=self._provider,
            model=self._model,
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.01,
            latency_ms=100.0,
        )

    def health(self) -> HealthStatus:
        return HealthStatus.OK

    def cost_estimate(self, request: AgentRequest) -> float:
        return 0.01


def _make_lens_request(
    lens: str,
    artifact_type: str = "PRR",
    artifact_content: str = "# Frozen SAD content",
) -> AgentRequest:
    """Build an AgentRequest for a specific lens review."""
    return AgentRequest(
        artifact_type=artifact_type,
        event=LifecycleEvent.PRE_VALIDATION,
        spec_content=LENS_SPECS.get(lens, f"# {lens} Spec"),
        template_content=f"# {lens.title()} Review Template",
        prompt_content=f"Review from {lens} perspective.",
        upstream_artifacts={"SAD-TEST-001": artifact_content},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": f"PRR-TEST-001-{lens}", "lens": lens},
    )


def _run_lens(
    lens: str,
    adapter: MockAdapter | SequentialMockAdapter | RequestCapturingAdapter,
    artifact_content: str = "# Frozen SAD content",
) -> AgentResponse:
    """Invoke a single lens adapter with a lens-specific request."""
    request = _make_lens_request(lens, artifact_content=artifact_content)
    return adapter.invoke(request)


def _run_lens_convergence(
    lens: str,
    gen_adapter,
    val_adapter,
    max_iterations: int = 3,
) -> tuple[AgentResponse, ValidationResult, ConvergenceState]:
    """Run a convergence loop for one lens."""
    gen_request = _make_lens_request(lens)
    val_request = _make_lens_request(lens)
    loop = ConvergenceLoop(gen_adapter, val_adapter, max_iterations=max_iterations)
    return loop.run(gen_request, val_request)


# ---------------------------------------------------------------------------
# 1. Independent Context Packages
# ---------------------------------------------------------------------------


class TestIndependentContextPackages:
    """Each lens gets its own context and never sees other lenses' specs or outputs."""

    def test_each_lens_receives_own_spec(self) -> None:
        """Each lens adapter receives a request with its own unique spec."""
        adapters = {}
        for lens in LENS_NAMES:
            adapters[lens] = RequestCapturingAdapter(
                provider_name=f"provider-{lens}",
                model_name="model-v1",
                content=f"Review output for {lens}",
            )

        # Invoke each lens with its own request
        for lens in LENS_NAMES:
            _run_lens(lens, adapters[lens])

        # Verify each adapter received its own spec
        for lens in LENS_NAMES:
            assert len(adapters[lens].captured_requests) == 1
            received_spec = adapters[lens].captured_requests[0].spec_content
            expected_spec = LENS_SPECS[lens]
            assert received_spec == expected_spec, (
                f"{lens} received wrong spec"
            )

        # Cross-check: no two lenses received the same spec
        specs_received = [
            adapters[lens].captured_requests[0].spec_content
            for lens in LENS_NAMES
        ]
        assert len(set(specs_received)) == len(LENS_NAMES)

    def test_no_cross_lens_output_leakage(self) -> None:
        """Output from lens A is not present in lens B's request."""
        lens_a_output = "SECURITY-SPECIFIC-FINDING: SQL injection in auth module"
        adapter_a = RequestCapturingAdapter(
            "provider-security", "model-v1", lens_a_output
        )
        adapter_b = RequestCapturingAdapter(
            "provider-reliability", "model-v1", "Reliability review output"
        )

        # Run lens A first
        _run_lens("security", adapter_a)

        # Run lens B — it should have no knowledge of lens A's output
        _run_lens("reliability", adapter_b)

        req_b = adapter_b.captured_requests[0]
        # Lens B should not contain lens A's output anywhere
        assert lens_a_output not in (req_b.current_artifact or "")
        assert lens_a_output not in str(req_b.upstream_artifacts)
        assert lens_a_output not in str(req_b.correction_constraints)
        assert lens_a_output not in req_b.spec_content
        assert lens_a_output not in req_b.prompt_content

    def test_lenses_run_in_separate_sessions(self) -> None:
        """Each lens adapter receives exactly one invoke() call."""
        adapters = {}
        for lens in LENS_NAMES:
            adapters[lens] = MockAdapter(
                provider_name=f"provider-{lens}",
                model_name="model-v1",
                preset_responses={"PRR": f"Review from {lens}"},
            )

        # Invoke all 4 lenses
        for lens in LENS_NAMES:
            _run_lens(lens, adapters[lens])

        # Each adapter should have exactly 1 invoke call, not 4
        for lens in LENS_NAMES:
            invoke_calls = [
                c for c in adapters[lens].call_history if c[0] == "invoke"
            ]
            assert len(invoke_calls) == 1, (
                f"{lens} adapter had {len(invoke_calls)} invoke calls, expected 1"
            )


# ---------------------------------------------------------------------------
# 2. Mixed Provider Assignment
# ---------------------------------------------------------------------------


class TestMixedProviderAssignment:
    """Different lenses can use different AI providers."""

    def test_security_uses_opus_others_use_sonnet(self) -> None:
        """Security lens uses Opus; reliability, cost, operability use Sonnet."""
        security_adapter = MockAdapter(
            provider_name="anthropic",
            model_name="opus",
            preset_responses={"PRR": "Security review (opus)"},
        )
        other_adapters = {
            lens: MockAdapter(
                provider_name="anthropic",
                model_name="sonnet",
                preset_responses={"PRR": f"{lens} review (sonnet)"},
            )
            for lens in ["reliability", "cost", "operability"]
        }

        security_response = _run_lens("security", security_adapter)
        other_responses = {
            lens: _run_lens(lens, adapter)
            for lens, adapter in other_adapters.items()
        }

        assert security_response.model == "opus"
        for lens, resp in other_responses.items():
            assert resp.model == "sonnet", f"{lens} should use sonnet"

    def test_different_providers_per_lens(self) -> None:
        """Security uses Anthropic Claude, reliability uses OpenAI GPT-4o."""
        security_adapter = MockAdapter(
            provider_name="anthropic",
            model_name="claude-opus",
            preset_responses={"PRR": "Security review"},
        )
        reliability_adapter = MockAdapter(
            provider_name="openai",
            model_name="gpt-4o",
            preset_responses={"PRR": "Reliability review"},
        )

        sec_resp = _run_lens("security", security_adapter)
        rel_resp = _run_lens("reliability", reliability_adapter)

        assert sec_resp.provider == "anthropic"
        assert rel_resp.provider == "openai"
        assert sec_resp.provider != rel_resp.provider


# ---------------------------------------------------------------------------
# 3. Lens Reconvergence
# ---------------------------------------------------------------------------


class TestLensReconvergence:
    """All lens outputs are collected after parallel (or sequential) execution."""

    def test_all_lenses_pass_reconverge_success(self) -> None:
        """4 lenses all return PASS; reconverged result has 4 passes."""
        results = {}
        for lens in LENS_NAMES:
            adapter = MockAdapter(
                provider_name=f"provider-{lens}",
                model_name="model-v1",
                preset_responses={"PRR": _pass_validation()},
            )
            resp = _run_lens(lens, adapter)
            vr = parse_validation_result(resp)
            results[lens] = vr

        assert len(results) == 4
        assert all(r.status == "PASS" for r in results.values())

    def test_partial_failure_identified(self) -> None:
        """3 lenses PASS, 1 lens FAILs; failure is identified by lens name."""
        results = {}
        for lens in LENS_NAMES:
            content = FAIL_VALIDATION if lens == "operability" else PASS_VALIDATION
            adapter = MockAdapter(
                provider_name=f"provider-{lens}",
                model_name="model-v1",
                preset_responses={"PRR": content},
            )
            resp = _run_lens(lens, adapter)
            results[lens] = parse_validation_result(resp)

        passing = [l for l, r in results.items() if r.status == "PASS"]
        failing = [l for l, r in results.items() if r.status == "FAIL"]

        assert len(passing) == 3
        assert len(failing) == 1
        assert failing[0] == "operability"

    def test_reconvergence_preserves_individual_scores(self) -> None:
        """Each lens returns a different completeness_score; all are preserved."""
        scores = {"security": 92, "reliability": 88, "cost": 95, "operability": 71}
        results = {}

        for lens, score in scores.items():
            adapter = MockAdapter(
                provider_name=f"provider-{lens}",
                model_name="model-v1",
                preset_responses={"PRR": _pass_validation(score=score)},
            )
            resp = _run_lens(lens, adapter)
            results[lens] = parse_validation_result(resp)

        for lens, expected_score in scores.items():
            assert results[lens].completeness_score == expected_score, (
                f"{lens} score should be {expected_score}, got "
                f"{results[lens].completeness_score}"
            )


# ---------------------------------------------------------------------------
# 4. Per-Lens Convergence
# ---------------------------------------------------------------------------


class TestPerLensConvergence:
    """A failing lens can re-run without affecting other lenses."""

    def test_one_lens_converges_others_unaffected(self) -> None:
        """Security fails first, corrects on second try. Others pass first time."""
        # Security: generates twice, validates twice (FAIL then PASS)
        sec_gen = SequentialMockAdapter(
            "sec-gen", "opus",
            ["Security review attempt 1", "Security review attempt 2 (corrected)"],
        )
        sec_val = SequentialMockAdapter(
            "sec-val", "opus",
            [FAIL_VALIDATION, PASS_VALIDATION],
        )

        # Other lenses: pass on first try
        other_results = {}
        for lens in ["reliability", "cost", "operability"]:
            gen = MockAdapter(
                provider_name=f"{lens}-gen", model_name="sonnet",
                preset_responses={"PRR": f"{lens} review content"},
            )
            val = MockAdapter(
                provider_name=f"{lens}-val", model_name="sonnet",
                preset_responses={"PRR": PASS_VALIDATION},
            )
            resp, result, state = _run_lens_convergence(lens, gen, val)
            other_results[lens] = (resp, result, state)

        # Run security convergence
        sec_resp, sec_result, sec_state = _run_lens_convergence(
            "security", sec_gen, sec_val
        )

        # Security took 2 iterations
        assert sec_state.current_iteration == 2
        assert sec_result.status == "PASS"
        # Security generator was called twice
        sec_gen_calls = [c for c in sec_gen.call_history if c[0] == "invoke"]
        assert len(sec_gen_calls) == 2

        # Other lenses each took 1 iteration
        for lens, (resp, result, state) in other_results.items():
            assert state.current_iteration == 1, f"{lens} should be iteration 1"
            assert result.status == "PASS", f"{lens} should PASS"

    def test_one_lens_escalates_others_pass(self) -> None:
        """Operability fails 3 times (max). Others pass. Operability shows escalation."""
        # Operability: always fails
        op_gen = SequentialMockAdapter(
            "op-gen", "sonnet",
            ["attempt 1", "attempt 2", "attempt 3"],
        )
        op_val = SequentialMockAdapter(
            "op-val", "sonnet",
            [FAIL_VALIDATION, FAIL_VALIDATION, FAIL_VALIDATION],
        )

        op_resp, op_result, op_state = _run_lens_convergence(
            "operability", op_gen, op_val, max_iterations=3
        )

        # Operability hit max iterations and still FAILs
        assert op_result.status == "FAIL"
        assert op_state.current_iteration == 3
        assert len(op_state.ledger) == 3
        assert all(e["status"] == "FAIL" for e in op_state.ledger)

        # Other lenses pass independently
        for lens in ["security", "reliability", "cost"]:
            gen = MockAdapter(
                provider_name=f"{lens}-gen", model_name="sonnet",
                preset_responses={"PRR": f"{lens} content"},
            )
            val = MockAdapter(
                provider_name=f"{lens}-val", model_name="sonnet",
                preset_responses={"PRR": PASS_VALIDATION},
            )
            resp, result, state = _run_lens_convergence(lens, gen, val)
            assert result.status == "PASS", f"{lens} should PASS independently"
            assert state.current_iteration == 1


# ---------------------------------------------------------------------------
# 5. Pipeline With Tools
# ---------------------------------------------------------------------------


class TestPipelineWithTools:
    """Tool output (e.g. SAST) feeds into AI reviewer via pipeline strategy."""

    def test_sast_output_feeds_ai_reviewer(self) -> None:
        """SAST tool output becomes current_artifact for the AI reviewer."""
        sast_findings = "SAST: 3 findings — XSS in /api/v1, SQLi in /auth, SSRF in /proxy"
        sast_adapter = MockAdapter(
            provider_name="sast-tool",
            model_name="semgrep-v1",
            preset_responses={"PRR": sast_findings},
        )
        ai_adapter = RequestCapturingAdapter(
            provider_name="anthropic",
            model_name="opus",
            content="AI review incorporating SAST findings",
        )

        engine = RoutingEngine()
        request = _make_lens_request("security")

        response = engine.route(
            strategy=RoutingStrategy.PIPELINE,
            adapters=[sast_adapter, ai_adapter],
            request=request,
            config={},
        )

        # AI adapter should have received the SAST output as current_artifact
        assert len(ai_adapter.captured_requests) == 1
        assert ai_adapter.captured_requests[0].current_artifact == sast_findings
        assert response.provider == "anthropic"

    def test_tool_failure_stops_pipeline(self) -> None:
        """When the tool step fails, the AI adapter is never invoked."""
        sast_adapter = MockAdapter(
            provider_name="sast-tool",
            model_name="semgrep-v1",
            should_fail=True,
            failure_message="SAST scanner crashed",
        )
        ai_adapter = RequestCapturingAdapter(
            provider_name="anthropic",
            model_name="opus",
            content="Should never be reached",
        )

        engine = RoutingEngine()
        request = _make_lens_request("security")

        with pytest.raises(RuntimeError, match="Pipeline step 0.*failed"):
            engine.route(
                strategy=RoutingStrategy.PIPELINE,
                adapters=[sast_adapter, ai_adapter],
                request=request,
                config={},
            )

        # AI adapter should never have been called
        assert len(ai_adapter.captured_requests) == 0


# ---------------------------------------------------------------------------
# 6. Lens Fan-Out With Fallback
# ---------------------------------------------------------------------------


class TestLensFanOutWithFallback:
    """Individual lens invocations have their own fallback routing."""

    def test_lens_primary_down_fallback_succeeds(self) -> None:
        """Security primary (Opus) fails; fallback (Sonnet) succeeds."""
        primary = MockAdapter(
            provider_name="anthropic-opus",
            model_name="opus",
            should_fail=True,
            failure_message="Opus rate limited",
        )
        fallback = MockAdapter(
            provider_name="anthropic-sonnet",
            model_name="sonnet",
            preset_responses={"PRR": "Security review from fallback"},
        )

        engine = RoutingEngine(circuit_breaker=CircuitBreaker())
        request = _make_lens_request("security")

        sec_response = engine.route(
            strategy=RoutingStrategy.FALLBACK,
            adapters=[primary, fallback],
            request=request,
            config={},
        )

        assert sec_response.provider == "anthropic-sonnet"
        assert sec_response.model == "sonnet"

        # Reliability lens unaffected — uses its own adapter
        rel_adapter = MockAdapter(
            provider_name="anthropic-sonnet",
            model_name="sonnet",
            preset_responses={"PRR": "Reliability review"},
        )
        rel_response = _run_lens("reliability", rel_adapter)
        assert rel_response.provider == "anthropic-sonnet"

    def test_lens_both_providers_down_lens_fails(self) -> None:
        """Security primary AND fallback both fail; other lenses still succeed."""
        primary = MockAdapter(
            provider_name="anthropic-opus",
            model_name="opus",
            should_fail=True,
            failure_message="Opus down",
        )
        fallback = MockAdapter(
            provider_name="anthropic-sonnet",
            model_name="sonnet",
            should_fail=True,
            failure_message="Sonnet down",
        )

        engine = RoutingEngine(circuit_breaker=CircuitBreaker())
        request = _make_lens_request("security")

        with pytest.raises(RuntimeError, match="All adapters failed"):
            engine.route(
                strategy=RoutingStrategy.FALLBACK,
                adapters=[primary, fallback],
                request=request,
                config={},
            )

        # Other lenses still succeed independently
        for lens in ["reliability", "cost", "operability"]:
            adapter = MockAdapter(
                provider_name=f"provider-{lens}",
                model_name="sonnet",
                preset_responses={"PRR": f"{lens} review"},
            )
            resp = _run_lens(lens, adapter)
            assert resp.content == f"{lens} review"


# ---------------------------------------------------------------------------
# 7. Full SAD Review Workflow
# ---------------------------------------------------------------------------

FIXTURE_ER = """\
# Engagement Record: ER-TEST-001

## 1a. Initiative Summary

| Field | Value |
|-------|-------|
| Initiative | TEST-001 |
| Description | Test initiative |

## 1b. Current State

| Field | Value |
|-------|-------|
| Current Layer | Layer 14 (PRK) |
| Current Artifact | PRR-TEST-001 |
| Current Step | Multi-lens review |
| Frozen Count | 5 |
| Next Action | Run peer review lenses |
| Blocking On | None |
| Last Updated | 2026-03-25T10:00:00Z |
"""

FIXTURE_JOURNAL = """\
# Sherpa Journal: TEST-001
"""

FIXTURE_SAD = """\
# Solution Architecture Document

## Document Control

| Field | Value |
|-------|-------|
| Artifact ID | SAD-TEST-001 |
| Status | FROZEN |

## Architecture

Three-tier web application with API gateway, service layer, and PostgreSQL.
"""


class TestFullSADReviewWorkflow:
    """End-to-end: frozen SAD -> multi-lens review -> reconvergence -> state update."""

    def test_full_sad_review_lifecycle(self, tmp_path: Path) -> None:
        # Step 1: Set up fixture files on disk
        er_path = tmp_path / "er-TEST-001.md"
        er_path.write_text(FIXTURE_ER)

        journal_path = tmp_path / "journal-TEST-001.md"
        journal_path.write_text(FIXTURE_JOURNAL)

        sdlc_dir = tmp_path / "docs" / "sdlc"
        sdlc_dir.mkdir(parents=True)
        (sdlc_dir / "03-sad.md").write_text(FIXTURE_SAD)

        # Step 2: Create 3 lens generation adapters with preset PASS-worthy content
        review_lenses = ["security", "reliability", "operability"]
        gen_adapters = {}
        val_adapters = {}
        for lens in review_lenses:
            gen_adapters[lens] = MockAdapter(
                provider_name=f"{lens}-gen",
                model_name="sonnet",
                preset_responses={"PRR": f"# {lens.title()} Review\n\nAll clear."},
            )
            # Step 3: Create 3 lens validators with PASS responses
            val_adapters[lens] = MockAdapter(
                provider_name=f"{lens}-val",
                model_name="sonnet",
                preset_responses={"PRR": PASS_VALIDATION},
            )

        # Step 4: Run all lenses (sequential simulation of parallel execution)
        lens_results = {}
        for lens in review_lenses:
            resp, result, state = _run_lens_convergence(
                lens, gen_adapters[lens], val_adapters[lens]
            )
            lens_results[lens] = {
                "response": resp,
                "validation": result,
                "state": state,
            }

        # Step 5 + 6: Verify all lenses passed
        for lens in review_lenses:
            assert lens_results[lens]["validation"].status == "PASS", (
                f"{lens} should PASS"
            )

        all_pass = all(
            lr["validation"].status == "PASS" for lr in lens_results.values()
        )
        assert all_pass

        # Step 7: Update ER state block
        from src.models import ERStateBlock

        new_state = ERStateBlock(
            current_layer="Layer 14 (PRK)",
            current_artifact="PRR-TEST-001",
            current_step="Review complete",
            frozen_count=5,
            next_action="Present results for human freeze decision",
            blocking_on="Human decision",
            last_updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        write_er_state_block(er_path, new_state)

        # Step 8: Append journal entry with all lens results
        lens_summary = "; ".join(
            f"{l}={lr['validation'].status}({lr['validation'].completeness_score})"
            for l, lr in lens_results.items()
        )
        append_journal_entry(journal_path, "Multi-Lens Review", {
            "Artifact": "PRR-TEST-001",
            "Lenses": ", ".join(review_lenses),
            "Results": lens_summary,
            "Overall": "ALL_PASS",
        })

        # Step 9: Verify ER state updated
        updated_state = read_er_state_block(er_path)
        assert updated_state.next_action == "Present results for human freeze decision"
        assert updated_state.blocking_on == "Human decision"

        # Verify journal has the entry
        entries = read_journal_entries(journal_path)
        review_entries = [
            e for e in entries if e["entry_type"] == "Multi-Lens Review"
        ]
        assert len(review_entries) == 1
        assert "PRR-TEST-001" in review_entries[0]["Artifact"]
        assert "ALL_PASS" in review_entries[0]["Overall"]

        # Verify no auto-freeze: frozen count unchanged
        assert updated_state.frozen_count == 5


# ---------------------------------------------------------------------------
# 8. Observability Per Lens
# ---------------------------------------------------------------------------


class TestObservabilityPerLens:
    """Per-lens invocation records are tracked separately."""

    def test_each_lens_has_separate_observability_record(
        self, tmp_path: Path
    ) -> None:
        """4 lenses produce 4 separate invocation records."""
        log_path = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log_path)

        for i, lens in enumerate(LENS_NAMES):
            record = InvocationRecord(
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                artifact_type="PRR",
                artifact_id=f"PRR-TEST-001-{lens}",
                event=LifecycleEvent.POST_VALIDATION,
                provider=f"provider-{lens}",
                model="sonnet",
                strategy=RoutingStrategy.FALLBACK,
                tokens_in=100,
                tokens_out=200,
                cost_usd=0.01,
                latency_ms=100.0 + i * 10,
                result="success",
                validation_status="PASS",
                convergence_iteration=1,
            )
            obs.record(record)

        records = obs.read_records()
        assert len(records) == 4

        # Each record has a unique artifact_id reflecting the lens
        artifact_ids = {r.artifact_id for r in records}
        assert len(artifact_ids) == 4
        for lens in LENS_NAMES:
            assert f"PRR-TEST-001-{lens}" in artifact_ids

        # Each record has a different provider
        providers = {r.provider for r in records}
        assert len(providers) == 4

    def test_cost_tracked_per_lens_per_provider(self, tmp_path: Path) -> None:
        """Security lens (Opus, $0.05) + 3 others (Sonnet, $0.01 each) = $0.08 total."""
        log_path = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log_path)

        costs = {
            "security": ("anthropic-opus", 0.05),
            "reliability": ("anthropic-sonnet", 0.01),
            "cost": ("anthropic-sonnet", 0.01),
            "operability": ("anthropic-sonnet", 0.01),
        }

        for lens, (provider, cost) in costs.items():
            record = InvocationRecord(
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                artifact_type="PRR",
                artifact_id=f"PRR-TEST-001-{lens}",
                event=LifecycleEvent.POST_VALIDATION,
                provider=provider,
                model="sonnet" if "sonnet" in provider else "opus",
                strategy=RoutingStrategy.FALLBACK,
                tokens_in=100,
                tokens_out=200,
                cost_usd=cost,
                latency_ms=100.0,
                result="success",
                validation_status="PASS",
                convergence_iteration=1,
            )
            obs.record(record)

        summary = obs.cost_summary()
        assert summary["total_cost"] == pytest.approx(0.08, abs=1e-6)
        assert summary["invocation_count"] == 4
        assert summary["cost_by_provider"]["anthropic-opus"] == pytest.approx(
            0.05, abs=1e-6
        )
        assert summary["cost_by_provider"]["anthropic-sonnet"] == pytest.approx(
            0.03, abs=1e-6
        )


# ---------------------------------------------------------------------------
# Additional edge-case coverage
# ---------------------------------------------------------------------------


class TestLensOrchestrationEdgeCases:
    """Supplementary edge-case tests for lens orchestration patterns."""

    def test_parallel_fan_out_with_thread_pool(self) -> None:
        """Fan-out via ThreadPoolExecutor preserves independence."""
        adapters = {
            lens: MockAdapter(
                provider_name=f"provider-{lens}",
                model_name="model-v1",
                preset_responses={"PRR": _pass_validation(score=80 + i * 5)},
            )
            for i, lens in enumerate(LENS_NAMES)
        }

        results = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(_run_lens, lens, adapters[lens]): lens
                for lens in LENS_NAMES
            }
            for future in as_completed(futures):
                lens = futures[future]
                resp = future.result()
                results[lens] = parse_validation_result(resp)

        assert len(results) == 4
        assert all(r.status == "PASS" for r in results.values())
        # Verify distinct scores preserved through parallel execution
        scores = [r.completeness_score for r in results.values()]
        assert len(set(scores)) == 4

    def test_lifecycle_binder_resolves_per_lens_bindings(self) -> None:
        """LifecycleBinder correctly routes different artifact types to different adapters."""
        sec_adapter = MockAdapter(
            provider_name="security-provider", model_name="opus",
            preset_responses={"PRR-security": "Security review"},
        )
        rel_adapter = MockAdapter(
            provider_name="reliability-provider", model_name="sonnet",
            preset_responses={"PRR-reliability": "Reliability review"},
        )

        adapters = {
            "security-provider": sec_adapter,
            "reliability-provider": rel_adapter,
        }
        bindings = [
            EventBinding(
                event=LifecycleEvent.PRE_VALIDATION,
                artifact_type="PRR-security",
                adapter_names=["security-provider"],
                strategy=RoutingStrategy.FALLBACK,
            ),
            EventBinding(
                event=LifecycleEvent.PRE_VALIDATION,
                artifact_type="PRR-reliability",
                adapter_names=["reliability-provider"],
                strategy=RoutingStrategy.FALLBACK,
            ),
        ]

        binder = LifecycleBinder(bindings, adapters)

        # Security resolves to security adapter
        sec_resolved = binder.resolve(
            LifecycleEvent.PRE_VALIDATION, "PRR-security"
        )
        assert len(sec_resolved) == 1
        assert sec_resolved[0][0].provider_name == "security-provider"

        # Reliability resolves to reliability adapter
        rel_resolved = binder.resolve(
            LifecycleEvent.PRE_VALIDATION, "PRR-reliability"
        )
        assert len(rel_resolved) == 1
        assert rel_resolved[0][0].provider_name == "reliability-provider"

    def test_convergence_correction_constraints_are_lens_specific(self) -> None:
        """When security lens fails, its correction constraints don't leak to other lenses."""
        sec_gen = SequentialMockAdapter(
            "sec-gen", "opus",
            ["attempt 1", "attempt 2 corrected"],
        )
        sec_val = SequentialMockAdapter(
            "sec-val", "opus",
            [
                _fail_validation(
                    gate="threat_model",
                    description="Threat model missing STRIDE analysis",
                ),
                PASS_VALIDATION,
            ],
        )

        sec_resp, sec_result, sec_state = _run_lens_convergence(
            "security", sec_gen, sec_val
        )

        # Security's second generation request should have correction constraints
        assert len(sec_gen.call_history) == 2
        second_request: AgentRequest = sec_gen.call_history[1][1][0]
        assert len(second_request.correction_constraints) > 0
        assert any(
            "STRIDE" in c for c in second_request.correction_constraints
        )

        # Now run reliability — it starts fresh with no constraints
        rel_gen = MockAdapter(
            provider_name="rel-gen", model_name="sonnet",
            preset_responses={"PRR": "Reliability review"},
        )
        rel_val = MockAdapter(
            provider_name="rel-val", model_name="sonnet",
            preset_responses={"PRR": PASS_VALIDATION},
        )

        rel_resp, rel_result, rel_state = _run_lens_convergence(
            "reliability", rel_gen, rel_val
        )

        # Reliability's request should have NO correction constraints
        rel_request: AgentRequest = rel_gen.call_history[0][1][0]
        assert len(rel_request.correction_constraints) == 0
        assert rel_result.status == "PASS"
