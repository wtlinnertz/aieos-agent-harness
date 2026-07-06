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
    write_er_state_block,
)


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
