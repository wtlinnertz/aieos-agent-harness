"""Freeze authority: the single code path that writes ``FROZEN`` (ADR-0002).

``apply_freeze_decision`` is the one function, across all three drivers (sherpa,
console, dark factory), permitted to promote an artifact to ``FROZEN``. The
console reaches it through the ``harness freeze`` CLI subcommand (ADR-0003); the
dark factory reaches it through :class:`src.driver.HarnessDriver`. A conductor
can drive an artifact to ``FREEZE_PENDING`` and no further -- it has no code path
that writes ``FROZEN``.

The andon cord writes ``HALTED``/``FAULTED`` and a resume routes around this
function entirely (ADR-0004); the freeze invariant is never a backdoor.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from src.invariants import check_authorized_freeze
from src.models import (
    ArtifactStatus,
    DecisionOutcome,
    FreezeGateDecision,
    FreezeResult,
    LifecycleEvent,
)
from src.state import (
    append_journal_entry,
    find_artifact_path,
    read_er_state_block,
    upsert_document_control_rows,
    write_artifact_status,
    write_er_state_block,
)

# Outcomes that authorize promotion to FROZEN. BLOCK, REMEDIATE_AND_RETRY,
# REQUIRE_REDESIGN, and ROLLBACK never freeze.
_APPROVING_OUTCOMES = frozenset(
    {DecisionOutcome.APPROVE, DecisionOutcome.APPROVE_WITH_CONDITIONS}
)


class FreezeError(Exception):
    """A freeze was refused. Carries a stable ``code`` so a caller (the CLI,
    ADR-0003) can emit a structured error the console can branch on, distinct
    from an unexpected crash. When raised, nothing has been written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def hash_artifact_content(text: str) -> str:
    """SHA-256 hex digest of artifact text -- the identity a decision pins.

    Matches the digest convention used elsewhere in the harness (the mock
    adapter's ``input_content_hash``), so the console can compute the same hash
    over the artifact it shows the human.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utctoday() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def apply_freeze_decision(
    initiative_path: Path,
    decision: FreezeGateDecision,
    *,
    er_path: Optional[Path] = None,
    journal_path: Optional[Path] = None,
    on_post_freeze: Optional[Callable[[FreezeResult], None]] = None,
) -> FreezeResult:
    """Promote one artifact to ``FROZEN`` -- the single ``FROZEN`` writer.

    Guards, checked in order; any failure raises :class:`FreezeError` and writes
    nothing:

    1. ``check_authorized_freeze`` -- ``decided_by`` present, not an auto-freeze.
    2. The outcome must approve promotion (APPROVE / APPROVE_WITH_CONDITIONS).
    3. The decision's ``content_hash`` must match the artifact on disk.
    4. If ``er_path`` / ``journal_path`` are given, they must already exist
       (disk-based-state invariant) -- checked before any write so a partial
       write cannot happen.

    On success it writes the Document Control ``Status`` cell to ``FROZEN``
    plus the provenance rows the schema requires at FROZEN (FR-018 D1):
    ``Owner`` (the decision's ``owner``, defaulting to ``decided_by``),
    ``Frozen By`` (``decided_by``), and ``Frozen Date`` (today, UTC). It then
    increments the ER frozen count (if ``er_path`` given), appends a
    ``POST_FREEZE`` Journal entry (if ``journal_path`` given), fires the optional
    ``on_post_freeze`` hook, and returns a :class:`FreezeResult`.
    """
    # Guard 1: authorization invariant (pure over the decision).
    auth = check_authorized_freeze(decision)
    if not auth.passed:
        raise FreezeError("unauthorized", auth.reason)

    # Guard 2: outcome must authorize a freeze.
    if decision.outcome not in _APPROVING_OUTCOMES:
        raise FreezeError(
            "not_approved",
            f"Decision outcome {decision.outcome.value} does not authorize a "
            f"freeze for {decision.artifact_id}",
        )

    # Guard 3: locate the artifact and verify the content hash against disk.
    try:
        target = find_artifact_path(initiative_path, decision.artifact_id)
    except ValueError as exc:
        raise FreezeError("not_found", str(exc)) from exc

    disk_hash = hash_artifact_content(target.read_text(encoding="utf-8"))
    if disk_hash != decision.content_hash:
        raise FreezeError(
            "hash_mismatch",
            f"Artifact {decision.artifact_id} changed since the decision was "
            f"made (decision {decision.content_hash[:12]}..., "
            f"on disk {disk_hash[:12]}...); freeze refused",
        )

    # Guard 4: side-effect targets must exist before we write anything.
    if er_path is not None and not er_path.exists():
        raise FreezeError("state_error", f"ER file not found: {er_path}")
    if journal_path is not None and not journal_path.exists():
        raise FreezeError("state_error", f"Journal file not found: {journal_path}")

    # --- Writes begin here; all guards have passed. ---
    written = write_artifact_status(
        initiative_path, decision.artifact_id, ArtifactStatus.FROZEN
    )

    # D1: the freeze authority is the writer of the FROZEN provenance tuple.
    # Owner defaults to the freeze approver unless the decision names a
    # distinct owner; a FROZEN block therefore always satisfies
    # frozen_requires_provenance without templates carrying these at DRAFT.
    owner = (decision.owner or "").strip() or decision.decided_by
    artifact_text = written.read_text(encoding="utf-8")
    artifact_text = upsert_document_control_rows(
        artifact_text,
        {
            "Owner": owner,
            "Frozen By": decision.decided_by,
            "Frozen Date": _utctoday(),
        },
    )
    written.write_text(artifact_text, encoding="utf-8")

    frozen_count: Optional[int] = None
    if er_path is not None:
        er_state = read_er_state_block(er_path)
        er_state.frozen_count += 1
        er_state.last_updated = _utcnow()
        write_er_state_block(er_path, er_state)
        frozen_count = er_state.frozen_count

    if journal_path is not None:
        append_journal_entry(
            journal_path,
            "Freeze",
            {
                "Artifact": decision.artifact_id,
                "Event": LifecycleEvent.POST_FREEZE.value,
                "Outcome": decision.outcome.value,
                "Decided By": decision.decided_by,
                "Content Hash": decision.content_hash,
            },
        )

    result = FreezeResult(
        artifact_id=decision.artifact_id,
        status=ArtifactStatus.FROZEN,
        path=str(written),
        decided_by=decision.decided_by,
        frozen_count=frozen_count,
        owner=owner,
    )

    if on_post_freeze is not None:
        on_post_freeze(result)

    return result
