"""Build isolated context packages for each work item in each execution phase.

Extracts relevant sections from WDD, TDD, ACF, and DCF content to produce
a focused context for a single work item in a single phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.wdd_parser import ExecutionItem


PHASES = ["tests", "plan", "code", "review"]


@dataclass
class ItemContext:
    """Assembled context for a single work item in a single phase."""

    item: ExecutionItem
    wdd_item_text: str  # Verbatim WDD item section
    tdd_sections: str  # Relevant TDD sections only
    acf_sections: str  # Relevant ACF sections
    dcf_sections: str  # Relevant DCF sections
    phase_prompt: str  # Phase-specific prompt content
    prior_phase_outputs: dict[str, str] = field(
        default_factory=dict
    )  # phase_name -> output content


def _extract_wdd_item_section(item_id: str, wdd_content: str) -> str:
    """Extract the verbatim WDD section for a specific item ID.

    Searches for the item ID and returns the full section from
    the '### WDD Item' or '#### WDD-' header through to the next
    section boundary.
    """
    # Strategy 1: Find '### WDD Item' section containing this ID
    sections = re.split(r"(?=^### WDD Item\b)", wdd_content, flags=re.MULTILINE)
    for section in sections:
        if item_id in section:
            return section.strip()

    # Strategy 2: Find '#### WDD-XXX-NNN' header containing this ID
    sections = re.split(r"(?=^#### WDD-)", wdd_content, flags=re.MULTILINE)
    for section in sections:
        if item_id in section:
            return section.strip()

    # Strategy 3: Find any paragraph block containing the ID
    lines = wdd_content.split("\n")
    start = None
    for i, line in enumerate(lines):
        if item_id in line:
            # Walk backward to find section start
            start = i
            for j in range(i - 1, -1, -1):
                if lines[j].startswith("#") or lines[j] == "---":
                    start = j
                    break
            break

    if start is not None:
        # Walk forward to find section end
        end = len(lines)
        for k in range(start + 1, len(lines)):
            if lines[k].startswith("### ") or lines[k] == "---":
                end = k
                break
        return "\n".join(lines[start:end]).strip()

    return ""


def _extract_tdd_sections(
    item: ExecutionItem, tdd_content: str
) -> str:
    """Extract TDD sections relevant to a specific work item.

    Searches for:
    - References to the item ID (e.g., WDD-HARNESS-001)
    - Component names mentioned in the item title or description
    - Parent TDD section references from the item
    """
    if not tdd_content:
        return ""

    relevant_sections: list[str] = []
    seen: set[str] = set()

    # Split TDD into sections by ## or ### headers
    sections = re.split(r"(?=^##\s+)", tdd_content, flags=re.MULTILINE)

    # Build search terms from the item
    search_terms: list[str] = [item.id]

    # Add words from title that might be component names (3+ chars, capitalized)
    if item.title:
        for word in item.title.split():
            cleaned = word.strip(".,;:()")
            if len(cleaned) >= 3:
                search_terms.append(cleaned)

    # Add file names from files_touched (without path)
    for fpath in item.files_touched:
        fname = fpath.rsplit("/", 1)[-1].replace(".py", "").replace(".md", "")
        if fname:
            search_terms.append(fname)

    for section in sections:
        if not section.strip():
            continue
        section_key = section[:80]
        if section_key in seen:
            continue

        # Check if any search term appears in this section
        section_lower = section.lower()
        for term in search_terms:
            if term.lower() in section_lower:
                seen.add(section_key)
                relevant_sections.append(section.strip())
                break

    if relevant_sections:
        return "\n\n".join(relevant_sections)

    # Fallback: return the full TDD if no sections matched
    return tdd_content


def build_context(
    item: ExecutionItem,
    wdd_content: str,
    tdd_content: str,
    acf_content: str,
    dcf_content: str,
    phase: str,
    prompt_content: str,
    prior_outputs: dict[str, str] | None = None,
) -> ItemContext:
    """Build an isolated context package for a work item in a specific phase.

    Args:
        item: The ExecutionItem to build context for.
        wdd_content: Full WDD Markdown content.
        tdd_content: Full TDD Markdown content.
        acf_content: Full ACF (Architecture Conformance Framework) content.
        dcf_content: Full DCF (Design Conformance Framework) content.
        phase: The execution phase ("tests", "plan", "code", "review").
        prompt_content: Phase-specific prompt content (passed in, not read from disk).
        prior_outputs: Optional dict of phase_name -> output content from prior phases.

    Returns:
        ItemContext with extracted and assembled context.
    """
    # Extract the specific work item section from WDD
    wdd_item_text = _extract_wdd_item_section(item.id, wdd_content)

    # Extract relevant TDD sections
    tdd_sections = _extract_tdd_sections(item, tdd_content)

    # Include full ACF and DCF (they're short enough to include entirely)
    acf_sections = acf_content
    dcf_sections = dcf_content

    # Prior phase outputs
    prior = prior_outputs if prior_outputs is not None else {}

    return ItemContext(
        item=item,
        wdd_item_text=wdd_item_text,
        tdd_sections=tdd_sections,
        acf_sections=acf_sections,
        dcf_sections=dcf_sections,
        phase_prompt=prompt_content,
        prior_phase_outputs=prior,
    )
