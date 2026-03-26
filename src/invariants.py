"""Seven invariant checks for the AIEOS Agent Harness.

Each check is a pure function returning an InvariantCheck.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from src.models import (
    ConvergenceState,
    InvariantCheck,
    LifecycleEvent,
    ValidationResult,
)
from src import state as state_module

# ---------------------------------------------------------------------------
# Upstream dependency map: artifact_type -> list of required frozen artifact
# type prefixes.  E.g. "SAD" requires a frozen PRD.
# ---------------------------------------------------------------------------
UPSTREAM_DEPENDENCIES: dict[str, list[str]] = {
    "PRD": [],
    "ACF": ["PRD"],
    "SAD": ["PRD"],
    "TDD": ["ACF", "SAD"],
    "WDD": ["TDD"],
    "ORD": ["WDD"],
    "QAER": ["ORD"],
    "VP": ["QAER"],
    "TCR": ["VP"],
    "QGR": ["TCR"],
    "RER": ["ORD"],
    "RCF": ["RER"],
    "RP": ["RCF"],
    "RR": ["RP"],
    "RHR": ["RR"],
    "SDR": ["DPRD"],
    "DPRD": [],
    "SOER": ["DPRD"],
    "VER": ["SOER"],
    "TM": ["SAD"],
    "SAR": ["TDD"],
    "DAR": ["TDD"],
    "CER": [],
    "CSPEC": ["TDD"],
    "FFLR": [],
    "DSR": ["TDD"],
    "PDR": [],
    "ISPEC": [],
    "EM": [],
    "UDR": [],
    "ARR": ["TDD"],
    "SKA": [],
    "DHR": [],
}


def check_generation_validation_separation(
    gen_event: LifecycleEvent, val_event: LifecycleEvent
) -> InvariantCheck:
    """Generation and validation must be different lifecycle events."""
    passed = gen_event != val_event
    reason = (
        "Generation and validation use separate lifecycle events"
        if passed
        else f"Same event '{gen_event.value}' used for both generation and validation"
    )
    return InvariantCheck(
        name="generation_validation_separation",
        passed=passed,
        reason=reason,
    )


def check_freeze_before_promote(
    initiative_path: Path,
    artifact_type: str,
    upstream_deps: dict[str, list[str]],
) -> InvariantCheck:
    """All upstream artifacts must be frozen before downstream generation."""
    required = upstream_deps.get(artifact_type, [])
    if not required:
        return InvariantCheck(
            name="freeze_before_promote",
            passed=True,
            reason=f"No upstream dependencies for '{artifact_type}'",
        )

    frozen = state_module.read_frozen_artifacts(initiative_path)
    frozen_types = set()
    for aid, status in frozen.items():
        from src.models import ArtifactStatus

        if status == ArtifactStatus.FROZEN:
            # Extract type prefix from artifact ID (e.g. "PRD-INIT-001" -> "PRD")
            parts = aid.split("-")
            if parts:
                frozen_types.add(parts[0])

    missing = [r for r in required if r not in frozen_types]
    passed = len(missing) == 0
    reason = (
        f"All upstream deps frozen for '{artifact_type}'"
        if passed
        else f"Missing frozen upstream artifacts: {missing}"
    )
    return InvariantCheck(
        name="freeze_before_promote",
        passed=passed,
        reason=reason,
    )


def check_human_freeze_decision(auto_freeze_attempted: bool) -> InvariantCheck:
    """Freeze must be a human decision -- auto-freeze is never allowed."""
    passed = not auto_freeze_attempted
    reason = (
        "Freeze decision is human-controlled"
        if passed
        else "Auto-freeze attempted -- freeze requires human approval"
    )
    return InvariantCheck(
        name="human_freeze_decision",
        passed=passed,
        reason=reason,
    )


def check_bounded_convergence(cstate: ConvergenceState) -> InvariantCheck:
    """Current iteration must not exceed max_iterations."""
    passed = cstate.current_iteration <= cstate.max_iterations
    reason = (
        f"Iteration {cstate.current_iteration}/{cstate.max_iterations} within bounds"
        if passed
        else f"Iteration {cstate.current_iteration} exceeds max {cstate.max_iterations}"
    )
    return InvariantCheck(
        name="bounded_convergence",
        passed=passed,
        reason=reason,
    )


# Words that indicate suggestions (validators must not contain these)
_SUGGESTION_PATTERNS = re.compile(
    r"\b(consider|suggest|recommend|you might|you could|try|perhaps)\b",
    re.IGNORECASE,
)


def check_validator_output_format(
    response_text: str,
) -> tuple[InvariantCheck, Optional[ValidationResult]]:
    """Parse JSON validation output and verify required fields.

    Rejects responses containing suggestion language (validators judge,
    they do not help).
    """
    name = "validator_output_format"

    # Attempt JSON parse
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError as exc:
        return (
            InvariantCheck(name=name, passed=False, reason=f"Invalid JSON: {exc}"),
            None,
        )

    # Required fields
    required = ["status", "summary", "hard_gates", "blocking_issues", "warnings", "completeness_score"]
    missing = [f for f in required if f not in data]
    if missing:
        return (
            InvariantCheck(
                name=name,
                passed=False,
                reason=f"Missing required fields: {missing}",
            ),
            None,
        )

    # Status must be PASS or FAIL
    if data["status"] not in ("PASS", "FAIL"):
        return (
            InvariantCheck(
                name=name,
                passed=False,
                reason=f"Status must be PASS or FAIL, got '{data['status']}'",
            ),
            None,
        )

    # Check for suggestion language in summary
    if _SUGGESTION_PATTERNS.search(data.get("summary", "")):
        return (
            InvariantCheck(
                name=name,
                passed=False,
                reason="Validator output contains suggestion language",
            ),
            None,
        )

    result = ValidationResult(
        status=data["status"],
        summary=data["summary"],
        hard_gates=data["hard_gates"],
        blocking_issues=data["blocking_issues"],
        warnings=data["warnings"],
        completeness_score=int(data["completeness_score"]),
    )

    return (
        InvariantCheck(name=name, passed=True, reason="Valid validator output"),
        result,
    )


# Provider-specific terms that must not appear in governance content
_TOOL_SPECIFIC_TERMS = re.compile(
    r"\b(OpenAI|Anthropic|Claude|GPT|ChatGPT|Gemini|Copilot)\b"
)


def check_tool_agnostic_policy(content: str) -> InvariantCheck:
    """Scan governance content for provider-specific references."""
    match = _TOOL_SPECIFIC_TERMS.search(content)
    passed = match is None
    reason = (
        "No provider-specific references found"
        if passed
        else f"Provider-specific reference found: '{match.group()}'"
    )
    return InvariantCheck(
        name="tool_agnostic_policy",
        passed=passed,
        reason=reason,
    )


def check_disk_based_state(er_path: Path, journal_path: Path) -> InvariantCheck:
    """Both the ER file and journal file must exist on disk."""
    er_exists = er_path.exists()
    journal_exists = journal_path.exists()
    passed = er_exists and journal_exists
    missing = []
    if not er_exists:
        missing.append("ER")
    if not journal_exists:
        missing.append("journal")
    reason = (
        "Both ER and journal files exist on disk"
        if passed
        else f"Missing files: {', '.join(missing)}"
    )
    return InvariantCheck(
        name="disk_based_state",
        passed=passed,
        reason=reason,
    )
