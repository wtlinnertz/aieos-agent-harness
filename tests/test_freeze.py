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
        owner=kw.get("owner"),
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


class TestFreezeWritesProvenance:
    """FR-018 D1: the freeze authority writes the FROZEN provenance tuple --
    Owner (defaulting to the approver), Frozen By, Frozen Date -- so a FROZEN
    block always satisfies frozen_requires_provenance."""

    def test_owner_defaults_to_decided_by(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        result = apply_freeze_decision(root, _decision(aid, h))
        text = Path(result.path).read_text()
        assert "| Owner | Todd Linnertz |" in text
        assert result.owner == "Todd Linnertz"

    def test_distinct_owner_when_decision_names_one(self, tmp_path):
        root, aid, h = _make_initiative(tmp_path)
        result = apply_freeze_decision(
            root, _decision(aid, h, owner="Platform Team")
        )
        text = Path(result.path).read_text()
        assert "| Owner | Platform Team |" in text
        assert "| Frozen By | Todd Linnertz |" in text
        assert result.owner == "Platform Team"

    def test_frozen_by_and_frozen_date_written(self, tmp_path):
        import re

        root, aid, h = _make_initiative(tmp_path)
        result = apply_freeze_decision(root, _decision(aid, h))
        text = Path(result.path).read_text()
        assert "| Frozen By | Todd Linnertz |" in text
        assert re.search(r"\|\s*Frozen Date\s*\|\s*\d{4}-\d{2}-\d{2}\s*\|", text)

    def test_owner_placeholder_row_is_replaced_not_duplicated(self, tmp_path):
        # A template-authored artifact carries `| Owner | {owner} |` (D2).
        sdlc = tmp_path / "docs" / "sdlc"
        sdlc.mkdir(parents=True)
        artifact = (
            "# SAD\n\n"
            "## Document Control\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Artifact ID | SAD-TEST-001 |\n"
            "| Owner | {owner} |\n"
            "| Status | FREEZE_PENDING |\n\n"
            "## Body\n\nArchitecture details.\n"
        )
        (sdlc / "05-sad.md").write_text(artifact)
        h = hash_artifact_content((sdlc / "05-sad.md").read_text())
        result = apply_freeze_decision(tmp_path, _decision("SAD-TEST-001", h))
        text = Path(result.path).read_text()
        assert text.count("| Owner |") == 1
        assert "| Owner | Todd Linnertz |" in text
        assert "{owner}" not in text


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


# The cross-language hash contract fixture (G-19). The LF and CRLF spellings
# are the same text; both must produce the pinned digest.
G19_FIXTURE_LF = (
    "# SAD\n\n| Artifact ID | SAD-TEST-001 |\n| Status | FREEZE_PENDING |\n"
)
G19_FIXTURE_CRLF = G19_FIXTURE_LF.replace("\n", "\r\n")
G19_FIXTURE_DIGEST = (
    "fce70731c15162436e1d5c70294e025229fa74726bac0332e8a9f70e03437b6f"
)


class TestHashHelper:
    def test_hash_is_sha256_hex(self, tmp_path):
        h = hash_artifact_content("hello")
        assert len(h) == 64
        assert h == hash_artifact_content("hello")

    def test_crlf_and_lf_hash_identically(self):
        assert hash_artifact_content("a\r\nb\r\n") == hash_artifact_content("a\nb\n")

    def test_lone_cr_normalizes_to_lf(self):
        assert hash_artifact_content("a\rb") == hash_artifact_content("a\nb")

    def test_pinned_cross_language_digest(self):
        """G-19 contract: the same literal digest is pinned in the console's
        harness-freeze-service.test.ts. If this assertion has to change, the
        two sides no longer agree on artifact identity -- update both or
        neither."""
        assert hash_artifact_content(G19_FIXTURE_LF) == G19_FIXTURE_DIGEST
        assert hash_artifact_content(G19_FIXTURE_CRLF) == G19_FIXTURE_DIGEST


class TestFreezeCrlfArtifact:
    """G-19: a CRLF-stored artifact freezes through the console convention.

    The console hashes the exact text it showed the human (line endings as
    stored on disk); the freeze authority hashes ``read_text()`` output, where
    universal newlines have already folded CRLF to LF. Before LF normalization
    lived inside ``hash_artifact_content``, those two digests disagreed on
    every CRLF artifact -- the default state of anything ``write_text`` had
    produced on Windows -- and every console freeze was refused with
    ``hash_mismatch``. These tests drive a real CRLF file end to end.
    """

    def _crlf_initiative(self, tmp_path):
        """Returns (root, artifact_id, shown_content) with CRLF files on disk,
        exactly as a pre-G-19 Windows ``write_text`` left them."""
        sdlc = tmp_path / "docs" / "sdlc"
        sdlc.mkdir(parents=True)
        shown = (
            "# SAD\r\n\r\n"
            "## Document Control\r\n\r\n"
            "| Field | Value |\r\n"
            "|-------|-------|\r\n"
            "| Artifact ID | SAD-TEST-001 |\r\n"
            "| Status | FREEZE_PENDING |\r\n\r\n"
            "## Body\r\n\r\nArchitecture details.\r\n"
        )
        (sdlc / "05-sad.md").write_bytes(shown.encode("utf-8"))

        eng = tmp_path / "docs" / "engagement"
        eng.mkdir(parents=True)
        (eng / "er.md").write_bytes(
            ER_CONTENT.replace("\n", "\r\n").encode("utf-8")
        )
        (eng / "journal.md").write_bytes(b"# Sherpa Journal: TEST-001\r\n")
        return tmp_path, "SAD-TEST-001", shown

    def test_console_convention_hash_freezes_crlf_artifact(self, tmp_path):
        root, aid, shown = self._crlf_initiative(tmp_path)
        er = root / "docs" / "engagement" / "er.md"
        journal = root / "docs" / "engagement" / "journal.md"
        # The console computes the decision hash over the raw shown content,
        # CRLF and all.
        result = apply_freeze_decision(
            root,
            _decision(aid, hash_artifact_content(shown)),
            er_path=er,
            journal_path=journal,
        )
        assert result.status == ArtifactStatus.FROZEN
        assert read_frozen_artifacts(root)[aid] == ArtifactStatus.FROZEN

    def test_frozen_artifact_rewritten_as_lf(self, tmp_path):
        root, aid, shown = self._crlf_initiative(tmp_path)
        result = apply_freeze_decision(
            root, _decision(aid, hash_artifact_content(shown))
        )
        assert b"\r" not in Path(result.path).read_bytes()

    def test_er_rewritten_lf_and_journal_appends_lf(self, tmp_path):
        root, aid, shown = self._crlf_initiative(tmp_path)
        er = root / "docs" / "engagement" / "er.md"
        journal = root / "docs" / "engagement" / "journal.md"
        apply_freeze_decision(
            root,
            _decision(aid, hash_artifact_content(shown)),
            er_path=er,
            journal_path=journal,
        )
        # The ER is rewritten whole, so the entire file comes out LF. The
        # journal only appends; the pre-existing CRLF header stays, but the
        # appended Freeze entry must be LF.
        assert b"\r" not in er.read_bytes()
        appended = journal.read_bytes().split(b"### Freeze")[1]
        assert b"\r" not in appended
