"""Cross-repo integration: harness Document Control writes must satisfy the
canonical FR-018 schema (``aieos-schema/scripts/validate-document-control.py``).

The unit suites prove each side in isolation; these tests prove the two repos
AGREE — the exact seam where G-2 hid (writers and validator drifting apart
with nothing exercising them together).

The schema repo is located via ``AIEOS_SCHEMA_PATH``, then ``./aieos-schema``
(the CI sibling checkout), then ``../aieos-schema`` (local dev layout); the
tests skip when it is absent so the unit suite stays self-contained.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.adapters.mock import MockAdapter
from src.driver import HarnessDriver
from src.freeze import apply_freeze_decision, hash_artifact_content
from src.models import DecisionOutcome, FreezeGateDecision

REPO_ROOT = Path(__file__).resolve().parents[2]


def _schema_repo() -> Path | None:
    env = os.environ.get("AIEOS_SCHEMA_PATH")
    candidates = [Path(env)] if env else []
    candidates += [REPO_ROOT / "aieos-schema", REPO_ROOT.parent / "aieos-schema"]
    for c in candidates:
        if (c / "scripts" / "validate-document-control.py").is_file():
            return c
    return None


SCHEMA_REPO = _schema_repo()
pytestmark = pytest.mark.skipif(
    SCHEMA_REPO is None,
    reason="aieos-schema repo not found (set AIEOS_SCHEMA_PATH)",
)

# The canonical D2 template block: Artifact ID + Owner placeholder + DRAFT.
TEMPLATE_BLOCK = (
    "# SAD\n\n"
    "## Document Control\n\n"
    "| Field | Value |\n"
    "|-------|-------|\n"
    "| Artifact ID | SAD-{PROJECT}-001 |\n"
    "| Owner | {owner} |\n"
    "| Status | DRAFT |\n\n"
    "## Body\n\nArchitecture.\n"
)


def _validate(*paths: Path) -> subprocess.CompletedProcess:
    validator = SCHEMA_REPO / "scripts" / "validate-document-control.py"
    return subprocess.run(
        [sys.executable, str(validator), *map(str, paths)],
        capture_output=True,
        text=True,
    )


def test_persist_freeze_pending_output_is_schema_conformant(tmp_path):
    """Template-authored content driven to FREEZE_PENDING: one block, valid."""
    initiative = tmp_path / "init"
    initiative.mkdir()
    driver = HarnessDriver(initiative, MockAdapter(), MockAdapter())
    written = driver._persist_freeze_pending("SAD", TEMPLATE_BLOCK)
    text = written.read_text(encoding="utf-8")
    assert text.count("## Document Control") == 1
    proc = _validate(written)
    assert proc.returncode == 0, proc.stderr


def test_frozen_artifact_is_schema_conformant(tmp_path):
    """apply_freeze_decision output satisfies frozen_requires_provenance."""
    sdlc = tmp_path / "docs" / "sdlc"
    sdlc.mkdir(parents=True)
    artifact = TEMPLATE_BLOCK.replace(
        "| Status | DRAFT |", "| Status | FREEZE_PENDING |"
    ).replace("SAD-{PROJECT}-001", "SAD-E2E-001")
    (sdlc / "05-sad.md").write_text(artifact, encoding="utf-8")
    h = hash_artifact_content((sdlc / "05-sad.md").read_text(encoding="utf-8"))

    result = apply_freeze_decision(
        tmp_path,
        FreezeGateDecision(
            artifact_id="SAD-E2E-001",
            outcome=DecisionOutcome.APPROVE,
            content_hash=h,
            decided_by="Todd Linnertz",
        ),
    )

    frozen = Path(result.path)
    text = frozen.read_text(encoding="utf-8")
    assert text.count("## Document Control") == 1
    assert "| Owner | Todd Linnertz |" in text
    proc = _validate(frozen)
    assert proc.returncode == 0, proc.stderr
