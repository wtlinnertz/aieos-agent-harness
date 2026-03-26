"""Shared fixtures for AIEOS Agent Harness tests."""

import pytest
from pathlib import Path

SAMPLE_ER_CONTENT = """\
# Engagement Record: ER-TEST-001

## 1a. Initiative Summary

| Field | Value |
|-------|-------|
| Initiative | TEST-001 |
| Description | Test initiative |

## 1b. Current State

| Field | Value |
|-------|-------|
| Current Layer | Layer 4 (EEK) |
| Current Artifact | TDD-TEST-001 |
| Current Step | Generation |
| Frozen Count | 3 |
| Next Action | Generate TDD |
| Blocking On | None |
| Last Updated | 2026-03-25T10:00:00Z |

## 2. Layer 2 Artifacts

| Artifact | ID | Status |
|----------|----|--------|
| PRD | PRD-TEST-001 | FROZEN |
"""

SAMPLE_JOURNAL_CONTENT = """\
# Sherpa Journal: TEST-001

### Invocation -- 2026-03-25T09:00:00Z
| Field | Value |
|-------|-------|
| Artifact | PRD-TEST-001 |
| Event | POST_GENERATION |
| Provider | anthropic |
| Result | success |

### Validation -- 2026-03-25T09:30:00Z
| Field | Value |
|-------|-------|
| Artifact | PRD-TEST-001 |
| Event | POST_VALIDATION |
| Status | PASS |
| Score | 92 |
"""


@pytest.fixture
def sample_er_content():
    return SAMPLE_ER_CONTENT


@pytest.fixture
def sample_journal_content():
    return SAMPLE_JOURNAL_CONTENT


@pytest.fixture
def tmp_er_file(tmp_path):
    er_file = tmp_path / "er-TEST-001.md"
    er_file.write_text(SAMPLE_ER_CONTENT)
    return er_file


@pytest.fixture
def tmp_journal_file(tmp_path):
    journal_file = tmp_path / "journal-TEST-001.md"
    journal_file.write_text(SAMPLE_JOURNAL_CONTENT)
    return journal_file


@pytest.fixture
def tmp_initiative(tmp_path):
    """Create a temp initiative directory with docs/sdlc/ containing mock artifacts."""
    sdlc_dir = tmp_path / "docs" / "sdlc"
    sdlc_dir.mkdir(parents=True)

    # Frozen PRD
    (sdlc_dir / "01-prd.md").write_text(
        "# PRD\n\n"
        "## Document Control\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Artifact ID | PRD-TEST-001 |\n"
        "| Status | FROZEN |\n"
    )

    # Frozen ACF
    (sdlc_dir / "02-acf.md").write_text(
        "# ACF\n\n"
        "## Document Control\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Artifact ID | ACF-TEST-001 |\n"
        "| Status | FROZEN |\n"
    )

    # Draft SAD
    (sdlc_dir / "03-sad.md").write_text(
        "# SAD\n\n"
        "## Document Control\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Artifact ID | SAD-TEST-001 |\n"
        "| Status | DRAFT |\n"
    )

    return tmp_path
