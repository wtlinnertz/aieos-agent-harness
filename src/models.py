"""Core data models for the AIEOS Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ArtifactStatus(Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    FREEZE_PENDING = "FREEZE_PENDING"
    FROZEN = "FROZEN"
    # Andon-cord fault states (ADR-0004). Written only by the andon cord, never
    # by a freeze path. HALTED = clean stop, resumable once the triggering
    # condition clears. FAULTED = governance breach; needs a recorded human
    # clear before any resume. Mirrors schema/document-control.yaml (FR-018).
    HALTED = "HALTED"
    FAULTED = "FAULTED"


class LifecycleEvent(Enum):
    PRE_GENERATION = "PRE_GENERATION"
    POST_GENERATION = "POST_GENERATION"
    PRE_VALIDATION = "PRE_VALIDATION"
    POST_VALIDATION = "POST_VALIDATION"
    POST_FREEZE = "POST_FREEZE"
    ON_FAILURE = "ON_FAILURE"


class RoutingStrategy(Enum):
    PARALLEL_CONSENSUS = "PARALLEL_CONSENSUS"
    PIPELINE = "PIPELINE"
    FALLBACK = "FALLBACK"
    COST_AWARE = "COST_AWARE"


class AgentSpecies(Enum):
    CODING_HARNESS = "CODING_HARNESS"
    DARK_FACTORY = "DARK_FACTORY"
    ORCHESTRATION = "ORCHESTRATION"
    AUTO_RESEARCH = "AUTO_RESEARCH"


class HealthStatus(Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class DecisionOutcome(Enum):
    APPROVE = "APPROVE"
    APPROVE_WITH_CONDITIONS = "APPROVE_WITH_CONDITIONS"
    BLOCK = "BLOCK"
    REMEDIATE_AND_RETRY = "REMEDIATE_AND_RETRY"
    REQUIRE_REDESIGN = "REQUIRE_REDESIGN"
    ROLLBACK = "ROLLBACK"


@dataclass
class AgentRequest:
    artifact_type: str
    event: LifecycleEvent
    spec_content: str
    template_content: str
    prompt_content: str
    upstream_artifacts: dict[str, str]
    current_artifact: Optional[str]
    correction_constraints: list[str]
    metadata: dict[str, str]
    # G-3/G-5 (manifest 1.1): manifest-declared non-upstream inputs, resolved
    # by src.inputs.resolve_declared_inputs -- principles files (framework)
    # and the entry brief (human), keyed "<role>: <ref>".
    declared_inputs: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentResponse:
    content: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    raw_response: Optional[dict] = None
    # G-7: did the provider stop because it hit the output ceiling?
    # Normalized here on purpose: every provider spells it differently
    # (Anthropic stop_reason == "max_tokens", OpenAI finish_reason == "length"),
    # and the convergence loop must not reach into provider-shaped raw_response
    # to find out. None = the provider didn't say.
    #
    # A truncated artifact is structurally incomplete, so it can NEVER pass
    # validation -- retrying it identically just burns the whole convergence
    # budget and reports a generic escalation indistinguishable from a real
    # quality failure. That is exactly what happened on 2026-07-14: the signal
    # was sitting in raw_response and nothing read it.
    truncated: Optional[bool] = None
    # Five-element provenance (AI SDLC Governance — Human Oversight gate 5)
    human_author: Optional[str] = None
    input_content_hash: Optional[str] = None
    modification_record: Optional[list[dict]] = None
    compliance_attestation: Optional[str] = None


@dataclass
class ValidationResult:
    status: str
    summary: str
    hard_gates: dict[str, str]
    blocking_issues: list[dict]
    warnings: list[dict]
    completeness_score: int


@dataclass
class ERStateBlock:
    current_layer: str
    current_artifact: str
    current_step: str
    frozen_count: int
    next_action: str
    blocking_on: str
    last_updated: str


@dataclass
class InvocationRecord:
    timestamp: str
    artifact_type: str
    artifact_id: str
    event: LifecycleEvent
    provider: str
    model: str
    strategy: RoutingStrategy
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    result: str
    validation_status: Optional[str]
    convergence_iteration: int
    error: Optional[str] = None
    species: str = ""


@dataclass
class ConvergenceState:
    artifact_id: str
    artifact_type: str
    max_iterations: int = 3
    current_iteration: int = 0
    ledger: list[dict] = field(default_factory=list)


@dataclass
class InvariantCheck:
    name: str
    passed: bool
    reason: str


class LifecycleResult(Enum):
    """Result of driving one artifact through its generate/validate lifecycle.

    The return contract of ``HarnessDriver.run_artifact_lifecycle`` (ADR-0002).
    CONVERGED = validation reached PASS within the convergence budget.
    ESCALATION_NEEDED = the budget was exhausted without a PASS; a human is
    needed. The conductor never turns either result into a FROZEN write.
    ALREADY_FROZEN = the target artifact is already FROZEN, so there is nothing
    to do and nothing may be written (G-13). A frozen artifact is immutable:
    regenerating over it would destroy a human-approved decision. Returned
    BEFORE any provider call, so a re-walk of a partially-frozen initiative is
    both safe and free. Callers treat it as "this node is done, move on".
    """

    CONVERGED = "CONVERGED"
    ESCALATION_NEEDED = "ESCALATION_NEEDED"
    ALREADY_FROZEN = "ALREADY_FROZEN"


@dataclass
class FreezeGateDecision:
    """A human's freeze-gate decision -- the input to ``apply_freeze_decision``.

    This is the serialized record the console and dark factory hand to the
    ``harness freeze`` CLI (ADR-0003). ``apply_freeze_decision`` is the single
    authority that turns an *approving* decision into a ``FROZEN`` write; the
    conductor produces this record but never writes ``FROZEN`` itself (ADR-0002).

    - ``artifact_id`` -- the artifact to freeze (matches its Document Control ID).
    - ``outcome`` -- the human's DecisionOutcome. Only APPROVE /
      APPROVE_WITH_CONDITIONS authorize a freeze.
    - ``content_hash`` -- SHA-256 hex of the artifact the human actually saw.
      ``apply_freeze_decision`` rejects the freeze if the on-disk artifact no
      longer matches, so an artifact that changed under the decision cannot be
      frozen silently (ADR-0003 decision integrity).
    - ``decided_by`` -- the human identity accountable for the decision. Required.
    - ``auto_freeze_attempted`` -- True only if an automated path tried to freeze
      without a human decision; such a decision is always refused.
    - ``conditions`` -- optional conditions attached to APPROVE_WITH_CONDITIONS.
    - ``rationale`` -- optional free-text justification.
    - ``owner`` -- optional distinct accountable owner written to the Document
      Control ``| Owner |`` row at freeze (FR-018 D1). Defaults to
      ``decided_by`` when absent.
    """

    artifact_id: str
    outcome: DecisionOutcome
    content_hash: str
    decided_by: str
    auto_freeze_attempted: bool = False
    conditions: list[str] = field(default_factory=list)
    rationale: str = ""
    owner: Optional[str] = None


@dataclass
class FreezeResult:
    """The outcome of a successful ``apply_freeze_decision`` write."""

    artifact_id: str
    status: ArtifactStatus
    path: str
    decided_by: str
    frozen_count: Optional[int] = None
    owner: Optional[str] = None
