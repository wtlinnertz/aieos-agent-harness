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
