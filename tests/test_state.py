"""Tests for state management (ER state blocks, journal, frozen artifacts)."""

from pathlib import Path

import pytest

from src.models import ArtifactStatus, ERStateBlock
from src.state import (
    append_journal_entry,
    is_artifact_frozen,
    read_er_state_block,
    read_frozen_artifacts,
    read_journal_entries,
    upsert_document_control_rows,
    write_artifact_status,
    write_er_state_block,
)


CANONICAL_BLOCK = (
    "# SAD\n\n"
    "## Document Control\n\n"
    "| Field | Value |\n"
    "|-------|-------|\n"
    "| Artifact ID | SAD-TEST-001 |\n"
    "| Owner | {owner} |\n"
    "| Status | DRAFT |\n\n"
    "## Body\n\nContent.\n"
)


class TestUpsertDocumentControlRows:
    """FR-018 D1/D2: the Document Control block grows through the lifecycle."""

    def test_updates_existing_row_in_place(self):
        out = upsert_document_control_rows(
            CANONICAL_BLOCK, {"Status": "FREEZE_PENDING"}
        )
        assert "| Status | FREEZE_PENDING |" in out
        assert "| Status | DRAFT |" not in out

    def test_replaces_owner_placeholder_without_duplicating(self):
        out = upsert_document_control_rows(CANONICAL_BLOCK, {"Owner": "Todd"})
        assert "| Owner | Todd |" in out
        assert out.count("| Owner |") == 1

    def test_appends_missing_rows_to_table_end(self):
        out = upsert_document_control_rows(
            CANONICAL_BLOCK,
            {"Frozen By": "Todd", "Frozen Date": "2026-07-25"},
        )
        # New rows land inside the table (before the ## Body section), in order.
        assert out.index("| Frozen By | Todd |") < out.index("## Body")
        assert out.index("| Frozen By |") < out.index("| Frozen Date | 2026-07-25 |")
        assert out.index("| Frozen Date |") < out.index("## Body")

    def test_mixed_update_and_insert(self):
        out = upsert_document_control_rows(
            CANONICAL_BLOCK,
            {"Status": "FROZEN", "Frozen By": "Todd", "Frozen Date": "2026-07-25"},
        )
        assert "| Status | FROZEN |" in out
        assert out.count("| Status |") == 1
        assert "| Frozen By | Todd |" in out

    def test_refuses_text_without_artifact_id_row(self):
        with pytest.raises(ValueError):
            upsert_document_control_rows("# Just prose\n", {"Owner": "Todd"})

    def test_label_match_is_exact_not_substring(self):
        # "Status" must not clobber a row whose label merely contains it.
        text = CANONICAL_BLOCK.replace(
            "| Status | DRAFT |", "| ADR Status | Proposed |\n| Status | DRAFT |"
        )
        out = upsert_document_control_rows(text, {"Status": "FREEZE_PENDING"})
        assert "| ADR Status | Proposed |" in out
        assert "| Status | FREEZE_PENDING |" in out


class TestERStateBlockRoundTrip:
    def test_read_state_block(self, tmp_er_file):
        block = read_er_state_block(tmp_er_file)
        assert block.current_layer == "Layer 4 (EEK)"
        assert block.current_artifact == "TDD-TEST-001"
        assert block.current_step == "Generation"
        assert block.frozen_count == 3
        assert block.next_action == "Generate TDD"
        assert block.blocking_on == "None"
        assert block.last_updated == "2026-03-25T10:00:00Z"

    def test_write_then_read_state_block(self, tmp_er_file):
        new_state = ERStateBlock(
            current_layer="Layer 5 (REK)",
            current_artifact="RER-TEST-001",
            current_step="Validation",
            frozen_count=5,
            next_action="Validate RER",
            blocking_on="QGR-TEST-001",
            last_updated="2026-03-25T12:00:00Z",
        )
        write_er_state_block(tmp_er_file, new_state)
        readback = read_er_state_block(tmp_er_file)
        assert readback.current_layer == "Layer 5 (REK)"
        assert readback.current_artifact == "RER-TEST-001"
        assert readback.frozen_count == 5
        assert readback.blocking_on == "QGR-TEST-001"

    def test_write_preserves_other_content(self, tmp_er_file):
        original = tmp_er_file.read_text()
        assert "Initiative Summary" in original

        new_state = ERStateBlock(
            current_layer="Layer 6 (RRK)",
            current_artifact="RHR-TEST-001",
            current_step="Generation",
            frozen_count=7,
            next_action="Generate RHR",
            blocking_on="None",
            last_updated="2026-03-25T14:00:00Z",
        )
        write_er_state_block(tmp_er_file, new_state)
        updated = tmp_er_file.read_text()
        assert "Initiative Summary" in updated
        assert "Layer 2 Artifacts" in updated


class TestFrozenArtifacts:
    def test_read_frozen_artifacts(self, tmp_initiative):
        artifacts = read_frozen_artifacts(tmp_initiative)
        assert artifacts["PRD-TEST-001"] == ArtifactStatus.FROZEN
        assert artifacts["ACF-TEST-001"] == ArtifactStatus.FROZEN
        assert artifacts["SAD-TEST-001"] == ArtifactStatus.DRAFT
        assert len(artifacts) == 3

    def test_is_artifact_frozen_true(self, tmp_initiative):
        assert is_artifact_frozen(tmp_initiative, "PRD-TEST-001") is True

    def test_is_artifact_frozen_false(self, tmp_initiative):
        assert is_artifact_frozen(tmp_initiative, "SAD-TEST-001") is False

    def test_is_artifact_frozen_missing(self, tmp_initiative):
        assert is_artifact_frozen(tmp_initiative, "NONEXISTENT-001") is False

    def test_read_frozen_no_sdlc_dir(self, tmp_path):
        result = read_frozen_artifacts(tmp_path)
        assert result == {}


class TestJournal:
    def test_read_journal_entries(self, tmp_journal_file):
        entries = read_journal_entries(tmp_journal_file)
        assert len(entries) == 2
        assert entries[0]["entry_type"] == "Invocation"
        assert entries[0]["Artifact"] == "PRD-TEST-001"
        assert entries[0]["Provider"] == "anthropic"
        assert entries[1]["entry_type"] == "Validation"
        assert entries[1]["Status"] == "PASS"

    def test_append_and_read_journal(self, tmp_journal_file):
        append_journal_entry(
            tmp_journal_file,
            "Convergence",
            {"Artifact": "TDD-TEST-001", "Iteration": "2", "Result": "FAIL"},
        )
        entries = read_journal_entries(tmp_journal_file)
        assert len(entries) == 3
        assert entries[2]["entry_type"] == "Convergence"
        assert entries[2]["Artifact"] == "TDD-TEST-001"
        assert entries[2]["Iteration"] == "2"

    def test_append_to_empty_journal(self, tmp_path):
        journal = tmp_path / "journal.md"
        journal.write_text("# Sherpa Journal\n")
        append_journal_entry(
            journal,
            "Invocation",
            {"Artifact": "PRD-X-001", "Event": "POST_GENERATION"},
        )
        entries = read_journal_entries(journal)
        assert len(entries) == 1
        assert entries[0]["Artifact"] == "PRD-X-001"


class TestReadFrozenArtifactsEdgeCases:
    def _make_sdlc(self, tmp_path, name, content):
        d = tmp_path / "docs" / "sdlc"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(content)
        return tmp_path

    def test_block_without_artifact_id_is_skipped(self, tmp_path):
        root = self._make_sdlc(tmp_path, "01.md", "| Status | Frozen |\n")
        assert read_frozen_artifacts(root) == {}

    def test_block_without_status_is_skipped(self, tmp_path):
        root = self._make_sdlc(tmp_path, "01.md", "| Artifact ID | PRD-X-001 |\n")
        assert read_frozen_artifacts(root) == {}

    def test_invalid_status_falls_back_to_draft(self, tmp_path):
        root = self._make_sdlc(
            tmp_path, "01.md", "| Artifact ID | PRD-X-001 |\n| Status | Bogus |\n"
        )
        assert read_frozen_artifacts(root) == {"PRD-X-001": ArtifactStatus.DRAFT}


class TestJournalSeparatorRows:
    def test_dashed_separator_rows_are_skipped(self, tmp_path):
        journal = tmp_path / "journal.md"
        journal.write_text(
            "### Invocation -- 2026-03-25T10:00:00Z\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| ---- | ---- |\n"
            "| Artifact | PRD-X-001 |\n"
        )
        entries = read_journal_entries(journal)
        assert len(entries) == 1
        assert entries[0]["Artifact"] == "PRD-X-001"
        assert "----" not in entries[0]


class TestWriteArtifactStatus:
    def _make_artifact(self, tmp_path, name, artifact_id, status_text, extra=""):
        d = tmp_path / "docs" / "sdlc"
        d.mkdir(parents=True, exist_ok=True)
        content = (
            "## Document Control\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            f"| Artifact ID | {artifact_id} |\n"
            f"| Status | {status_text} |\n"
            f"{extra}"
        )
        (d / name).write_text(content)
        return tmp_path

    def test_write_then_read_roundtrip(self, tmp_path):
        root = self._make_artifact(tmp_path, "05-sad.md", "SAD-X-001", "Draft")
        write_artifact_status(root, "SAD-X-001", ArtifactStatus.FROZEN)
        assert read_frozen_artifacts(root)["SAD-X-001"] == ArtifactStatus.FROZEN

    def test_fault_states_roundtrip(self, tmp_path):
        # The new andon states (ADR-0004) must survive the on-disk write->read cycle.
        root = self._make_artifact(tmp_path, "05-sad.md", "SAD-X-001", "VALIDATED")
        write_artifact_status(root, "SAD-X-001", ArtifactStatus.HALTED)
        assert read_frozen_artifacts(root)["SAD-X-001"] == ArtifactStatus.HALTED
        write_artifact_status(root, "SAD-X-001", ArtifactStatus.FAULTED)
        assert read_frozen_artifacts(root)["SAD-X-001"] == ArtifactStatus.FAULTED

    def test_returns_written_path(self, tmp_path):
        root = self._make_artifact(tmp_path, "05-sad.md", "SAD-X-001", "Draft")
        p = write_artifact_status(root, "SAD-X-001", ArtifactStatus.FROZEN)
        assert p.name == "05-sad.md"

    def test_preserves_other_content(self, tmp_path):
        root = self._make_artifact(
            tmp_path, "05-sad.md", "SAD-X-001", "Draft",
            extra="\n## Body\n\nArchitecture details here.\n",
        )
        write_artifact_status(root, "SAD-X-001", ArtifactStatus.FROZEN)
        text = (root / "docs" / "sdlc" / "05-sad.md").read_text()
        assert "Architecture details here." in text
        assert "| Artifact ID | SAD-X-001 |" in text

    def test_locates_correct_file_among_many(self, tmp_path):
        self._make_artifact(tmp_path, "03-prd.md", "PRD-X-001", "Frozen")
        self._make_artifact(tmp_path, "05-sad.md", "SAD-X-001", "Draft")
        write_artifact_status(tmp_path, "SAD-X-001", ArtifactStatus.FROZEN)
        arts = read_frozen_artifacts(tmp_path)
        assert arts["SAD-X-001"] == ArtifactStatus.FROZEN
        assert arts["PRD-X-001"] == ArtifactStatus.FROZEN  # untouched

    def test_unknown_artifact_id_raises(self, tmp_path):
        root = self._make_artifact(tmp_path, "05-sad.md", "SAD-X-001", "Draft")
        with pytest.raises(ValueError, match="No artifact with ID"):
            write_artifact_status(root, "NOPE-001", ArtifactStatus.FROZEN)

    def test_no_status_cell_raises(self, tmp_path):
        d = tmp_path / "docs" / "sdlc"
        d.mkdir(parents=True, exist_ok=True)
        (d / "05-sad.md").write_text("| Artifact ID | SAD-X-001 |\n")
        with pytest.raises(ValueError, match="no Status cell"):
            write_artifact_status(tmp_path, "SAD-X-001", ArtifactStatus.FROZEN)

    def test_no_sdlc_dir_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No docs/sdlc"):
            write_artifact_status(tmp_path, "SAD-X-001", ArtifactStatus.FROZEN)


class TestNonAsciiArtifactsRoundTrip:
    """G-6: text I/O must declare encoding="utf-8" on BOTH sides.

    Real kit files are UTF-8 and contain smart quotes and em dashes -- the
    dogfood on 2026-07-14 died reading aieos-engineering-execution's
    prd-template.md on the "Success" curly quote (U+201D) because read_text()
    fell back to the Windows locale encoding (cp1252).

    These assert the round trip explicitly rather than trusting the ambient
    locale, so they fail on a Windows runner if anyone drops an encoding= again.
    A codebase that is wrong everywhere still round-trips; being right in only
    one place is what breaks -- which is exactly how this surfaced.
    """

    SMART = 'Curly "quotes", an em dash —, a section §, and café.'

    def _artifact(self, tmp_path, body):
        d = tmp_path / "docs" / "sdlc"
        d.mkdir(parents=True, exist_ok=True)
        (d / "05-sad.md").write_text(
            "## Document Control\n\n"
            "| Artifact ID | SAD-X-001 |\n"
            "| Status | DRAFT |\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_read_frozen_artifacts_reads_utf8_bodies(self, tmp_path):
        root = self._artifact(tmp_path, self.SMART)
        assert read_frozen_artifacts(root)["SAD-X-001"] == ArtifactStatus.DRAFT

    def test_write_artifact_status_preserves_non_ascii(self, tmp_path):
        root = self._artifact(tmp_path, self.SMART)
        write_artifact_status(root, "SAD-X-001", ArtifactStatus.FROZEN)
        text = (root / "docs" / "sdlc" / "05-sad.md").read_text(encoding="utf-8")
        assert self.SMART in text
        assert read_frozen_artifacts(root)["SAD-X-001"] == ArtifactStatus.FROZEN

    def test_er_state_block_round_trips_non_ascii(self, tmp_path):
        er = tmp_path / "er.md"
        er.write_text(
            "## 1b. Current State\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Current Layer | Layer 4 — EEK |\n"
            "| Current Artifact | SAD-X-001 |\n"
            "| Current Step | Generation |\n"
            "| Frozen Count | 1 |\n"
            "| Next Action | Generate “SAD” |\n"
            "| Blocking On | None |\n"
            "| Last Updated | 2026-07-16T00:00:00Z |\n",
            encoding="utf-8",
        )
        block = read_er_state_block(er)
        assert "—" in block.current_layer
        write_er_state_block(er, block)
        assert "—" in read_er_state_block(er).current_layer

    def test_journal_round_trips_non_ascii(self, tmp_path):
        journal = tmp_path / "journal.md"
        journal.write_text("# Sherpa Journal\n", encoding="utf-8")
        append_journal_entry(journal, "Freeze", {"Note": self.SMART})
        assert read_journal_entries(journal)[0]["Note"] == self.SMART


class TestFindArtifactPath:
    def test_no_sdlc_dir_raises(self, tmp_path):
        from src.state import find_artifact_path

        with pytest.raises(ValueError, match="No docs/sdlc"):
            find_artifact_path(tmp_path, "SAD-X-001")

    def test_unknown_id_raises(self, tmp_path):
        from src.state import find_artifact_path

        d = tmp_path / "docs" / "sdlc"
        d.mkdir(parents=True)
        (d / "01.md").write_text("| Artifact ID | PRD-X-001 |\n| Status | DRAFT |\n")
        with pytest.raises(ValueError, match="No artifact with ID"):
            find_artifact_path(tmp_path, "NOPE-001")

    def test_finds_matching_file(self, tmp_path):
        from src.state import find_artifact_path

        d = tmp_path / "docs" / "sdlc"
        d.mkdir(parents=True)
        (d / "05-sad.md").write_text("| Artifact ID | SAD-X-001 |\n| Status | DRAFT |\n")
        assert find_artifact_path(tmp_path, "SAD-X-001").name == "05-sad.md"


class TestStateWritesAreLf:
    """G-19: every state write is LF on every platform (``newline="\n"``).

    The default ``write_text`` translated LF to ``os.linesep`` -- CRLF on
    Windows -- which is how dark-factory artifacts became CRLF on disk in the
    first place. Hash normalization makes CRLF artifacts freezable anyway;
    these tests keep the writers from manufacturing new ones. On Windows CI
    they fail without ``newline="\n"``; on POSIX they are vacuous but cheap.
    """

    def test_write_artifact_status_rewrites_crlf_file_as_lf(self, tmp_path):
        sdlc = tmp_path / "docs" / "sdlc"
        sdlc.mkdir(parents=True)
        crlf = CANONICAL_BLOCK.replace("\n", "\r\n")
        (sdlc / "05-sad.md").write_bytes(crlf.encode("utf-8"))
        target = write_artifact_status(
            tmp_path, "SAD-TEST-001", ArtifactStatus.FREEZE_PENDING
        )
        assert b"\r" not in target.read_bytes()

    def test_write_er_state_block_is_lf(self, tmp_path):
        er = tmp_path / "er.md"
        er.write_bytes(
            b"| Current Layer | Layer 4 |\r\n| Frozen Count | 1 |\r\n"
            b"| Last Updated | 2026-07-11T10:00:00Z |\r\n"
        )
        state = read_er_state_block(er)
        state.frozen_count += 1
        write_er_state_block(er, state)
        assert b"\r" not in er.read_bytes()

    def test_append_journal_entry_appends_lf(self, tmp_path):
        journal = tmp_path / "journal.md"
        journal.write_bytes(b"# Journal\r\n")
        append_journal_entry(journal, "Freeze", {"Artifact": "SAD-TEST-001"})
        appended = journal.read_bytes().split(b"### Freeze")[1]
        assert b"\r" not in appended
