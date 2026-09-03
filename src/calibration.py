"""Judge-calibration engine (FR-014 slice 2).

Measures an LLM-backed validator ("judge") against its human-labeled gold set:
each gold case is judged exactly :data:`RUNS_PER_CASE` times on identical
input, verdicts are scored at the GATE level, and the calibration verdict
applies the role's asymmetric thresholds. G-9's two receipts are standing
gates here: a judge that PASSes a gold-FAIL gate in any run (receipt 1's
lenient direction) or flips a gate across identical runs (receipt 2) fails
calibration regardless of aggregate score. Cohen's kappa is computed and
STORED for drift trending; no verdict logic may read it.

The contracts are the three schema records ratified 2026-08-12 in
``aieos-schema``: ``schema/gold-case.yaml`` (loader + activation floor),
``schema/calibration-report.yaml`` (scoring, thresholds, verdict), and
``schema/calibration-lock.yaml`` (the deterministic CI contract). The
constants below mirror those files; a guarded sync test in
``tests/test_calibration.py`` reads the sibling schema repo and fails the
build if they drift.

:func:`check_lock` is the whole per-push CI contract: pure string/hash
comparison, zero LLM, zero network. kit-ci (slice 3) and the dark-factory
conductor (slice 4) call exactly that function.

INVARIANT (measures_never_corrects, test-enforced by AST): calibration
measures the judge; it never corrects artifacts and never writes artifact
status. This module imports nothing from the freeze or state writers — the
same refusal shape as mark-status refusing FROZEN.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from src.adapters.base import AgentAdapter
from src.convergence import parse_validation_result
from src.models import AgentRequest, LifecycleEvent

# Ratified decision: three identical runs per case, always. Deliberately a
# constant, not a parameter — a knob here would let a flaky judge be
# "calibrated" at one run. Mirrors calibration-report.yaml ``runs_per_case``.
RUNS_PER_CASE = 3

# Activation floor (ratified decision 3). Mirrors gold-case.yaml
# ``activation_floor``. Below the floor, calibration refuses and no lock may
# be written — a 5-case gold set produces confident-looking numbers that
# measure nothing.
FLOOR_MIN_CASES = 12
FLOOR_MIN_SPEC_EXEMPTION_CASES = 2
FLOOR_MIN_PASS_CASES = 1
FLOOR_MIN_FAIL_CASES = 1

# Per-role verdict thresholds (ratified decision 2). Mirrors
# calibration-report.yaml ``thresholds``. Asymmetric on purpose: the
# dangerous direction is the lenient judge — a false PASS becomes a frozen
# artifact contaminating everything downstream — so freeze-gating roles get
# a zero-false-PASS bar that advisory (lens) roles do not.
THRESHOLDS: dict[str, dict[str, Optional[float]]] = {
    "freeze-gate": {"gate_agreement_min": 0.9, "false_pass_max": 0},
    "advisory": {"gate_agreement_min": 0.75, "false_pass_max": None},
}

REPORT_VERSION = "1.0"  # mirrors calibration-report.yaml calibration_report_version
CALIBRATION_LOCK_VERSION = "1.0"  # mirrors calibration-lock.yaml calibration_lock_version

_GOLD_CASE_REQUIRED_FIELDS = frozenset(
    {
        "case_id",
        "artifact_type",
        "validator",
        "input_path",
        "input_sha256",
        "expected_gates",
        "spec_exemption_case",
        "labeled_by",
        "labeled_date",
        "source",
    }
)

_VALID_GATE_LABELS = frozenset({"PASS", "FAIL"})

# The sentinel verdict recorded when the judge's output omits a labeled gate.
# It disagrees with both PASS and FAIL, and it participates in flip detection
# like any other verdict (a gate present in two runs and absent in the third
# IS instability).
_MISSING = "MISSING"


class CalibrationError(Exception):
    """A calibration step was refused. Carries a stable ``code`` so a caller
    (the CLI, kit-ci) can emit a structured error to branch on, distinct from
    an unexpected crash — the same contract shape as ``FreezeError``. When
    raised, nothing has been written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class GoldCase:
    """One human-labeled calibration case (gold-case.yaml).

    ``input_content`` is the fixture text the judge sees, loaded at parse
    time — but only after the sha256 pin verified, so a case that reaches a
    judge is guaranteed to be measuring the input its labels were assigned
    against.
    """

    case_id: str
    artifact_type: str
    validator: str
    input_path: Path
    input_sha256: str
    expected_gates: dict[str, str]
    spec_exemption_case: bool
    labeled_by: str
    labeled_date: str
    source: str
    dispute_ref: Optional[str] = None
    input_content: str = ""


@dataclass
class CaseRuns:
    """The :data:`RUNS_PER_CASE` per-gate verdict maps for one gold case.

    ``run_evidence`` carries the judge's OWN stated reasoning for each run
    (``summary`` + ``blocking_issues``), parallel to ``gate_verdicts`` and
    index-aligned with it. Scoring never reads it: the verdict is a function
    of the gate maps alone, exactly as before. It exists because a
    disagreement without a reason cannot be disputed, and dispute analysis
    is what tells us whether a failing gate means the judge is wrong or the
    gold label is (the ``dispute_ref`` accretion path on GoldCase).

    The validator prompt already asks for ``blocking_issues`` and
    :func:`parse_validation_result` already parses them -- this field only
    stops the engine from discarding them. No prompt change, so
    ``prompt_sha256`` is unaffected and runs stay comparable across the
    change.
    """

    case: GoldCase
    gate_verdicts: list[dict[str, str]] = field(default_factory=list)
    run_evidence: list[dict] = field(default_factory=list)


@dataclass
class CalibrationRuns:
    """Raw output of :func:`run_calibration` — verdicts plus judge identity.

    The identity triple (provider, model, prompt_sha256) is the same
    provenance slice 1 stamps on every ledger verdict; here it names the
    judge the whole calibration measured.
    """

    validator: str
    artifact_type: str
    provider: str
    model: str
    prompt_sha256: str
    case_runs: list[CaseRuns] = field(default_factory=list)
    # DEF-007. Sourced from the dict actually handed to the judge, never
    # from what a caller intended to supply -- a caller-derived value
    # reports full context on a run that delivered none, which is the
    # failure this field exists to expose.
    upstream_artifact_ids: list[str] = field(default_factory=list)


@dataclass
class CalibrationReport:
    """One calibration run's evidence record (calibration-report.yaml)."""

    report_version: str
    validator: str
    artifact_type: str
    role: str
    judge: dict[str, str]
    run_date: str
    case_count: int
    runs_per_case: int
    gate_agreement: float
    false_pass_count: int
    stability_flips: list[dict[str, str]]
    kappa: float
    verdict: str
    gates: dict[str, dict[str, int]]


@dataclass
class LockCheckResult:
    """Outcome of :func:`check_lock` — the deterministic staleness verdict.

    ``reason`` is one of: ``fresh``, ``missing_lock``, ``bad_lock``,
    ``unknown_validator``, ``prompt_changed``, ``model_changed``.
    """

    fresh: bool
    reason: str
    validator: str
    entry: Optional[dict] = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Gold set loading
# ---------------------------------------------------------------------------


def _parse_gold_case(case_file: Path) -> GoldCase:
    try:
        raw = yaml.safe_load(case_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CalibrationError(
            "bad_case", f"Gold case {case_file.name} is not valid YAML: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise CalibrationError(
            "bad_case", f"Gold case {case_file.name} is not a mapping"
        )

    missing = sorted(_GOLD_CASE_REQUIRED_FIELDS - set(raw))
    if missing:
        raise CalibrationError(
            "bad_case",
            f"Gold case {case_file.name} missing required fields: {missing}",
        )

    expected_gates = raw["expected_gates"]
    if not isinstance(expected_gates, dict) or not expected_gates:
        raise CalibrationError(
            "bad_case",
            f"Gold case {case_file.name}: expected_gates must be a non-empty "
            "map of gate -> PASS|FAIL",
        )
    gates: dict[str, str] = {}
    for gate, label in expected_gates.items():
        if str(label) not in _VALID_GATE_LABELS:
            raise CalibrationError(
                "bad_case",
                f"Gold case {case_file.name}: gate {gate!r} has label "
                f"{label!r}; labels must be PASS or FAIL",
            )
        gates[str(gate)] = str(label)

    if not isinstance(raw["spec_exemption_case"], bool):
        raise CalibrationError(
            "bad_case",
            f"Gold case {case_file.name}: spec_exemption_case must be a boolean",
        )

    input_path = (case_file.parent / str(raw["input_path"])).resolve()
    if not input_path.is_file():
        raise CalibrationError(
            "missing_fixture",
            f"Gold case {case_file.name}: input fixture not found: {input_path}",
        )

    # Content pin, checked BEFORE any judge ever sees the case: an unpinned
    # gold case silently measures the wrong thing. Line endings are
    # normalized to LF before hashing — the same G-19 convention as the
    # freeze authority's ``hash_artifact_content`` — so the pin names the
    # text, not the platform's byte encoding of it. Without this, a CRLF
    # checkout (windows-latest CI, git autocrlf=true) refuses every case.
    blob = input_path.read_bytes()
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CalibrationError(
            "bad_case",
            f"Gold case {case_file.name}: input fixture {input_path.name} is "
            f"not valid UTF-8: {exc}",
        ) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    pinned = str(raw["input_sha256"]).strip().lower()
    if digest != pinned:
        raise CalibrationError(
            "sha_mismatch",
            f"Gold case {raw['case_id']}: input fixture {input_path.name} does "
            f"not match its pin (pinned {pinned[:12]}..., on disk "
            f"{digest[:12]}...); load refused",
        )

    return GoldCase(
        case_id=str(raw["case_id"]),
        artifact_type=str(raw["artifact_type"]),
        validator=str(raw["validator"]),
        input_path=input_path,
        input_sha256=digest,
        expected_gates=gates,
        spec_exemption_case=raw["spec_exemption_case"],
        labeled_by=str(raw["labeled_by"]),
        labeled_date=str(raw["labeled_date"]),
        source=str(raw["source"]),
        dispute_ref=(str(raw["dispute_ref"]) if raw.get("dispute_ref") else None),
        # The judge sees the LF-normalized text on every platform: identical
        # input is the precondition the stability gate stands on.
        input_content=normalized,
    )


def _check_activation_floor(cases: list[GoldCase]) -> None:
    n_cases = len(cases)
    n_exempt = sum(1 for c in cases if c.spec_exemption_case)
    n_fail = sum(
        1 for c in cases if any(v == "FAIL" for v in c.expected_gates.values())
    )
    n_pass = sum(
        1 for c in cases if all(v == "PASS" for v in c.expected_gates.values())
    )

    problems: list[str] = []
    if n_cases < FLOOR_MIN_CASES:
        problems.append(f"{n_cases} cases (need >= {FLOOR_MIN_CASES})")
    if n_exempt < FLOOR_MIN_SPEC_EXEMPTION_CASES:
        problems.append(
            f"{n_exempt} spec-exemption cases "
            f"(need >= {FLOOR_MIN_SPEC_EXEMPTION_CASES})"
        )
    if n_pass < FLOOR_MIN_PASS_CASES:
        problems.append(f"{n_pass} gold-PASS cases (need >= {FLOOR_MIN_PASS_CASES})")
    if n_fail < FLOOR_MIN_FAIL_CASES:
        problems.append(f"{n_fail} gold-FAIL cases (need >= {FLOOR_MIN_FAIL_CASES})")

    if problems:
        raise CalibrationError(
            "below_floor",
            "Gold set is below the activation floor: "
            + "; ".join(problems)
            + ". Calibration refused; no lock may be written.",
        )


def load_gold_set(gold_dir: Path) -> list[GoldCase]:
    """Parse and pin-verify every gold case under ``gold_dir``.

    Raises :class:`CalibrationError` (a load refusal, never a warning) on a
    missing directory (``bad_gold_dir``), a malformed case (``bad_case``), a
    missing fixture (``missing_fixture``), a content-pin mismatch
    (``sha_mismatch``), or a set below the activation floor
    (``below_floor``). A caller that catches ``below_floor`` must not write
    a lock.
    """
    gold_dir = Path(gold_dir)
    if not gold_dir.is_dir():
        raise CalibrationError(
            "bad_gold_dir", f"Gold set directory not found: {gold_dir}"
        )

    case_files = sorted(gold_dir.rglob("*.yaml"))
    if not case_files:
        raise CalibrationError(
            "bad_gold_dir", f"No gold case YAML files found under {gold_dir}"
        )

    cases = [_parse_gold_case(f) for f in case_files]
    _check_activation_floor(cases)
    return cases


# ---------------------------------------------------------------------------
# Running the judge
# ---------------------------------------------------------------------------


def run_calibration(
    cases: list[GoldCase],
    judge: AgentAdapter,
    *,
    validator_prompt: str,
    spec_content: str = "",
    template_content: str = "",
    upstream_artifacts: Optional[dict[str, str]] = None,
) -> CalibrationRuns:
    """Judge every gold case exactly :data:`RUNS_PER_CASE` times.

    The judge is an injected :class:`AgentAdapter` — the same seam the
    :class:`ConvergenceLoop` uses, so calibration exercises the identical
    adapter machinery a real validation call does. Each of the three
    requests per case is byte-identical (same prompt, same fixture content)
    and pinned to temperature 0.0 structurally, mirroring the loop's judge
    pin: a flip across these runs is judge instability, not sampling noise.

    ``upstream_artifacts`` (DEF-007) closes the last context gap between a
    calibration call and a real validation call. It was hardcoded ``{}``
    from ``955b946`` until 2026-09-02, which made gates defined as
    comparisons against upstream unmeasurable: ``sad-validator``'s Required
    Inputs name "PRD (for intent comparison)", the prompt orders "evaluate
    only what is explicitly present", and a judge handed no PRD therefore
    PASSES intent gates by construction. That is the documented root cause
    of the safety-critical ``mutant-intent-integrity`` false pass -- six
    identical clean runs, zero variance. The CLI refuses rather than
    calibrating without it; see ``_collect_gold_upstream``.
    """
    if not cases:
        raise CalibrationError("bad_gold_set", "run_calibration got zero gold cases")

    prompt_sha256 = hashlib.sha256(validator_prompt.encode("utf-8")).hexdigest()
    provider = ""
    model = ""
    case_runs: list[CaseRuns] = []
    total_calls = len(cases) * RUNS_PER_CASE
    calls_made = 0
    # Same object every call: the judge's context must be byte-identical
    # across the three runs of a case, same as prompt and fixture.
    upstream = dict(upstream_artifacts or {})
    # Read off the REQUEST below, not off `upstream`. Deriving it here would
    # report the context this function was handed rather than the context the
    # judge received, and those diverge in exactly the case worth catching.
    delivered_upstream_ids: list[str] = []

    for case in cases:
        verdicts: list[dict[str, str]] = []
        evidence: list[dict] = []
        for run_index in range(RUNS_PER_CASE):
            request = AgentRequest(
                artifact_type=case.artifact_type,
                event=LifecycleEvent.PRE_VALIDATION,
                spec_content=spec_content,
                template_content=template_content,
                prompt_content=validator_prompt,
                upstream_artifacts=upstream,
                current_artifact=case.input_content,
                correction_constraints=[],
                metadata={
                    "case_id": case.case_id,
                    "calibration_run": str(run_index + 1),
                },
                temperature=0.0,
            )
            if not calls_made:
                delivered_upstream_ids = sorted(request.upstream_artifacts)
            # DEF-003 (narrow fix): a provider failure -- expired key, 401,
            # 400, transport error -- must not escape as an unhandled
            # exception. Unhandled, it skips the CLI's CalibrationError
            # handler entirely, so the run neither writes evidence nor
            # honors the calibrate exit-code contract (0/1/2/3): a failure
            # at call 30 of 36 burns the spend and reports a traceback.
            # Refusing here routes it to exit 2 like every other refusal.
            #
            # This stops the burn; it does not save the completed calls.
            # Those are still discarded, because run_calibration returns
            # nothing until every case finishes. The real fix is
            # incremental per-case writes -- see DEF-003 in the roadmap.
            try:
                response = judge.invoke(request)
            except Exception as exc:
                raise CalibrationError(
                    "provider_error",
                    f"Case {case.case_id} run {run_index + 1} "
                    f"(call {calls_made + 1} of {total_calls}): provider call "
                    f"failed: {type(exc).__name__}: {exc}. "
                    f"{calls_made} completed call(s) discarded -- no evidence "
                    f"written.",
                ) from exc
            calls_made += 1
            try:
                result = parse_validation_result(response)
            except ValueError as exc:
                raise CalibrationError(
                    "bad_judge_output",
                    f"Case {case.case_id} run {run_index + 1}: judge returned "
                    f"an unparseable verdict: {exc}",
                ) from exc
            verdicts.append(dict(result.hard_gates))
            # The judge's own reasons, kept verbatim. Recorded for dispute
            # analysis only -- scoring never sees this.
            evidence.append({
                "run": run_index + 1,
                "status": result.status,
                "summary": result.summary,
                "blocking_issues": list(result.blocking_issues),
            })
            provider = response.provider
            model = response.model
        case_runs.append(
            CaseRuns(case=case, gate_verdicts=verdicts, run_evidence=evidence)
        )

    return CalibrationRuns(
        validator=cases[0].validator,
        artifact_type=cases[0].artifact_type,
        provider=provider,
        model=model,
        prompt_sha256=prompt_sha256,
        case_runs=case_runs,
        upstream_artifact_ids=delivered_upstream_ids,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _cohens_kappa(gold: list[str], judged: list[str]) -> float:
    """Cohen's kappa of judge verdicts vs gold labels.

    Stored for drift trending ONLY (kappa_never_gates). The degenerate case
    (both raters constant, chance agreement 1.0) is defined as 1.0 on full
    agreement and 0.0 otherwise.
    """
    n = len(gold)
    if n == 0:
        return 0.0
    po = sum(1 for g, j in zip(gold, judged) if g == j) / n
    categories = set(gold) | set(judged)
    pe = sum(
        (gold.count(c) / n) * (judged.count(c) / n) for c in categories
    )
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def _verdict(
    role: str,
    gate_agreement: float,
    false_pass_count: int,
    stability_flips: list[dict[str, str]],
) -> str:
    """Apply the role's thresholds plus both hard gates.

    Deliberately has no kappa parameter: the verdict logic cannot consult a
    number it never receives (kappa_never_gates, test-enforced).
    """
    thresholds = THRESHOLDS[role]
    if stability_flips:
        return "FAIL"  # any_flip_fails — G-9 receipt 2, regardless of score
    false_pass_max = thresholds["false_pass_max"]
    if false_pass_max is not None and false_pass_count > false_pass_max:
        return "FAIL"  # zero_false_pass_freeze_gate
    if gate_agreement < thresholds["gate_agreement_min"]:
        return "FAIL"
    return "PASS"


def score(
    runs: CalibrationRuns,
    role: str,
    *,
    run_date: Optional[str] = None,
) -> CalibrationReport:
    """Score raw calibration runs into a :class:`CalibrationReport`.

    Agreement is the fraction of labeled gate verdicts the judge matched
    over all cases x gates x runs. ``false_pass_count`` counts (case, gate)
    pairs where the judge PASSed a gold-FAIL gate in ANY run.
    ``stability_flips`` lists every (case, gate) where the three identical
    runs disagreed. A gate the judge omitted counts as a disagreement (and
    participates in flip detection) — silence is not a verdict.
    """
    if role not in THRESHOLDS:
        raise CalibrationError(
            "bad_role",
            f"Unknown calibration role {role!r}; expected one of "
            f"{sorted(THRESHOLDS)}",
        )

    agreements = 0
    total = 0
    false_pass_count = 0
    stability_flips: list[dict[str, str]] = []
    gates_matrix: dict[str, dict[str, int]] = {}
    gold_labels: list[str] = []
    judge_labels: list[str] = []

    for case_run in runs.case_runs:
        for gate, expected in case_run.case.expected_gates.items():
            row = gates_matrix.setdefault(
                gate, {"agreements": 0, "total": 0, "false_pass": 0, "flips": 0}
            )
            verdicts = [rv.get(gate, _MISSING) for rv in case_run.gate_verdicts]
            for verdict in verdicts:
                total += 1
                row["total"] += 1
                gold_labels.append(expected)
                judge_labels.append(verdict)
                if verdict == expected:
                    agreements += 1
                    row["agreements"] += 1
            if expected == "FAIL" and any(v == "PASS" for v in verdicts):
                false_pass_count += 1
                row["false_pass"] += 1
            if len(set(verdicts)) > 1:
                stability_flips.append(
                    {"case_id": case_run.case.case_id, "gate": gate}
                )
                row["flips"] += 1

    gate_agreement = (agreements / total) if total else 0.0

    return CalibrationReport(
        report_version=REPORT_VERSION,
        validator=runs.validator,
        artifact_type=runs.artifact_type,
        role=role,
        judge={
            "provider": runs.provider,
            "model": runs.model,
            "prompt_sha256": runs.prompt_sha256,
        },
        run_date=run_date or _utcnow(),
        case_count=len(runs.case_runs),
        runs_per_case=RUNS_PER_CASE,
        gate_agreement=gate_agreement,
        false_pass_count=false_pass_count,
        stability_flips=stability_flips,
        kappa=_cohens_kappa(gold_labels, judge_labels),
        verdict=_verdict(role, gate_agreement, false_pass_count, stability_flips),
        gates=gates_matrix,
    )


# ---------------------------------------------------------------------------
# Report and lock writes
# ---------------------------------------------------------------------------


def _write_json_atomic(path: Path, payload: dict) -> None:
    """utf-8, LF, atomic-ish: write a sibling temp file, then os.replace."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def write_runs(runs: CalibrationRuns, path: Path) -> Path:
    """Write the raw per-case, per-run judge output (dispute evidence).

    The calibration report aggregates to gate totals, which is the right
    contract for a verdict and the wrong one for asking WHY a gate
    disagreed. This file is the per-run record behind those totals: for
    every gold case, each run's gate map plus the judge's own summary and
    blocking issues, stamped with the same judge identity triple.

    Optional and off by default (``--runs-out``). Nothing reads it
    programmatically -- not CI, not the conductor, not scoring. It exists so
    a human can answer the only question the aggregate cannot: on a gate the
    judge and the gold set disagree about, which one is wrong? Without it,
    the disagreement has to be re-purchased to be examined.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "runs_version": "1.0",
        "validator": runs.validator,
        "artifact_type": runs.artifact_type,
        "judge": {
            "provider": runs.provider,
            "model": runs.model,
            "prompt_sha256": runs.prompt_sha256,
        },
        "runs_per_case": RUNS_PER_CASE,
        # DEF-007: which upstream artifacts this run's judge actually had.
        # A dispute read months later cannot reconstruct context from the
        # verdicts alone -- the 08-30 false pass took a source dive to
        # explain, and this line is what would have answered it.
        "upstream_artifact_ids": list(runs.upstream_artifact_ids),
        "cases": [
            {
                "case_id": cr.case.case_id,
                "expected_gates": dict(cr.case.expected_gates),
                "spec_exemption_case": cr.case.spec_exemption_case,
                "labeled_by": cr.case.labeled_by,
                "labeled_date": cr.case.labeled_date,
                "dispute_ref": cr.case.dispute_ref,
                "gate_verdicts": [dict(v) for v in cr.gate_verdicts],
                "run_evidence": list(cr.run_evidence),
            }
            for cr in runs.case_runs
        ],
    }
    _write_json_atomic(path, payload)
    return path


def write_report(report: CalibrationReport, path: Path) -> Path:
    """Write the calibration report JSON (the evidence record).

    Written on PASS and FAIL alike — a failing calibration is exactly the
    evidence a human needs. Per-push CI never reads this file; it reads the
    lock (report_is_evidence_not_contract).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, asdict(report))
    return path


def write_lock(report: CalibrationReport, lock_path: Path, *, report_ref: str) -> Path:
    """Record a PASSING calibration in ``calibration.lock``.

    Refuses a FAIL verdict outright (``verdict_fail``): a failing
    calibration must never mint a lock entry, the same refusal shape as
    mark-status refusing FROZEN. Other validators' entries are preserved;
    an unreadable existing lock is rebuilt rather than bricking
    recalibration (the lock is derived state — the reports are the record).
    """
    if report.verdict != "PASS":
        raise CalibrationError(
            "verdict_fail",
            f"Calibration verdict for {report.validator} is "
            f"{report.verdict}; a failing calibration writes no lock",
        )

    lock_path = Path(lock_path)
    validators: dict[str, dict] = {}
    if lock_path.is_file():
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(
                existing.get("validators"), dict
            ):
                validators = dict(existing["validators"])
        except (json.JSONDecodeError, UnicodeDecodeError):
            validators = {}

    validators[report.validator] = {
        "prompt_sha256": report.judge["prompt_sha256"],
        "model": report.judge["model"],
        "gate_agreement": report.gate_agreement,
        "false_pass_count": report.false_pass_count,
        "calibrated_at": report.run_date,
        "report_ref": report_ref,
    }

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(
        lock_path,
        {"lock_version": CALIBRATION_LOCK_VERSION, "validators": validators},
    )
    return lock_path


# ---------------------------------------------------------------------------
# The deterministic CI contract
# ---------------------------------------------------------------------------


def check_lock(
    prompt_sha256: str,
    model: str,
    lock_path: Path,
    *,
    validator: str,
) -> LockCheckResult:
    """Pure staleness check of a validator's calibration lock entry.

    String/hash comparison only — zero LLM calls, zero network
    (hash_compare_only). This exact function is what kit-ci (slice 3) and
    the dark-factory conductor precondition (slice 4) call. A missing lock
    is stale by definition (committed_artifact).
    """
    lock_path = Path(lock_path)
    if not lock_path.is_file():
        return LockCheckResult(fresh=False, reason="missing_lock", validator=validator)

    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return LockCheckResult(fresh=False, reason="bad_lock", validator=validator)

    validators = data.get("validators") if isinstance(data, dict) else None
    entry = validators.get(validator) if isinstance(validators, dict) else None
    if not isinstance(entry, dict):
        return LockCheckResult(
            fresh=False, reason="unknown_validator", validator=validator
        )

    if str(entry.get("prompt_sha256", "")) != prompt_sha256:
        return LockCheckResult(
            fresh=False, reason="prompt_changed", validator=validator, entry=entry
        )
    if str(entry.get("model", "")) != model:
        return LockCheckResult(
            fresh=False, reason="model_changed", validator=validator, entry=entry
        )
    return LockCheckResult(fresh=True, reason="fresh", validator=validator, entry=entry)
