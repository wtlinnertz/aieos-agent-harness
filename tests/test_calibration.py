"""FR-014 slice 2 — judge-calibration engine (Phase 1: tests).

WHY THIS FILE EXISTS. Slice 1 made judge verdicts deterministic (temperature
0) and traceable (prompt sha + provider + model on every ledger entry). This
slice measures the judge those verdicts came from: run it 3 times per
human-labeled gold case, score agreement at the GATE level, and refuse to
bless a judge that false-PASSes a gold-FAIL gate or flips a gate across
identical runs (G-9 receipts 1 and 2 as standing tests).

Contracts under test mirror the ratified schema records:
- aieos-schema/schema/gold-case.yaml         (loader + activation floor)
- aieos-schema/schema/calibration-report.yaml (scoring + thresholds + verdict)
- aieos-schema/schema/calibration-lock.yaml   (lock write + pure staleness check)

No real LLM anywhere: the judge is a scripted fake adapter returning
per-gate verdicts per call (converging_mock precedent). Static gold fixtures
with real sha256 pins live under tests/fixtures/gold/sad/; scenario-shaped
sets are built in tmp_path.

Run: pytest tests/test_calibration.py
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

import src.calibration as calibration
from src.calibration import (
    CALIBRATION_LOCK_VERSION,
    FLOOR_MIN_CASES,
    FLOOR_MIN_FAIL_CASES,
    FLOOR_MIN_PASS_CASES,
    FLOOR_MIN_SPEC_EXEMPTION_CASES,
    REPORT_VERSION,
    RUNS_PER_CASE,
    THRESHOLDS,
    CalibrationError,
    check_lock,
    load_gold_set,
    run_calibration,
    score,
    write_lock,
    write_report,
)
from src.models import AgentRequest, AgentResponse, HealthStatus, LifecycleEvent

REPO_ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_SRC = REPO_ROOT / "src" / "calibration.py"
STATIC_GOLD = Path(__file__).resolve().parent / "fixtures" / "gold" / "sad"

VALIDATOR_PROMPT = "sad validator prompt v1"
VALIDATOR_SHA = hashlib.sha256(VALIDATOR_PROMPT.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Fakes and fixture builders
# ---------------------------------------------------------------------------


class ScriptedJudgeAdapter:
    """AgentAdapter fake returning scripted per-gate verdicts per call.

    ``script`` maps case_id -> list of per-run gate dicts (one dict per run,
    indexed by how many times that case has been judged). Cases absent from
    the script get ``default_gates``. The case identity travels on
    ``request.metadata["case_id"]`` — the seam run_calibration provides.
    """

    def __init__(
        self,
        script: dict[str, list[dict[str, str]]] | None = None,
        *,
        default_gates: dict[str, str] | None = None,
        provider_name: str = "scripted",
        model_name: str = "scripted-judge-v1",
    ) -> None:
        self._script = script or {}
        self._default_gates = default_gates or {"g1": "PASS"}
        self._counts: dict[str, int] = {}
        self._provider_name = provider_name
        self._model_name = model_name
        self.calls: list[AgentRequest] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def invoke(self, request: AgentRequest) -> AgentResponse:
        self.calls.append(request)
        case_id = request.metadata.get("case_id", "")
        runs = self._script.get(case_id)
        idx = self._counts.get(case_id, 0)
        self._counts[case_id] = idx + 1
        gates = dict(runs[idx % len(runs)]) if runs else dict(self._default_gates)
        status = "PASS" if all(v == "PASS" for v in gates.values()) else "FAIL"
        content = json.dumps(
            {
                "status": status,
                "summary": f"scripted verdict for {case_id}",
                "hard_gates": gates,
                "blocking_issues": [
                    {"gate": g, "description": "scripted failure", "location": "s1"}
                    for g, v in gates.items()
                    if v == "FAIL"
                ],
                "warnings": [],
                "completeness_score": 90 if status == "PASS" else 40,
            }
        )
        return AgentResponse(
            content=content,
            provider=self._provider_name,
            model=self._model_name,
            tokens_in=10,
            tokens_out=20,
            cost_usd=0.0,
            latency_ms=1.0,
        )

    def health(self) -> HealthStatus:
        return HealthStatus.OK

    def cost_estimate(self, request: AgentRequest) -> float:
        return 0.0


def _case_yaml(
    case_id: str,
    sha: str,
    expected_gates: dict[str, str],
    spec_exemption: bool,
    input_rel: str,
) -> str:
    gates = "\n".join(f"  {g}: {v}" for g, v in expected_gates.items())
    return (
        f"case_id: {case_id}\n"
        "artifact_type: SAD\n"
        "validator: sad-validator\n"
        f"input_path: {input_rel}\n"
        f"input_sha256: {sha}\n"
        f"expected_gates:\n{gates}\n"
        f"spec_exemption_case: {'true' if spec_exemption else 'false'}\n"
        "labeled_by: Todd Linnertz\n"
        "labeled_date: 2026-08-14\n"
        "source: synthetic tmp fixture (FR-014 slice 2 tests)\n"
    )


def write_case(
    gold_dir: Path,
    case_id: str,
    expected_gates: dict[str, str],
    *,
    spec_exemption: bool = False,
    content: str | None = None,
    sha: str | None = None,
) -> None:
    inputs = gold_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    text = content if content is not None else f"# {case_id}\n\ninput for {case_id}\n"
    (inputs / f"{case_id}.md").write_bytes(text.encode("utf-8"))
    digest = sha or hashlib.sha256(text.encode("utf-8")).hexdigest()
    (gold_dir / f"{case_id}.yaml").write_bytes(
        _case_yaml(
            case_id, digest, expected_gates, spec_exemption, f"inputs/{case_id}.md"
        ).encode("utf-8")
    )


def build_gold_set(
    gold_dir: Path,
    *,
    gates: tuple[str, ...] = ("g1",),
    n_pass: int = 9,
    n_exemption: int = 2,
    n_fail: int = 1,
) -> list[str]:
    """A floor-satisfying gold set by default: 9 PASS + 2 exemption + 1 FAIL."""
    ids: list[str] = []
    for i in range(n_pass):
        cid = f"pass-{i:03d}"
        write_case(gold_dir, cid, {g: "PASS" for g in gates})
        ids.append(cid)
    for i in range(n_exemption):
        cid = f"exemption-{i:03d}"
        write_case(gold_dir, cid, {g: "PASS" for g in gates}, spec_exemption=True)
        ids.append(cid)
    for i in range(n_fail):
        cid = f"fail-{i:03d}"
        eg = {g: "PASS" for g in gates}
        eg[gates[0]] = "FAIL"
        write_case(gold_dir, cid, eg)
        ids.append(cid)
    return ids


def perfect_script(cases) -> dict[str, list[dict[str, str]]]:
    """A judge that matches the gold labels exactly on every run."""
    return {c.case_id: [dict(c.expected_gates)] * RUNS_PER_CASE for c in cases}


def _calibrate(cases, judge, role="freeze-gate"):
    runs = run_calibration(cases, judge, validator_prompt=VALIDATOR_PROMPT)
    return score(runs, role)


# ---------------------------------------------------------------------------
# load_gold_set
# ---------------------------------------------------------------------------


class TestLoadGoldSet:
    def test_loads_all_cases_from_static_fixtures(self):
        cases = load_gold_set(STATIC_GOLD)
        assert len(cases) == 12
        assert {c.validator for c in cases} == {"sad-validator"}
        assert {c.artifact_type for c in cases} == {"SAD"}

    def test_static_set_contains_g9_receipt_2_case(self):
        # The G-9 receipt-2 scenario (one gate flipping across identical runs)
        # is a standing fixture, named so nobody forgets what it replays.
        cases = load_gold_set(STATIC_GOLD)
        assert "g9-receipt-2-stability-flip" in {c.case_id for c in cases}

    def test_fixture_content_is_loaded_and_pin_verified(self):
        cases = load_gold_set(STATIC_GOLD)
        for case in cases:
            assert case.input_content, f"{case.case_id} loaded no fixture content"
            assert (
                hashlib.sha256(case.input_content.encode("utf-8")).hexdigest()
                == case.input_sha256
            )

    def test_fields_are_parsed_with_types(self):
        cases = {c.case_id: c for c in load_gold_set(STATIC_GOLD)}
        exempt = cases["spec-exemption-001"]
        assert exempt.spec_exemption_case is True
        assert cases["case-001"].spec_exemption_case is False
        assert cases["gold-fail-001"].expected_gates["completeness"] == "FAIL"
        assert cases["case-001"].labeled_date == "2026-08-14"
        assert cases["case-001"].dispute_ref is None

    def test_sha_mismatch_is_a_load_refusal_not_a_warning(self, tmp_path):
        build_gold_set(tmp_path)
        # Tamper with one fixture after pinning: the exact "silently measures
        # the wrong thing" failure the schema's content_pinned constraint names.
        (tmp_path / "inputs" / "pass-000.md").write_bytes(b"tampered\n")
        with pytest.raises(CalibrationError) as exc:
            load_gold_set(tmp_path)
        assert exc.value.code == "sha_mismatch"
        assert "pass-000" in exc.value.message

    def test_missing_fixture_refuses_load(self, tmp_path):
        build_gold_set(tmp_path)
        (tmp_path / "inputs" / "pass-000.md").unlink()
        with pytest.raises(CalibrationError) as exc:
            load_gold_set(tmp_path)
        assert exc.value.code == "missing_fixture"

    def test_missing_required_field_refuses_load(self, tmp_path):
        build_gold_set(tmp_path)
        case_file = tmp_path / "pass-000.yaml"
        text = case_file.read_text(encoding="utf-8")
        case_file.write_bytes(text.replace("labeled_by: Todd Linnertz\n", "").encode("utf-8"))
        with pytest.raises(CalibrationError) as exc:
            load_gold_set(tmp_path)
        assert exc.value.code == "bad_case"
        assert "labeled_by" in exc.value.message

    def test_bad_gate_label_refuses_load(self, tmp_path):
        build_gold_set(tmp_path)
        case_file = tmp_path / "pass-000.yaml"
        text = case_file.read_text(encoding="utf-8")
        case_file.write_bytes(text.replace("g1: PASS", "g1: MAYBE").encode("utf-8"))
        with pytest.raises(CalibrationError) as exc:
            load_gold_set(tmp_path)
        assert exc.value.code == "bad_case"

    def test_crlf_checkout_does_not_break_the_pin(self, tmp_path):
        # G-19: windows-latest CI checks text files out with CRLF (git
        # autocrlf). The pin is over LF-normalized text — the same convention
        # as hash_artifact_content — so a CRLF-stored fixture still verifies
        # against its LF pin, and the judge sees identical LF input on every
        # platform.
        build_gold_set(tmp_path)
        lf_text = "# pass-000\n\ncrlf checkout survivor\n"
        write_case(
            tmp_path,
            "pass-000",
            {"g1": "PASS"},
            content=lf_text.replace("\n", "\r\n"),
            sha=hashlib.sha256(lf_text.encode("utf-8")).hexdigest(),
        )
        cases = {c.case_id: c for c in load_gold_set(tmp_path)}
        assert cases["pass-000"].input_content == lf_text

    def test_missing_gold_dir_refuses(self, tmp_path):
        with pytest.raises(CalibrationError) as exc:
            load_gold_set(tmp_path / "nope")
        assert exc.value.code == "bad_gold_dir"


class TestActivationFloor:
    """Below the ratified floor, calibration refuses and no lock may follow."""

    def test_eleven_cases_refused(self, tmp_path):
        build_gold_set(tmp_path, n_pass=8)  # 8 + 2 + 1 = 11
        with pytest.raises(CalibrationError) as exc:
            load_gold_set(tmp_path)
        assert exc.value.code == "below_floor"
        assert str(FLOOR_MIN_CASES) in exc.value.message

    def test_single_spec_exemption_case_refused(self, tmp_path):
        build_gold_set(tmp_path, n_pass=10, n_exemption=1)  # 12 cases, 1 exempt
        with pytest.raises(CalibrationError) as exc:
            load_gold_set(tmp_path)
        assert exc.value.code == "below_floor"

    def test_no_gold_fail_case_refused(self, tmp_path):
        # False-PASS measurement is impossible without gold-FAIL gates.
        build_gold_set(tmp_path, n_pass=10, n_fail=0)  # 12 cases, all PASS
        with pytest.raises(CalibrationError) as exc:
            load_gold_set(tmp_path)
        assert exc.value.code == "below_floor"

    def test_no_gold_pass_case_refused(self, tmp_path):
        build_gold_set(tmp_path, n_pass=0, n_exemption=0, n_fail=12)
        with pytest.raises(CalibrationError) as exc:
            load_gold_set(tmp_path)
        assert exc.value.code == "below_floor"

    def test_floor_constants_mirror_ratified_decision_3(self):
        assert FLOOR_MIN_CASES == 12
        assert FLOOR_MIN_SPEC_EXEMPTION_CASES == 2
        assert FLOOR_MIN_PASS_CASES == 1
        assert FLOOR_MIN_FAIL_CASES == 1


# ---------------------------------------------------------------------------
# run_calibration
# ---------------------------------------------------------------------------


class TestRunCalibration:
    def test_each_case_judged_exactly_three_times(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        judge = ScriptedJudgeAdapter(perfect_script(cases))
        run_calibration(cases, judge, validator_prompt=VALIDATOR_PROMPT)
        assert len(judge.calls) == RUNS_PER_CASE * len(cases)
        per_case: dict[str, int] = {}
        for req in judge.calls:
            per_case[req.metadata["case_id"]] = per_case.get(req.metadata["case_id"], 0) + 1
        assert set(per_case.values()) == {RUNS_PER_CASE}

    def test_runs_per_case_is_a_hardcoded_three(self):
        # Ratified decision: 3 identical runs per case. Not configurable —
        # a knob here would let a flaky judge be "calibrated" at 1 run.
        assert RUNS_PER_CASE == 3
        assert "runs_per_case" not in inspect.signature(run_calibration).parameters

    def test_judge_requests_are_judge_shaped(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        judge = ScriptedJudgeAdapter(perfect_script(cases))
        run_calibration(cases, judge, validator_prompt=VALIDATOR_PROMPT)
        first = judge.calls[0]
        # Temperature 0 comes structurally, same as the ConvergenceLoop's pin.
        assert first.temperature == 0.0
        assert first.event == LifecycleEvent.PRE_VALIDATION
        assert first.prompt_content == VALIDATOR_PROMPT
        assert first.current_artifact == cases[0].input_content
        assert first.metadata["case_id"] == cases[0].case_id

    def test_identical_input_across_the_three_runs(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        judge = ScriptedJudgeAdapter(perfect_script(cases))
        run_calibration(cases, judge, validator_prompt=VALIDATOR_PROMPT)
        by_case: dict[str, list[AgentRequest]] = {}
        for req in judge.calls:
            by_case.setdefault(req.metadata["case_id"], []).append(req)
        for reqs in by_case.values():
            artifacts = {r.current_artifact for r in reqs}
            prompts = {r.prompt_content for r in reqs}
            assert len(artifacts) == 1 and len(prompts) == 1

    def test_captures_judge_identity(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        judge = ScriptedJudgeAdapter(
            perfect_script(cases), provider_name="prov-x", model_name="model-y"
        )
        runs = run_calibration(cases, judge, validator_prompt=VALIDATOR_PROMPT)
        assert runs.provider == "prov-x"
        assert runs.model == "model-y"
        assert runs.prompt_sha256 == VALIDATOR_SHA
        assert runs.validator == "sad-validator"

    def test_unparseable_judge_output_is_structured_error(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)

        class GarbageJudge(ScriptedJudgeAdapter):
            def invoke(self, request):
                response = super().invoke(request)
                response.content = "not a verdict"
                return response

        with pytest.raises(CalibrationError) as exc:
            run_calibration(cases, GarbageJudge(), validator_prompt=VALIDATOR_PROMPT)
        assert exc.value.code == "bad_judge_output"


# ---------------------------------------------------------------------------
# score — agreement math, asymmetric gates, kappa
# ---------------------------------------------------------------------------


class TestScoreAgreement:
    def test_perfect_judge_passes_freeze_gate(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        report = _calibrate(cases, ScriptedJudgeAdapter(perfect_script(cases)))
        assert report.gate_agreement == pytest.approx(1.0)
        assert report.false_pass_count == 0
        assert report.stability_flips == []
        assert report.verdict == "PASS"

    def test_agreement_fraction_over_cases_x_gates_x_runs(self, tmp_path):
        # 12 cases x 1 gate x 3 runs = 36 observations; judge consistently
        # wrong (strict direction) on exactly one case => 33/36.
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        script["pass-000"] = [{"g1": "FAIL"}] * 3
        report = _calibrate(cases, ScriptedJudgeAdapter(script))
        assert report.gate_agreement == pytest.approx(33 / 36)
        assert report.verdict == "PASS"  # 0.9166 >= 0.9, strict direction only

    def test_freeze_gate_fails_below_090(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        script["pass-000"] = [{"g1": "FAIL"}] * 3
        script["pass-001"] = [{"g1": "FAIL"}] * 3
        report = _calibrate(cases, ScriptedJudgeAdapter(script))
        assert report.gate_agreement == pytest.approx(30 / 36)
        assert report.false_pass_count == 0
        assert report.verdict == "FAIL"

    def test_advisory_passes_where_freeze_gate_agreement_fails(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        script["pass-000"] = [{"g1": "FAIL"}] * 3
        script["pass-001"] = [{"g1": "FAIL"}] * 3
        report = _calibrate(cases, ScriptedJudgeAdapter(script), role="advisory")
        assert report.gate_agreement == pytest.approx(30 / 36)  # 0.833 >= 0.75
        assert report.verdict == "PASS"

    def test_advisory_fails_below_075(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        for cid in ("pass-000", "pass-001", "pass-002", "pass-003"):
            script[cid] = [{"g1": "FAIL"}] * 3
        report = _calibrate(cases, ScriptedJudgeAdapter(script), role="advisory")
        assert report.gate_agreement == pytest.approx(24 / 36)
        assert report.verdict == "FAIL"


class TestScoreFalsePass:
    def test_any_false_pass_fails_freeze_gate_despite_high_agreement(self, tmp_path):
        # The dangerous direction: a lenient judge PASSing a gold-FAIL gate.
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        script["fail-000"] = [{"g1": "PASS"}] * 3
        report = _calibrate(cases, ScriptedJudgeAdapter(script))
        assert report.gate_agreement == pytest.approx(33 / 36)  # >= 0.9
        assert report.false_pass_count == 1
        assert report.verdict == "FAIL"

    def test_single_run_false_pass_counts(self, tmp_path):
        # PASS on a gold-FAIL gate in ANY of the 3 runs counts (it also flips,
        # and either hard gate alone must fail the calibration).
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        script["fail-000"] = [{"g1": "FAIL"}, {"g1": "PASS"}, {"g1": "FAIL"}]
        report = _calibrate(cases, ScriptedJudgeAdapter(script))
        assert report.false_pass_count == 1
        assert {"case_id": "fail-000", "gate": "g1"} in report.stability_flips
        assert report.verdict == "FAIL"

    def test_advisory_has_no_false_pass_bar(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        script["fail-000"] = [{"g1": "PASS"}] * 3
        report = _calibrate(cases, ScriptedJudgeAdapter(script), role="advisory")
        assert report.false_pass_count == 1  # still REPORTED
        assert report.verdict == "PASS"  # but never gates an advisory judge


class TestScoreStability:
    def test_flip_fails_freeze_gate_even_at_high_agreement(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        script["pass-000"] = [{"g1": "PASS"}, {"g1": "PASS"}, {"g1": "FAIL"}]
        report = _calibrate(cases, ScriptedJudgeAdapter(script))
        assert report.gate_agreement == pytest.approx(35 / 36)
        assert report.stability_flips == [{"case_id": "pass-000", "gate": "g1"}]
        assert report.verdict == "FAIL"

    def test_flip_fails_advisory_too(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        script["pass-000"] = [{"g1": "PASS"}, {"g1": "FAIL"}, {"g1": "PASS"}]
        report = _calibrate(cases, ScriptedJudgeAdapter(script), role="advisory")
        assert report.verdict == "FAIL"

    def test_g9_receipt_2_scenario_on_the_named_fixture(self):
        # The receipt itself, replayed: same prompt, same model, same input,
        # one hard gate flipping across three identical runs. Aggregate score
        # is excellent; the calibration still FAILS. This is the failure class
        # FR-014 exists to catch, pinned to its named gold case.
        cases = load_gold_set(STATIC_GOLD)
        script = perfect_script(cases)
        stable = {"completeness": "PASS", "spec-exemption-honored": "PASS"}
        flipped = {"completeness": "FAIL", "spec-exemption-honored": "PASS"}
        script["g9-receipt-2-stability-flip"] = [dict(stable), dict(stable), flipped]
        report = _calibrate(cases, ScriptedJudgeAdapter(script))
        assert report.gate_agreement == pytest.approx(71 / 72)
        assert report.stability_flips == [
            {"case_id": "g9-receipt-2-stability-flip", "gate": "completeness"}
        ]
        assert report.verdict == "FAIL"


class TestKappa:
    def test_kappa_is_computed_and_stored(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        report = _calibrate(cases, ScriptedJudgeAdapter(perfect_script(cases)))
        assert report.kappa == pytest.approx(1.0)

    def test_kappa_never_gates_the_verdict(self, tmp_path):
        # Two advisory calibrations with WILDLY different kappa (0.0 vs 1.0)
        # and the same verdict: kappa is drift-trend telemetry, not a gate.
        # (Always-PASS judge on an imbalanced set: po == pe => kappa 0.)
        gold_a = tmp_path / "a"
        build_gold_set(gold_a)
        cases_a = load_gold_set(gold_a)
        always_pass = ScriptedJudgeAdapter(default_gates={"g1": "PASS"})
        report_a = _calibrate(cases_a, always_pass, role="advisory")

        gold_b = tmp_path / "b"
        build_gold_set(gold_b)
        cases_b = load_gold_set(gold_b)
        report_b = _calibrate(
            cases_b, ScriptedJudgeAdapter(perfect_script(cases_b)), role="advisory"
        )

        assert report_a.kappa == pytest.approx(0.0)
        assert report_b.kappa == pytest.approx(1.0)
        assert report_a.verdict == report_b.verdict == "PASS"

    def test_verdict_helper_cannot_even_see_kappa(self):
        # Structural enforcement of kappa_never_gates: the verdict logic has
        # no kappa parameter to consult.
        params = inspect.signature(calibration._verdict).parameters
        assert "kappa" not in params


class TestReportShape:
    def test_report_fields_match_schema(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        report = _calibrate(cases, ScriptedJudgeAdapter(perfect_script(cases)))
        payload = asdict(report)
        assert set(payload) == {
            "report_version",
            "validator",
            "artifact_type",
            "role",
            "judge",
            "run_date",
            "case_count",
            "runs_per_case",
            "gate_agreement",
            "false_pass_count",
            "stability_flips",
            "kappa",
            "verdict",
            "gates",
        }
        assert payload["report_version"] == REPORT_VERSION
        assert payload["runs_per_case"] == RUNS_PER_CASE
        assert payload["case_count"] == 12
        assert set(payload["judge"]) == {"provider", "model", "prompt_sha256"}
        assert "T" in payload["run_date"]  # ISO 8601 UTC

    def test_per_gate_matrix(self, tmp_path):
        build_gold_set(tmp_path, gates=("g1", "g2"))
        cases = load_gold_set(tmp_path)
        script = perfect_script(cases)
        script["fail-000"] = [{"g1": "PASS", "g2": "PASS"}] * 3  # false pass on g1
        script["pass-000"] = [
            {"g1": "PASS", "g2": "PASS"},
            {"g1": "PASS", "g2": "FAIL"},
            {"g1": "PASS", "g2": "PASS"},
        ]  # flip on g2
        report = _calibrate(cases, ScriptedJudgeAdapter(script))
        assert report.gates["g1"]["total"] == 36
        assert report.gates["g1"]["agreements"] == 33
        assert report.gates["g1"]["false_pass"] == 1
        assert report.gates["g1"]["flips"] == 0
        assert report.gates["g2"]["false_pass"] == 0
        assert report.gates["g2"]["flips"] == 1

    def test_unknown_role_is_refused(self, tmp_path):
        build_gold_set(tmp_path)
        cases = load_gold_set(tmp_path)
        judge = ScriptedJudgeAdapter(perfect_script(cases))
        runs = run_calibration(cases, judge, validator_prompt=VALIDATOR_PROMPT)
        with pytest.raises(CalibrationError) as exc:
            score(runs, "supreme-court")
        assert exc.value.code == "bad_role"


# ---------------------------------------------------------------------------
# write_report / write_lock / check_lock
# ---------------------------------------------------------------------------


def _pass_report(tmp_path, subdir="gold"):
    gold = tmp_path / subdir
    build_gold_set(gold)
    cases = load_gold_set(gold)
    return _calibrate(cases, ScriptedJudgeAdapter(perfect_script(cases)))


class TestWriteReport:
    def test_report_json_roundtrip_utf8_lf(self, tmp_path):
        report = _pass_report(tmp_path)
        out = tmp_path / "reports" / "sad-validator-2026-08-14.json"
        write_report(report, out)
        raw = out.read_bytes()
        assert b"\r" not in raw  # LF discipline (G-19)
        data = json.loads(raw.decode("utf-8"))
        assert data == asdict(report)

    def test_no_tmp_file_left_behind(self, tmp_path):
        report = _pass_report(tmp_path)
        out = tmp_path / "report.json"
        write_report(report, out)
        assert [p.name for p in tmp_path.glob("*.json*")] == ["report.json"]


class TestWriteLock:
    def test_lock_structure_matches_schema(self, tmp_path):
        report = _pass_report(tmp_path)
        lock = tmp_path / "calibration.lock"
        write_lock(report, lock, report_ref="reports/r.json")
        data = json.loads(lock.read_text(encoding="utf-8"))
        assert data["lock_version"] == CALIBRATION_LOCK_VERSION
        entry = data["validators"]["sad-validator"]
        assert set(entry) == {
            "prompt_sha256",
            "model",
            "gate_agreement",
            "false_pass_count",
            "calibrated_at",
            "report_ref",
        }
        assert entry["prompt_sha256"] == VALIDATOR_SHA
        assert entry["model"] == "scripted-judge-v1"
        assert entry["false_pass_count"] == 0
        assert entry["report_ref"] == "reports/r.json"
        assert b"\r" not in lock.read_bytes()

    def test_lock_preserves_other_validators_entries(self, tmp_path):
        lock = tmp_path / "calibration.lock"
        lock.write_bytes(
            json.dumps(
                {
                    "lock_version": "1.0",
                    "validators": {"prd-validator": {"prompt_sha256": "abc", "model": "m0"}},
                }
            ).encode("utf-8")
        )
        report = _pass_report(tmp_path)
        write_lock(report, lock, report_ref="r.json")
        data = json.loads(lock.read_text(encoding="utf-8"))
        assert set(data["validators"]) == {"prd-validator", "sad-validator"}
        assert data["validators"]["prd-validator"]["model"] == "m0"

    def test_failing_calibration_writes_no_lock(self, tmp_path):
        # The refusal shape: a FAIL verdict must never mint a lock entry, the
        # same way mark-status refuses FROZEN.
        gold = tmp_path / "gold"
        build_gold_set(gold)
        cases = load_gold_set(gold)
        script = perfect_script(cases)
        script["fail-000"] = [{"g1": "PASS"}] * 3
        report = _calibrate(cases, ScriptedJudgeAdapter(script))
        assert report.verdict == "FAIL"
        lock = tmp_path / "calibration.lock"
        with pytest.raises(CalibrationError) as exc:
            write_lock(report, lock, report_ref="r.json")
        assert exc.value.code == "verdict_fail"
        assert not lock.exists()


class TestCheckLock:
    """Pure string/hash comparison — the slice 3 / slice 4 call path."""

    def _lock(self, tmp_path):
        report = _pass_report(tmp_path)
        lock = tmp_path / "calibration.lock"
        write_lock(report, lock, report_ref="r.json")
        return report, lock

    def test_roundtrip_is_fresh(self, tmp_path):
        report, lock = self._lock(tmp_path)
        result = check_lock(
            report.judge["prompt_sha256"],
            report.judge["model"],
            lock,
            validator="sad-validator",
        )
        assert result.fresh is True
        assert result.reason == "fresh"

    def test_prompt_change_is_stale(self, tmp_path):
        report, lock = self._lock(tmp_path)
        live_sha = hashlib.sha256(b"edited validator prompt").hexdigest()
        result = check_lock(
            live_sha, report.judge["model"], lock, validator="sad-validator"
        )
        assert result.fresh is False
        assert result.reason == "prompt_changed"

    def test_model_change_is_stale(self, tmp_path):
        report, lock = self._lock(tmp_path)
        result = check_lock(
            report.judge["prompt_sha256"], "gpt-99", lock, validator="sad-validator"
        )
        assert result.fresh is False
        assert result.reason == "model_changed"

    def test_missing_lock_is_stale_by_definition(self, tmp_path):
        result = check_lock(
            "sha", "model", tmp_path / "calibration.lock", validator="sad-validator"
        )
        assert result.fresh is False
        assert result.reason == "missing_lock"

    def test_unknown_validator_is_stale(self, tmp_path):
        _, lock = self._lock(tmp_path)
        result = check_lock("sha", "model", lock, validator="tdd-validator")
        assert result.fresh is False
        assert result.reason == "unknown_validator"

    def test_corrupt_lock_is_stale(self, tmp_path):
        lock = tmp_path / "calibration.lock"
        lock.write_bytes(b"{not json")
        result = check_lock("sha", "model", lock, validator="sad-validator")
        assert result.fresh is False
        assert result.reason == "bad_lock"


# ---------------------------------------------------------------------------
# Module invariants
# ---------------------------------------------------------------------------


class TestModuleInvariants:
    FORBIDDEN_STATUS_WRITERS = {"write_artifact_status", "apply_freeze_decision"}
    FORBIDDEN_MODULES = {"src.state", "src.freeze"}

    def test_calibration_never_references_status_writers(self):
        # Calibration measures the judge; it never corrects artifacts and
        # never writes artifact status (measures_never_corrects). Enforced at
        # the AST: no import of the state/freeze modules, no reference to the
        # status-writing functions, anywhere in the module.
        tree = ast.parse(CALIBRATION_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in self.FORBIDDEN_MODULES, ast.dump(node)
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "") not in self.FORBIDDEN_MODULES, ast.dump(node)
            elif isinstance(node, ast.Name):
                assert node.id not in self.FORBIDDEN_STATUS_WRITERS
            elif isinstance(node, ast.Attribute):
                assert node.attr not in self.FORBIDDEN_STATUS_WRITERS

    def test_calibration_namespace_has_no_status_writers(self):
        for name in self.FORBIDDEN_STATUS_WRITERS:
            assert not hasattr(calibration, name)

    def test_all_text_io_declares_utf8_encoding(self):
        # G-6: CI runs windows-latest where the default codec is cp1252.
        # Every text open/read_text/write_text in calibration.py must say
        # encoding explicitly. (read_bytes is exempt: binary is binary.)
        tree = ast.parse(CALIBRATION_SRC.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue
            if name in {"open", "read_text", "write_text"}:
                if "encoding" not in {kw.arg for kw in node.keywords}:
                    offenders.append((name, node.lineno))
        assert not offenders, f"text I/O without explicit encoding: {offenders}"


# ---------------------------------------------------------------------------
# Guarded sync with the sibling schema repo (single-source thresholds)
# ---------------------------------------------------------------------------


def _schema_repo():
    env = os.environ.get("AIEOS_SCHEMA_PATH")
    candidates = [Path(env)] if env else []
    candidates += [REPO_ROOT / "aieos-schema", REPO_ROOT.parent / "aieos-schema"]
    for c in candidates:
        if (c / "schema" / "calibration-report.yaml").is_file():
            return c
    return None


SCHEMA_REPO = _schema_repo()


@pytest.mark.skipif(
    SCHEMA_REPO is None,
    reason="aieos-schema repo not found (set AIEOS_SCHEMA_PATH)",
)
class TestSchemaSync:
    """The schema files are the single source; these constants mirror them.

    Same guarded-sibling pattern as tests/integration/test_doc_control_conformance.py:
    runs locally and in CI (sibling checkout), skips when the repo is absent.
    """

    def _load(self, name):
        return yaml.safe_load(
            (SCHEMA_REPO / "schema" / name).read_text(encoding="utf-8")
        )

    def test_thresholds_mirror_calibration_report_schema(self):
        data = self._load("calibration-report.yaml")
        assert data["runs_per_case"] == RUNS_PER_CASE
        th = data["thresholds"]
        assert th["freeze-gate"]["gate_agreement_min"] == THRESHOLDS["freeze-gate"]["gate_agreement_min"]
        assert th["freeze-gate"]["false_pass_max"] == THRESHOLDS["freeze-gate"]["false_pass_max"]
        assert th["advisory"]["gate_agreement_min"] == THRESHOLDS["advisory"]["gate_agreement_min"]
        assert th["advisory"]["false_pass_max"] is None
        assert THRESHOLDS["advisory"]["false_pass_max"] is None
        assert data["calibration_report_version"] == REPORT_VERSION

    def test_activation_floor_mirrors_gold_case_schema(self):
        floor = self._load("gold-case.yaml")["activation_floor"]
        assert floor["min_cases"] == FLOOR_MIN_CASES
        assert floor["min_spec_exemption_cases"] == FLOOR_MIN_SPEC_EXEMPTION_CASES
        assert floor["min_pass_cases"] == FLOOR_MIN_PASS_CASES
        assert floor["min_fail_cases"] == FLOOR_MIN_FAIL_CASES

    def test_lock_version_mirrors_calibration_lock_schema(self):
        data = self._load("calibration-lock.yaml")
        assert data["calibration_lock_version"] == CALIBRATION_LOCK_VERSION


# ---------------------------------------------------------------------------
# CLI: harness calibrate
# ---------------------------------------------------------------------------


from src.cli import main  # noqa: E402


def _prompt_file(tmp_path):
    vp = tmp_path / "validator-prompt.md"
    vp.write_bytes(VALIDATOR_PROMPT.encode("utf-8"))
    return vp


def _spec_file(tmp_path):
    sp = tmp_path / "spec.md"
    sp.write_bytes(b"# Spec\n\nGate definitions and exemptions live here.\n")
    return sp


class TestCalibrateCli:
    def test_gold_dir_required_unless_check_only(self, tmp_path, capsys):
        rc = main(["--config", "nope.yaml", "calibrate", "--lock", str(tmp_path / "l")])
        assert rc == 2
        assert json.loads(capsys.readouterr().err)["error"] == "bad_request"

    def test_below_floor_exit_3(self, tmp_path, capsys):
        gold = tmp_path / "gold"
        build_gold_set(gold, n_pass=8)  # 11 cases
        rc = main(
            [
                "--config", "nope.yaml", "calibrate",
                "--gold-dir", str(gold),
                "--lock", str(tmp_path / "calibration.lock"),
            ]
        )
        assert rc == 3
        err = json.loads(capsys.readouterr().err)
        assert err["error"] == "below_floor"
        assert not (tmp_path / "calibration.lock").exists()

    def test_load_error_exit_2(self, tmp_path, capsys):
        gold = tmp_path / "gold"
        build_gold_set(gold)
        (gold / "inputs" / "pass-000.md").write_bytes(b"tampered\n")
        rc = main(
            [
                "--config", "nope.yaml", "calibrate",
                "--gold-dir", str(gold),
                "--lock", str(tmp_path / "calibration.lock"),
            ]
        )
        assert rc == 2
        assert json.loads(capsys.readouterr().err)["error"] == "sha_mismatch"

    def test_calibration_fail_exit_1_and_no_lock(self, tmp_path, capsys):
        # Real wiring end to end: the converging mock judge blanket-PASSes,
        # which false-PASSes the gold-FAIL case => calibration FAIL, exit 1,
        # report written (evidence), lock NOT written.
        cfg = tmp_path / "harness.yaml"
        cfg.write_bytes(b"providers:\n  mock:\n    enabled: true\n")
        gold = tmp_path / "gold"
        build_gold_set(gold, gates=("completeness", "structure"))
        lock = tmp_path / "calibration.lock"
        report_out = tmp_path / "report.json"
        rc = main(
            [
                "--config", str(cfg), "calibrate",
                "--gold-dir", str(gold),
                "--validator-prompt", str(_prompt_file(tmp_path)),
                "--spec-file", str(_spec_file(tmp_path)),
                "--report-out", str(report_out),
                "--lock", str(lock),
            ]
        )
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert out["verdict"] == "FAIL"
        assert out["false_pass_count"] == 1
        assert report_out.exists()
        assert not lock.exists()

    def test_calibration_pass_exit_0_writes_report_and_lock(
        self, tmp_path, capsys, monkeypatch
    ):
        import src.cli as cli_mod

        gold = tmp_path / "gold"
        build_gold_set(gold)
        judge = ScriptedJudgeAdapter(default_gates={"g1": "PASS"}, script={
            "fail-000": [{"g1": "FAIL"}] * 3,
        })
        monkeypatch.setattr(cli_mod, "_build_adapters", lambda config: {"scripted": judge})
        lock = tmp_path / "calibration.lock"
        rc = main(
            [
                "--config", "nope.yaml", "calibrate",
                "--gold-dir", str(gold),
                "--validator-prompt", str(_prompt_file(tmp_path)),
                "--spec-file", str(_spec_file(tmp_path)),
                "--lock", str(lock),
            ]
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["verdict"] == "PASS"
        assert out["validator"] == "sad-validator"
        # Default report path: <gold-dir>/reports/<validator>-<date>.json
        reports = list((gold / "reports").glob("sad-validator-*.json"))
        assert len(reports) == 1
        data = json.loads(lock.read_text(encoding="utf-8"))
        entry = data["validators"]["sad-validator"]
        assert entry["prompt_sha256"] == VALIDATOR_SHA
        assert entry["model"] == "scripted-judge-v1"
        assert entry["report_ref"] == str(reports[0])

    def _write_lock_file(self, tmp_path, sha, model="m1"):
        lock = tmp_path / "calibration.lock"
        lock.write_bytes(
            json.dumps(
                {
                    "lock_version": "1.0",
                    "validators": {
                        "sad-validator": {
                            "prompt_sha256": sha,
                            "model": model,
                            "gate_agreement": 1.0,
                            "false_pass_count": 0,
                            "calibrated_at": "2026-08-14T00:00:00Z",
                            "report_ref": "r.json",
                        }
                    },
                }
            ).encode("utf-8")
        )
        return lock

    def test_check_only_fresh_exit_0_zero_adapter_calls(
        self, tmp_path, capsys, monkeypatch
    ):
        import src.cli as cli_mod

        built = []
        monkeypatch.setattr(
            cli_mod, "_build_adapters", lambda config: built.append(1) or {}
        )
        lock = self._write_lock_file(tmp_path, VALIDATOR_SHA)
        rc = main(
            [
                "--config", "nope.yaml", "calibrate", "--check-only",
                "--validator", "sad-validator",
                "--prompt-sha", VALIDATOR_SHA,
                "--model", "m1",
                "--lock", str(lock),
            ]
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["fresh"] is True
        assert out["reason"] == "fresh"
        # ZERO LLM machinery on the check path: no adapter was even built.
        assert built == []

    def test_check_only_stale_prompt_exit_4(self, tmp_path, capsys):
        lock = self._write_lock_file(tmp_path, "old-sha")
        rc = main(
            [
                "--config", "nope.yaml", "calibrate", "--check-only",
                "--validator", "sad-validator",
                "--prompt-sha", VALIDATOR_SHA,
                "--model", "m1",
                "--lock", str(lock),
            ]
        )
        assert rc == 4
        out = json.loads(capsys.readouterr().out)
        assert out["fresh"] is False
        assert out["reason"] == "prompt_changed"

    def test_check_only_stale_model_exit_4(self, tmp_path, capsys):
        lock = self._write_lock_file(tmp_path, VALIDATOR_SHA, model="old-model")
        rc = main(
            [
                "--config", "nope.yaml", "calibrate", "--check-only",
                "--validator", "sad-validator",
                "--prompt-sha", VALIDATOR_SHA,
                "--model", "m1",
                "--lock", str(lock),
            ]
        )
        assert rc == 4
        assert json.loads(capsys.readouterr().out)["reason"] == "model_changed"

    def test_check_only_missing_lock_exit_4(self, tmp_path, capsys):
        rc = main(
            [
                "--config", "nope.yaml", "calibrate", "--check-only",
                "--validator", "sad-validator",
                "--prompt-sha", VALIDATOR_SHA,
                "--model", "m1",
                "--lock", str(tmp_path / "calibration.lock"),
            ]
        )
        assert rc == 4
        assert json.loads(capsys.readouterr().out)["reason"] == "missing_lock"

    def test_check_only_requires_validator(self, tmp_path, capsys):
        rc = main(
            [
                "--config", "nope.yaml", "calibrate", "--check-only",
                "--prompt-sha", "x", "--model", "m1",
                "--lock", str(tmp_path / "calibration.lock"),
            ]
        )
        assert rc == 2
        assert json.loads(capsys.readouterr().err)["error"] == "bad_request"


class TestCalibrateSpecContext:
    """The judge must receive the spec it enforces (slice-3 defect guard).

    The exemption text that decides the spec-exemption gold cases lives in
    the spec; a calibration run without it measures a different judge than
    the one gating freezes.
    """

    def test_judge_receives_the_spec(self, tmp_path, capsys, monkeypatch):
        import src.cli as cli_mod

        gold = tmp_path / "gold"
        build_gold_set(gold)
        judge = ScriptedJudgeAdapter(
            default_gates={"g1": "PASS"},
            script={"fail-000": [{"g1": "FAIL"}] * 3},
        )
        monkeypatch.setattr(cli_mod, "_build_adapters", lambda config: {"scripted": judge})
        spec = _spec_file(tmp_path)
        rc = main(
            [
                "--config", "nope.yaml", "calibrate",
                "--gold-dir", str(gold),
                "--validator-prompt", str(_prompt_file(tmp_path)),
                "--spec-file", str(spec),
                "--lock", str(tmp_path / "calibration.lock"),
            ]
        )
        assert rc == 0
        spec_text = spec.read_text(encoding="utf-8")
        assert judge.calls, "judge was never invoked"
        assert all(r.spec_content == spec_text for r in judge.calls)

    def test_unresolvable_spec_refuses_exit_2(self, tmp_path, capsys, monkeypatch):
        # Pin aieos_root to an empty dir so kit resolution cannot succeed by
        # accident on a machine with sibling kit checkouts, and clear the env
        # override so the test is hermetic everywhere.
        monkeypatch.delenv("AIEOS_ROOT", raising=False)
        nokits = tmp_path / "nokits"
        nokits.mkdir()
        cfg = tmp_path / "harness.yaml"
        cfg.write_bytes(f"aieos_root: {nokits.as_posix()}\n".encode("utf-8"))
        gold = tmp_path / "gold"
        build_gold_set(gold)
        rc = main(
            [
                "--config", str(cfg), "calibrate",
                "--gold-dir", str(gold),
                "--validator-prompt", str(_prompt_file(tmp_path)),
                "--lock", str(tmp_path / "calibration.lock"),
            ]
        )
        assert rc == 2
        err = json.loads(capsys.readouterr().err)
        assert err["error"] == "bad_request"
        assert "spec" in err["message"].lower()
        assert not (tmp_path / "calibration.lock").exists()
