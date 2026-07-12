"""Tests for the single freeze authority (apply_freeze_decision, ADR-0002)."""

from pathlib import Path

import pytest

from src.freeze import FreezeError, apply_freeze_decision, hash_artifact_content
from src.models import ArtifactStatus, DecisionOutcome, FreezeGateDecision
from src.state import read_frozen_artifacts, read_journal_entries


ER_CONTENT = """\
# Engagement Record: ER-TEST-001

## 1b. Current State

| Field | Value |
|-------|-------|
| Current Layer | Layer 4 (EEK) |
| Current Artifact | SAD-TEST-001 |
| Current Step | Freeze |
| Frozen Count | 2 |
| Next Action | Freeze SAD |
| Blocking On | None |
| Last Updated | 2026-07-11T10:00:00Z |
"""


def _make_initiative(tmp_path, status="FREEZE_PENDING", with_er=True, with_journal=True):
    """Build an initiative with one artifact plus optional ER + journal.

    Returns (root, artifact_id, content_hash) where content_hash is the digest
    of the artifact as written to disk.
    """
    sdlc = tmp_path / "docs" / "sdlc"
    sdlc.mkdir(parents=True)
    artifact = (
        "# SAD\n\n"
        "## Document Control\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Artifact ID | SAD-TEST-001 |\n"
        f"| Status | {status} |\n\n"
        "## Body\n\nArchitecture details.\n"
    )
    (sdlc / "05-sad.md").write_text(artifact)
    content_hash = hash_artifact_content((sdlc / "05-sad.md").read_text())

    if with_er:
        eng = tmp_path / "docs" / "engagement"
        eng.mkdir(parents=True, exist_ok=True)
        (eng / "er.md").write_text(ER_CONTENT)
    if with_journal:
        eng = tmp_path / "docs" / "engagement"
        eng.mkdir(parents=True, exist_ok=True)
        (eng / "journal.md").write_text("# Sherpa Journal: TEST-001\n")

    return tmp_path, "SAD-TEST-001", content_hash


def _decision(artifact_id, content_hash, **kw):
    return FreezeGateDecision(
        artifact_id=artifact_id,
        outcome=kw.get("outcome", DecisionOutcome.APPROVE),
        content_hash=content_hash,
        decided_by=kw.get("decided_by", "Todd Linnertz"),
        auto_freeze_attempted=kw.get("auto_freeze_attempted", False),
        conditions=kw.get("conditions", []),
        rationale=kw.get("rationale", ""),
    )


class TestApplyFreezeHappyPath:
    def test_approve_writes_frozen(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        er = root / "docs" / "engagement" / "er.md"
        journal = root / "docs" / "engagement" / "journal.md"
        result = apply_freeze_decision(root, _decision(aid, h), er_path=er, journal_path=journal)
        assert result.status == ArtifactStatus.FROZEN
        assert read_frozen_artifacts(root)[aid] == ArtifactStatus.FROZEN

    def test_increments_frozen_count(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        er = root / "docs" / "engagement" / "er.md"
        result = apply_freeze_decision(root, _decision(aid, h), er_path=er)
        assert result.frozen_count == 3  # was 2

    def test_appends_post_freeze_journal_entry(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        journal = root / "docs" / "engagement" / "journal.md"
        apply_freeze_decision(root, _decision(aid, h), journal_path=journal)
        entries = read_journal_entries(journal)
        assert entries[-1]["entry_type"] == "Freeze"
        assert entries[-1]["Event"] == "POST_FREEZE"
        assert entries[-1]["Decided By"] == "Todd Linnertz"

    def test_fires_post_freeze_hook(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        seen = []
        apply_freeze_decision(root, _decision(aid, h), on_post_freeze=seen.append)
        assert len(seen) == 1
        assert seen[0].status == ArtifactStatus.FROZEN

    def test_approve_with_conditions_freezes(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        d = _decision(aid, h, outcome=DecisionOutcome.APPROVE_WITH_CONDITIONS, conditions=["ship note"])
        result = apply_freeze_decision(root, d)
        assert result.status == ArtifactStatus.FROZEN

    def test_works_without_er_or_journal(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path, with_er=False, with_journal=False)
        result = apply_freeze_decision(root, _decision(aid, h))
        assert result.frozen_count is None
        assert read_frozen_artifacts(root)[aid] == ArtifactStatus.FROZEN


class TestApplyFreezeRefused:
    def test_hash_mismatch_raises_and_writes_nothing(self, tmp_path):
        root, aid, _h = _make_initiative(tmp_path)
        with pytest.raises(FreezeError) as exc:
            apply_freeze_decision(root, _decision(aid, "deadbeef" * 8))
        assert exc.value.code == "hash_mismatch"
        # nothing written: status unchanged
        assert read_frozen_artifacts(root)[aid] == ArtifactStatus.FREEZE_PENDING

    def test_auto_freeze_is_unauthorized(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        with pytest.raises(FreezeError) as exc:
            apply_freeze_decision(root, _decision(aid, h, auto_freeze_attempted=True))
        assert exc.value.code == "unauthorized"
        assert read_frozen_artifacts(root)[aid] == ArtifactStatus.FREEZE_PENDING

    def test_missing_decided_by_is_unauthorized(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        with pytest.raises(FreezeError) as exc:
            apply_freeze_decision(root, _decision(aid, h, decided_by="  "))
        assert exc.value.code == "unauthorized"

    def test_non_approving_outcome_refused(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        with pytest.raises(FreezeError) as exc:
            apply_freeze_decision(root, _decision(aid, h, outcome=DecisionOutcome.BLOCK))
        assert exc.value.code == "not_approved"
        assert read_frozen_artifacts(root)[aid] == ArtifactStatus.FREEZE_PENDING

    def test_unknown_artifact_not_found(self, tmp_path):
        root, _aid, h = _make_initiative(tmp_path)
        with pytest.raises(FreezeError) as exc:
            apply_freeze_decision(root, _decision("NOPE-001", h))
        assert exc.value.code == "not_found"

    def test_missing_er_file_refused_before_write(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path, with_er=False)
        with pytest.raises(FreezeError) as exc:
            apply_freeze_decision(root, _decision(aid, h), er_path=root / "docs" / "engagement" / "er.md")
        assert exc.value.code == "state_error"
        # guard fired before any status write
        assert read_frozen_artifacts(root)[aid] == ArtifactStatus.FREEZE_PENDING


class TestApplyFreezeMissingJournal:
    def test_missing_journal_file_refused_before_write(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path, with_journal=False)
        with pytest.raises(FreezeError) as exc:
            apply_freeze_decision(
                root, _decision(aid, h),
                journal_path=root / "docs" / "engagement" / "journal.md",
            )
        assert exc.value.code == "state_error"
        assert read_frozen_artifacts(root)[aid] == ArtifactStatus.FREEZE_PENDING


class TestHashHelper:
    def test_hash_is_sha256_hex(self, tmp_path):
        h = hash_artifact_content("hello")
        assert len(h) == 64
        assert h == hash_artifact_content("hello")
