"""Data models for the CI/CD capability substrate.

RegistryEntry — adapter identity, capabilities, attestation ref, health status.
RegistrationOutcome — result of register_adapter.
TaskResult / AdapterResult — agent + adapter return shapes (used in M2.3).
Event payloads — structured stdout events (used in M2.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class TaskStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=False)
class RegistryEntry:
    """One row in the capability registry.

    adapter_id — e.g., "adapter-pytest-unit".
    adapter_version — semver string.
    capabilities — list of action identifiers this adapter claims.
    contract_versions — map action -> contract version semver the adapter is
        attested against.
    attestation_ref — reference to the conformance attestation that backs this
        registration. Resolvable by the attestation verifier at registration
        time.
    registered_at — timestamp when this entry was registered.
    context — environment tags (e.g., {"environment": "ci"}) used for
        context-scoped lookups.
    health_status — coarse health signal; lookups may prefer healthy entries.
    """

    adapter_id: str
    adapter_version: str
    capabilities: list[str]
    contract_versions: dict[str, str]
    attestation_ref: str
    registered_at: datetime
    context: dict[str, str] = field(default_factory=dict)
    health_status: HealthStatus = HealthStatus.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "capabilities": list(self.capabilities),
            "contract_versions": dict(self.contract_versions),
            "attestation_ref": self.attestation_ref,
            "registered_at": self.registered_at.isoformat(),
            "context": dict(self.context),
            "health_status": self.health_status.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegistryEntry:
        return cls(
            adapter_id=data["adapter_id"],
            adapter_version=data["adapter_version"],
            capabilities=list(data["capabilities"]),
            contract_versions=dict(data["contract_versions"]),
            attestation_ref=data["attestation_ref"],
            registered_at=datetime.fromisoformat(data["registered_at"]),
            context=dict(data.get("context", {})),
            health_status=HealthStatus(data.get("health_status", HealthStatus.HEALTHY.value)),
        )


@dataclass
class RegistrationOutcome:
    """Outcome of a register_adapter call."""

    accepted: bool
    diagnostic: str = ""


@dataclass
class AdapterResult:
    """What an adapter returns after executing one task.

    findings — canonical findings payload, or None for non-findings actions.
    evidence — list of evidence artifact references.
    exit_code — process-style exit code (0 success).
    """

    findings: dict[str, Any] | None
    evidence: list[str]
    exit_code: int


@dataclass
class TaskResult:
    """What an agent returns to the run orchestrator for one task."""

    action: str
    adapter_id: str
    findings: dict[str, Any] | None
    evidence: list[str]
    status: TaskStatus
    error: str | None = None
