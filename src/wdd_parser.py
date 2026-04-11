"""Parse a frozen WDD Markdown file + execution plan into structured execution data.

Extracts work items, work groups, and metadata from WDD format into dataclasses
suitable for orchestrated execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExecutionItem:
    """A single WDD work item parsed for execution."""

    id: str  # WDD-XXX-NNN
    title: str
    description: str
    assignee_type: str  # "AI", "Human", "Either"
    acceptance_criteria: list[str]
    complexity: str  # "S", "M", "L", "XL"
    dependencies: list[str]  # Other item IDs
    work_group: str  # Group name
    parallel_safe: bool = True  # Default true, override from execution plan
    files_touched: list[str] = field(default_factory=list)


@dataclass
class ExecutionGroup:
    """A work group containing related execution items."""

    name: str
    items: list[ExecutionItem] = field(default_factory=list)
    mode: str = "phase_sync"  # "phase_sync" (Mode A) or "independent" (Mode B)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    """Top-level parsed execution plan from a WDD."""

    wdd_id: str
    initiative: str
    groups: list[ExecutionGroup] = field(default_factory=list)
    baseline_tests: int = 0
    baseline_build_clean: bool = True


# --- Internal regex patterns ---

_WDD_ID_PATTERN = re.compile(r"WDD\s+ID:\s*(WDD-[\w-]+)", re.IGNORECASE)
_INITIATIVE_PATTERN = re.compile(
    r"^[-*]?\s*Initiative\s*:\s*(.+)", re.IGNORECASE | re.MULTILINE
)
_ITEM_ID_PATTERN = re.compile(
    r"WDD\s+Item\s+ID:\s*(WDD-[\w-]+)", re.IGNORECASE
)
_ASSIGNEE_PATTERN = re.compile(
    r"Assignee\s+Type:\s*(AI\s*Agent|Human|Either)", re.IGNORECASE
)
_COMPLEXITY_PATTERN = re.compile(
    r"Complexity\s+Estimate:\s*([SMLX]{1,2})\b", re.IGNORECASE
)
_AC_PATTERN = re.compile(r"^-\s*(AC\d+:.+)", re.MULTILINE)
_DEPENDENCY_PATTERN = re.compile(r"(WDD-[\w-]+\d{3})")

_GROUP_ID_PATTERN = re.compile(r"Group\s+ID:\s*(WG-\d+)", re.IGNORECASE)
_GROUP_NAME_PATTERN = re.compile(r"Group\s+Name:\s*(.+)", re.IGNORECASE)
_MEMBER_ITEMS_PATTERN = re.compile(
    r"Member\s+Items:\s*(.+)", re.IGNORECASE
)


def _normalize_assignee(raw: str) -> str:
    """Normalize assignee type to canonical form."""
    cleaned = raw.strip().lower()
    if "ai" in cleaned:
        return "AI"
    if "human" in cleaned:
        return "Human"
    if "either" in cleaned:
        return "Either"
    return raw.strip()


def _normalize_complexity(raw: str) -> str:
    """Normalize complexity to uppercase single/double letter."""
    return raw.strip().upper()


def _extract_wdd_metadata(content: str) -> tuple[str, str]:
    """Extract WDD ID and initiative name from document control section."""
    wdd_id = ""
    initiative = ""

    m = _WDD_ID_PATTERN.search(content)
    if m:
        wdd_id = m.group(1).strip()

    # Try to derive initiative from WDD ID (e.g., WDD-HARNESS-001 -> HARNESS)
    if wdd_id:
        parts = wdd_id.split("-")
        if len(parts) >= 3:
            initiative = parts[1]

    # Also check for explicit Initiative field
    m = _INITIATIVE_PATTERN.search(content)
    if m:
        initiative = m.group(1).strip()

    return wdd_id, initiative


def _split_wdd_items(content: str) -> list[str]:
    """Split WDD content into individual work item sections.

    Looks for '### WDD Item' headers (the real WDD format) or
    '#### WDD-' headers as item boundaries.
    """
    # Split on '### WDD Item' headers (real format)
    sections = re.split(r"(?=^### WDD Item\b)", content, flags=re.MULTILINE)
    items = [s for s in sections if _ITEM_ID_PATTERN.search(s)]

    if items:
        return items

    # Fallback: split on '#### WDD-' headers
    sections = re.split(r"(?=^#### WDD-)", content, flags=re.MULTILINE)
    items = [s for s in sections if _ITEM_ID_PATTERN.search(s)]

    return items


def _parse_item_section(section: str) -> ExecutionItem | None:
    """Parse a single WDD item section into an ExecutionItem."""
    id_match = _ITEM_ID_PATTERN.search(section)
    if not id_match:
        return None

    item_id = id_match.group(1).strip()

    # Extract title from Intent section
    title = ""
    intent_match = re.search(
        r"####\s+Intent.*?\n(.+?)(?:\n\n|\n####|\Z)",
        section,
        re.DOTALL,
    )
    if intent_match:
        title = intent_match.group(1).strip().split("\n")[0].strip()

    # If no intent section, try to get title from the item ID line context
    if not title:
        # Look for pattern like "#### WDD-XXX-NNN: Title"
        title_match = re.search(
            r"####\s+WDD-[\w-]+\d{3}:\s*(.+)", section
        )
        if title_match:
            title = title_match.group(1).strip()

    # Description: use the full Intent section or In Scope
    description = ""
    if intent_match:
        description = intent_match.group(1).strip()
    else:
        scope_match = re.search(
            r"####\s+In\s+Scope\s*\n(.*?)(?=\n####|\Z)",
            section,
            re.DOTALL,
        )
        if scope_match:
            description = scope_match.group(1).strip()

    # Assignee type
    assignee_type = "AI"
    assignee_match = _ASSIGNEE_PATTERN.search(section)
    if assignee_match:
        assignee_type = _normalize_assignee(assignee_match.group(1))

    # Complexity
    complexity = "M"
    complexity_match = _COMPLEXITY_PATTERN.search(section)
    if complexity_match:
        complexity = _normalize_complexity(complexity_match.group(1))

    # Acceptance criteria
    ac_section_match = re.search(
        r"####\s+Acceptance\s+Criteria.*?\n(.*?)(?=\n####|\Z)",
        section,
        re.DOTALL,
    )
    acceptance_criteria = []
    if ac_section_match:
        ac_text = ac_section_match.group(1)
        acceptance_criteria = _AC_PATTERN.findall(ac_text)
        # If no AC-prefixed items, try plain list items
        if not acceptance_criteria:
            acceptance_criteria = [
                line.strip().lstrip("- ")
                for line in ac_text.strip().split("\n")
                if line.strip().startswith("- ")
            ]

    # Dependencies
    dep_section_match = re.search(
        r"####\s+Dependencies\s*\n(.*?)(?=\n####|\n---|\Z)",
        section,
        re.DOTALL,
    )
    dependencies: list[str] = []
    if dep_section_match:
        dep_text = dep_section_match.group(1).strip()
        if dep_text.lower() not in ("none", "n/a", ""):
            dependencies = _DEPENDENCY_PATTERN.findall(dep_text)
            # Deduplicate while preserving order
            seen: set[str] = set()
            unique_deps: list[str] = []
            for d in dependencies:
                if d not in seen and d != item_id:
                    seen.add(d)
                    unique_deps.append(d)
            dependencies = unique_deps

    return ExecutionItem(
        id=item_id,
        title=title,
        description=description,
        assignee_type=assignee_type,
        acceptance_criteria=acceptance_criteria,
        complexity=complexity,
        dependencies=dependencies,
        work_group="",  # Filled in during group assignment
    )


def _parse_work_groups(
    content: str, items_by_id: dict[str, ExecutionItem]
) -> list[ExecutionGroup]:
    """Parse Work Groups section and assign items to groups."""
    groups: list[ExecutionGroup] = []

    # Find the Work Groups section
    groups_section_match = re.search(
        r"##\s+\d+\.\s+Work\s+Groups\s*\n(.*?)(?=\n##\s+\d+\.|\Z)",
        content,
        re.DOTALL,
    )
    if not groups_section_match:
        # No work groups section — put all items in a single default group
        if items_by_id:
            default_group = ExecutionGroup(
                name="Default",
                items=list(items_by_id.values()),
            )
            for item in default_group.items:
                item.work_group = "Default"
            groups.append(default_group)
        return groups

    groups_text = groups_section_match.group(1)
    group_sections = re.split(
        r"(?=^### Work Group\b)", groups_text, flags=re.MULTILINE
    )

    for gsection in group_sections:
        if not gsection.strip():
            continue

        name_match = _GROUP_NAME_PATTERN.search(gsection)
        if not name_match:
            continue

        group_name = name_match.group(1).strip()
        member_match = _MEMBER_ITEMS_PATTERN.search(gsection)

        group_items: list[ExecutionItem] = []
        if member_match:
            member_ids = _DEPENDENCY_PATTERN.findall(member_match.group(1))
            for mid in member_ids:
                if mid in items_by_id:
                    item = items_by_id[mid]
                    item.work_group = group_name
                    group_items.append(item)

        group = ExecutionGroup(name=group_name, items=group_items)
        groups.append(group)

    # Assign any unassigned items to a "Default" group
    assigned_ids = {
        item.id for group in groups for item in group.items
    }
    unassigned = [
        item
        for item_id, item in items_by_id.items()
        if item_id not in assigned_ids
    ]
    if unassigned:
        default_group = ExecutionGroup(name="Unassigned", items=unassigned)
        for item in unassigned:
            item.work_group = "Unassigned"
        groups.append(default_group)

    return groups


def _overlay_execution_plan(
    plan: ExecutionPlan, exec_plan_content: str
) -> None:
    """Overlay execution plan data onto the parsed plan.

    Reads the execution plan Markdown for parallel_safe flags,
    files_touched, baseline info, and group modes.
    """
    items_by_id = {
        item.id: item
        for group in plan.groups
        for item in group.items
    }

    # Look for baseline test count
    baseline_match = re.search(
        r"[Bb]aseline\s+[Tt]ests?\s*:\s*(\d+)", exec_plan_content
    )
    if baseline_match:
        plan.baseline_tests = int(baseline_match.group(1))

    # Look for baseline build status
    build_match = re.search(
        r"[Bb]aseline\s+[Bb]uild\s*:\s*(clean|passing|true|yes)",
        exec_plan_content,
        re.IGNORECASE,
    )
    if build_match:
        plan.baseline_build_clean = True

    build_fail_match = re.search(
        r"[Bb]aseline\s+[Bb]uild\s*:\s*(failing|false|no|broken)",
        exec_plan_content,
        re.IGNORECASE,
    )
    if build_fail_match:
        plan.baseline_build_clean = False

    # Look for parallel_safe overrides (e.g., "WDD-XXX-NNN: parallel_safe: false")
    parallel_overrides = re.findall(
        r"(WDD-[\w-]+\d{3})\s*.*?parallel[_\s]safe\s*:\s*(true|false)",
        exec_plan_content,
        re.IGNORECASE,
    )
    for item_id, value in parallel_overrides:
        if item_id in items_by_id:
            items_by_id[item_id].parallel_safe = value.lower() == "true"

    # Look for files_touched (e.g., "WDD-XXX-NNN files: src/foo.py, src/bar.py")
    files_matches = re.findall(
        r"(WDD-[\w-]+\d{3})\s*.*?files?\s*:\s*(.+)",
        exec_plan_content,
        re.IGNORECASE,
    )
    for item_id, files_str in files_matches:
        if item_id in items_by_id:
            files = [
                f.strip()
                for f in files_str.split(",")
                if f.strip()
            ]
            items_by_id[item_id].files_touched = files

    # Look for group mode overrides
    for group in plan.groups:
        mode_match = re.search(
            re.escape(group.name)
            + r".*?[Mm]ode\s*:\s*(phase_sync|independent|Mode\s*[AB])",
            exec_plan_content,
            re.IGNORECASE,
        )
        if mode_match:
            raw_mode = mode_match.group(1).strip().lower()
            if raw_mode in ("independent", "mode b"):
                group.mode = "independent"
            else:
                group.mode = "phase_sync"


def parse_wdd_for_execution(
    wdd_path: str, execution_plan_path: str | None = None
) -> ExecutionPlan:
    """Parse a WDD Markdown file into an ExecutionPlan.

    Args:
        wdd_path: Path to the frozen WDD Markdown file.
        execution_plan_path: Optional path to execution plan Markdown
            for parallel_safe overrides and files_touched data.

    Returns:
        ExecutionPlan with parsed groups, items, and metadata.
    """
    wdd_content = Path(wdd_path).read_text(encoding="utf-8")

    # Extract metadata
    wdd_id, initiative = _extract_wdd_metadata(wdd_content)

    # Parse individual work items
    item_sections = _split_wdd_items(wdd_content)
    items_by_id: dict[str, ExecutionItem] = {}
    for section in item_sections:
        item = _parse_item_section(section)
        if item:
            items_by_id[item.id] = item

    # Parse work groups and assign items
    groups = _parse_work_groups(wdd_content, items_by_id)

    plan = ExecutionPlan(
        wdd_id=wdd_id,
        initiative=initiative,
        groups=groups,
    )

    # Overlay execution plan if provided
    if execution_plan_path:
        exec_content = Path(execution_plan_path).read_text(encoding="utf-8")
        _overlay_execution_plan(plan, exec_content)

    return plan
