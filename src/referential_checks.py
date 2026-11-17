"""Referential check tier for validator gates (FR-012; FR-014 build spec section 4h, step A).

Some hard gates ask the judge to confirm that something is present: an
upstream artifact ID, a real value instead of a placeholder. Those are
lookups, not judgment, and the LLM judge skips them. FR-014 section 4g
recorded every presence-check note failing across two prompt iterations.
These checks run in code, after the judge, on the same inputs the judge
received.

Combining rule: a referential FAIL overrides a judge PASS on its gate. A
referential pass never overrides a judge FAIL, because finding no
placeholder proves nothing about the gate's semantic half.

Registered for SAD only. Other artifact types get checks once they have a
gold set to measure against (decision 2026-11-15).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from src.models import ValidationResult

TIER = "referential"


@dataclass(frozen=True)
class ReferentialFinding:
    gate: str
    check: str
    description: str
    location: str


# sad-spec.md, Upstream Artifact Traceability: "PRD reference must be
# present" and "ACF reference must be present". Other delivered upstream
# artifacts are not required citations, and production delivers every
# frozen artifact in the initiative, so they must not be checked.
_SAD_REQUIRED_UPSTREAM_PREFIXES = ("PRD-", "ACF-")


def check_upstream_ids(
    artifact: str, upstream_artifacts: dict[str, str]
) -> list[ReferentialFinding]:
    """R2: every delivered PRD and ACF artifact ID must appear in the SAD."""
    findings: list[ReferentialFinding] = []
    for artifact_id in sorted(upstream_artifacts):
        if not artifact_id.upper().startswith(_SAD_REQUIRED_UPSTREAM_PREFIXES):
            continue
        cited = re.search(rf"(?<![\w-]){re.escape(artifact_id)}(?![\w-])", artifact)
        if not cited:
            findings.append(
                ReferentialFinding(
                    gate="upstream_traceability",
                    check="R2-upstream-id",
                    description=(
                        f"Upstream artifact {artifact_id} was provided but is "
                        "not cited anywhere in the SAD."
                    ),
                    location="Document Control / upstream references",
                )
            )
    return findings


_DEFERRED_HEADING = re.compile(r"^#{1,6}\s*(?:\d+\.\s*)?deferred decisions\b", re.IGNORECASE)
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
# Whole-cell placeholders only. sad-spec.md: "No unexplained TBD entries",
# failure example "Placeholder-only decisions". A cell with real text that
# mentions TBD is left to the judge.
_PLACEHOLDERS = {"", "tbd", "tba", "tbc", "todo", "to be determined", "?", "-"}


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_placeholder(cell: str) -> bool:
    value = cell.strip("*_` .").lower()
    return value in _PLACEHOLDERS or set(value) <= {"?"} or set(value) <= {"-", "—", "–"}


def _deferred_rows(artifact: str) -> list[list[str]]:
    rows: list[list[str]] = []
    in_section = False
    for line in artifact.splitlines():
        if line.lstrip().startswith("#"):
            in_section = bool(_DEFERRED_HEADING.match(line.strip()))
            continue
        if in_section and line.lstrip().startswith("|"):
            rows.append(_cells(line))
    return rows


def check_deferred_placeholders(artifact: str) -> list[ReferentialFinding]:
    """R3: no deferred decision may carry a placeholder reason or target."""
    rows = _deferred_rows(artifact)
    if len(rows) < 2:
        return []
    header = [h.lower() for h in rows[0]]
    reason_idx = next((i for i, h in enumerate(header) if "reason" in h), 1)
    target_idx = next(
        (i for i, h in enumerate(header) if "target" in h or "phase" in h), 2
    )
    findings: list[ReferentialFinding] = []
    for row in rows[1:]:
        if all(_SEPARATOR_CELL.match(c) for c in row if c):
            continue
        for label, idx in (("reason", reason_idx), ("target resolution phase", target_idx)):
            value = row[idx] if idx < len(row) else ""
            if _is_placeholder(value):
                findings.append(
                    ReferentialFinding(
                        gate="deferred_decisions",
                        check="R3-deferred-placeholder",
                        description=(
                            f"Deferred decision '{row[0]}' has a placeholder "
                            f"{label}: '{value}'."
                        ),
                        location="Deferred Decisions",
                    )
                )
    return findings


Check = Callable[[str, dict[str, str]], list[ReferentialFinding]]

_REGISTRY: dict[str, tuple[Check, ...]] = {
    "SAD": (
        check_upstream_ids,
        lambda artifact, _upstream: check_deferred_placeholders(artifact),
    ),
}


def run_referential_checks(
    artifact_type: str, artifact: str, upstream_artifacts: dict[str, str]
) -> list[ReferentialFinding]:
    """Run every check registered for ``artifact_type``; unregistered types get none."""
    checks = _REGISTRY.get(artifact_type.upper(), ())
    return [f for check in checks for f in check(artifact, upstream_artifacts)]


def apply_referential_findings(
    result: ValidationResult, findings: list[ReferentialFinding]
) -> ValidationResult:
    """Return a new result with each finding's gate failed; never passes a gate."""
    if not findings:
        return result
    gates = dict(result.hard_gates)
    issues = list(result.blocking_issues)
    for finding in findings:
        gates[finding.gate] = "FAIL"
        issues.append(
            {
                "gate": finding.gate,
                "description": finding.description,
                "location": finding.location,
                "tier": TIER,
                "check": finding.check,
            }
        )
    return replace(result, status="FAIL", hard_gates=gates, blocking_issues=issues)
