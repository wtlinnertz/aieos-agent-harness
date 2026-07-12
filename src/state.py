"""State manager for ER state blocks and Sherpa Journal.

All operations read/write Markdown files on disk -- no database.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.models import ArtifactStatus, ERStateBlock


def read_er_state_block(er_path: Path) -> ERStateBlock:
    """Parse the ``| Field | Value |`` table from section 1b in the ER markdown.

    Expects rows like ``| Current Layer | Layer 4 (EEK) |``.
    """
    text = er_path.read_text()

    def _extract(field_name: str) -> str:
        pattern = rf"\|\s*{re.escape(field_name)}\s*\|\s*(.*?)\s*\|"
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    return ERStateBlock(
        current_layer=_extract("Current Layer"),
        current_artifact=_extract("Current Artifact"),
        current_step=_extract("Current Step"),
        frozen_count=int(_extract("Frozen Count") or "0"),
        next_action=_extract("Next Action"),
        blocking_on=_extract("Blocking On"),
        last_updated=_extract("Last Updated"),
    )


def write_er_state_block(er_path: Path, state: ERStateBlock) -> None:
    """Find and replace the state block table in the ER markdown in-place."""
    text = er_path.read_text()

    field_map = {
        "Current Layer": state.current_layer,
        "Current Artifact": state.current_artifact,
        "Current Step": state.current_step,
        "Frozen Count": str(state.frozen_count),
        "Next Action": state.next_action,
        "Blocking On": state.blocking_on,
        "Last Updated": state.last_updated,
    }

    for field_name, value in field_map.items():
        pattern = rf"(\|\s*{re.escape(field_name)}\s*\|)\s*.*?\s*\|"
        replacement = rf"\1 {value} |"
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    er_path.write_text(text)


def read_frozen_artifacts(initiative_path: Path) -> dict[str, ArtifactStatus]:
    """Scan docs/sdlc/*.md for the Status field in Document Control.

    Returns a mapping of artifact_id -> ArtifactStatus for every
    artifact file that contains a parseable Status field.
    """
    sdlc_dir = initiative_path / "docs" / "sdlc"
    results: dict[str, ArtifactStatus] = {}

    if not sdlc_dir.exists():
        return results

    for md_file in sorted(sdlc_dir.glob("*.md")):
        text = md_file.read_text()

        # Extract artifact ID
        id_match = re.search(
            r"\|\s*Artifact\s+ID\s*\|\s*(.*?)\s*\|", text, re.IGNORECASE
        )
        if not id_match:
            continue
        artifact_id = id_match.group(1).strip()

        # Extract status
        status_match = re.search(
            r"\|\s*Status\s*\|\s*(.*?)\s*\|", text, re.IGNORECASE
        )
        if not status_match:
            continue
        raw_status = status_match.group(1).strip().upper().replace(" ", "_")

        try:
            results[artifact_id] = ArtifactStatus(raw_status)
        except ValueError:
            results[artifact_id] = ArtifactStatus.DRAFT

    return results


def is_artifact_frozen(initiative_path: Path, artifact_id: str) -> bool:
    """Check whether a specific artifact is frozen."""
    artifacts = read_frozen_artifacts(initiative_path)
    return artifacts.get(artifact_id) == ArtifactStatus.FROZEN


def write_artifact_status(
    initiative_path: Path, artifact_id: str, status: ArtifactStatus
) -> Path:
    """Write the Document Control ``| Status |`` cell for one artifact.

    The writer counterpart to :func:`read_frozen_artifacts` (FR-018): locates
    the ``docs/sdlc`` file whose ``| Artifact ID |`` matches ``artifact_id`` and
    replaces its ``| Status |`` cell with ``status.value`` -- the canonical
    uppercase token (e.g. ``FROZEN``, ``FREEZE_PENDING``, ``HALTED``) that
    :func:`read_frozen_artifacts` parses back, so a write round-trips.

    Returns the Path written. Raises ``ValueError`` if no artifact file matches
    the ID, or if the matched file has no Status cell -- failing loudly so a
    caller (e.g. ``apply_freeze_decision``) never believes a write succeeded
    when it did not. This function is the single low-level status writer; freeze
    authority and journalling live above it (ADR-0002).
    """
    sdlc_dir = initiative_path / "docs" / "sdlc"
    if not sdlc_dir.exists():
        raise ValueError(f"No docs/sdlc directory under {initiative_path}")

    target: Optional[Path] = None
    for md_file in sorted(sdlc_dir.glob("*.md")):
        text = md_file.read_text()
        id_match = re.search(
            r"\|\s*Artifact\s+ID\s*\|\s*(.*?)\s*\|", text, re.IGNORECASE
        )
        if id_match and id_match.group(1).strip() == artifact_id:
            target = md_file
            break

    if target is None:
        raise ValueError(
            f"No artifact with ID {artifact_id!r} found under {sdlc_dir}"
        )

    text = target.read_text()
    status_pattern = r"(\|\s*Status\s*\|)\s*.*?\s*\|"
    if not re.search(status_pattern, text, re.IGNORECASE):
        raise ValueError(
            f"Artifact {artifact_id!r} ({target.name}) has no Status cell"
        )
    new_text = re.sub(
        status_pattern,
        rf"\1 {status.value} |",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    target.write_text(new_text)
    return target


def append_journal_entry(
    journal_path: Path, entry_type: str, fields: dict
) -> None:
    """Append a formatted journal entry to the Sherpa Journal file.

    Each entry is a Markdown section:
    ```
    ### <entry_type> -- <timestamp>
    | Field | Value |
    |-------|-------|
    | key   | val   |
    ```
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"\n### {entry_type} -- {timestamp}\n",
        "| Field | Value |",
        "|-------|-------|",
    ]
    for key, value in fields.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")

    with open(journal_path, "a") as f:
        f.write("\n".join(lines))


def read_journal_entries(journal_path: Path) -> list[dict]:
    """Parse journal entries from a Sherpa Journal markdown file.

    Returns a list of dicts, each with ``entry_type``, ``timestamp``,
    and all field/value pairs from the table.
    """
    text = journal_path.read_text()
    entries: list[dict] = []

    # Split on ### headers
    header_pattern = re.compile(
        r"###\s+(.+?)\s+--\s+(\S+)"
    )
    # Find all entry headers
    headers = list(header_pattern.finditer(text))

    for i, hdr in enumerate(headers):
        entry: dict = {
            "entry_type": hdr.group(1).strip(),
            "timestamp": hdr.group(2).strip(),
        }

        # Slice text from this header to the next (or end)
        start = hdr.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section = text[start:end]

        # Parse table rows (skip header + separator)
        row_pattern = re.compile(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|")
        rows = row_pattern.findall(section)
        for key, value in rows:
            key = key.strip()
            value = value.strip()
            if key in ("Field", "-------", "---"):
                continue
            if key.startswith("---"):
                continue
            entry[key] = value

        entries.append(entry)

    return entries


def find_artifact_path(initiative_path: Path, artifact_id: str) -> Path:
    """Return the ``docs/sdlc`` file whose ``| Artifact ID |`` matches.

    The read-side counterpart to the resolver inside :func:`write_artifact_status`,
    exposed so callers that need the artifact's *content* before writing (e.g.
    ``apply_freeze_decision`` verifying a content hash) can locate the file
    without a second write. Raises ``ValueError`` if there is no ``docs/sdlc``
    directory or no artifact with that ID.
    """
    sdlc_dir = initiative_path / "docs" / "sdlc"
    if not sdlc_dir.exists():
        raise ValueError(f"No docs/sdlc directory under {initiative_path}")

    for md_file in sorted(sdlc_dir.glob("*.md")):
        text = md_file.read_text()
        id_match = re.search(
            r"\|\s*Artifact\s+ID\s*\|\s*(.*?)\s*\|", text, re.IGNORECASE
        )
        if id_match and id_match.group(1).strip() == artifact_id:
            return md_file

    raise ValueError(
        f"No artifact with ID {artifact_id!r} found under {sdlc_dir}"
    )
