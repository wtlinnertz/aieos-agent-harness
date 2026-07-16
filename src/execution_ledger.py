"""Persistent execution state tracker, saved as Markdown on disk.

Tracks per-item phase status and group gate results across WDD execution.
The Markdown format is both human-readable and machine-parseable, supporting
session resumption by loading prior state from disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ItemPhaseStatus:
    """Status of a single phase for a single WDD item."""

    item_id: str
    phase: str                   # "tests", "plan", "code", "review"
    status: str                  # "pending", "running", "passed", "failed", "escalated"
    agent_id: str | None = None
    started: str | None = None
    completed: str | None = None
    output_path: str | None = None
    validation_status: str | None = None
    convergence_iterations: int = 0


@dataclass
class GroupGateResult:
    """Result of a work group gate check."""

    group_name: str
    status: str                  # "passed", "failed"
    tests_before: int = 0
    tests_after: int = 0
    tests_delta: int = 0
    lint_clean: bool = True
    items_completed: int = 0
    items_total: int = 0
    gate_file_path: str = ""


class ExecutionLedger:
    """Track execution state on disk as Markdown.

    Persists to a Markdown file with tables for per-item phase status
    and group gate results. Supports save/load for session resumption.
    """

    def __init__(self, ledger_path: str | Path):
        self._path = Path(ledger_path)
        self._phases: dict[str, dict[str, ItemPhaseStatus]] = {}  # item_id -> {phase -> status}
        self._gates: dict[str, GroupGateResult] = {}  # group_name -> result
        if self._path.exists():
            self.load()

    def record_phase_start(self, item_id: str, phase: str, agent_id: str) -> None:
        """Record that a phase has started for an item."""
        if item_id not in self._phases:
            self._phases[item_id] = {}
        self._phases[item_id][phase] = ItemPhaseStatus(
            item_id=item_id,
            phase=phase,
            status="running",
            agent_id=agent_id,
            started=datetime.now(timezone.utc).isoformat(),
        )
        self.save()

    def record_phase_complete(
        self,
        item_id: str,
        phase: str,
        output_path: str,
        validation: str,
        iterations: int,
    ) -> None:
        """Record successful phase completion."""
        status = self._phases.get(item_id, {}).get(phase)
        if status:
            status.status = "passed"
            status.completed = datetime.now(timezone.utc).isoformat()
            status.output_path = output_path
            status.validation_status = validation
            status.convergence_iterations = iterations
        else:
            self._phases.setdefault(item_id, {})[phase] = ItemPhaseStatus(
                item_id=item_id,
                phase=phase,
                status="passed",
                completed=datetime.now(timezone.utc).isoformat(),
                output_path=output_path,
                validation_status=validation,
                convergence_iterations=iterations,
            )
        self.save()

    def record_phase_failed(self, item_id: str, phase: str, error: str) -> None:
        """Record phase failure."""
        status = self._phases.get(item_id, {}).get(phase)
        if status:
            status.status = "failed"
            status.completed = datetime.now(timezone.utc).isoformat()
            status.validation_status = f"FAIL: {error}"
        else:
            self._phases.setdefault(item_id, {})[phase] = ItemPhaseStatus(
                item_id=item_id,
                phase=phase,
                status="failed",
                completed=datetime.now(timezone.utc).isoformat(),
                validation_status=f"FAIL: {error}",
            )
        self.save()

    def record_group_gate(self, result: GroupGateResult) -> None:
        """Record a work group gate result."""
        self._gates[result.group_name] = result
        self.save()

    def get_item_status(self, item_id: str) -> dict[str, ItemPhaseStatus]:
        """Get all phase statuses for an item."""
        return self._phases.get(item_id, {})

    def get_group_status(self, group_name: str) -> GroupGateResult | None:
        """Get gate result for a group."""
        return self._gates.get(group_name)

    def is_item_phase_complete(self, item_id: str, phase: str) -> bool:
        """Check if a specific phase is complete (passed) for an item."""
        status = self._phases.get(item_id, {}).get(phase)
        return status is not None and status.status == "passed"

    def is_group_complete(self, group_name: str, item_ids: list[str]) -> bool:
        """Check if all items in a group have completed all 4 phases."""
        for item_id in item_ids:
            for phase in ["tests", "plan", "code", "review"]:
                if not self.is_item_phase_complete(item_id, phase):
                    return False
        return True

    def all_groups_complete(self, group_names: list[str]) -> bool:
        """Check if all named groups have passed their gate."""
        return all(
            g in self._gates and self._gates[g].status == "passed"
            for g in group_names
        )

    def save(self) -> None:
        """Write ledger to disk as Markdown.

        Format:
        - H1 header with title and timestamp
        - Item Phase Status table with 7 columns
        - Group Gates table with 7 columns
        """
        lines = [
            "# Execution Ledger",
            "",
            f"Last updated: {datetime.now(timezone.utc).isoformat()}",
            "",
        ]

        # Phase status table
        lines.append("## Item Phase Status")
        lines.append("")
        lines.append(
            "| Item ID | Phase | Status | Agent | Validation | Iterations | Output |"
        )
        lines.append(
            "|---------|-------|--------|-------|------------|------------|--------|"
        )
        for item_id in sorted(self._phases.keys()):
            for phase in ["tests", "plan", "code", "review"]:
                if phase in self._phases[item_id]:
                    s = self._phases[item_id][phase]
                    # Escape pipe characters in validation status
                    val = (s.validation_status or "-").replace("|", "\\|")
                    lines.append(
                        f"| {s.item_id} | {s.phase} | {s.status} | "
                        f"{s.agent_id or '-'} | {val} | "
                        f"{s.convergence_iterations} | {s.output_path or '-'} |"
                    )
        lines.append("")

        # Group gates table
        lines.append("## Group Gates")
        lines.append("")
        lines.append(
            "| Group | Status | Tests Before | Tests After | Delta | Lint | Items |"
        )
        lines.append(
            "|-------|--------|-------------|-------------|-------|------|-------|"
        )
        for name in sorted(self._gates.keys()):
            g = self._gates[name]
            lines.append(
                f"| {g.group_name} | {g.status} | {g.tests_before} | "
                f"{g.tests_after} | {g.tests_delta} | "
                f"{'clean' if g.lint_clean else 'DIRTY'} | "
                f"{g.items_completed}/{g.items_total} |"
            )

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def load(self) -> None:
        """Load ledger from disk Markdown.

        Parses the two tables (Item Phase Status and Group Gates) using
        regex-based row extraction. Tolerant of extra whitespace.
        """
        content = self._path.read_text(encoding="utf-8")

        self._phases = {}
        self._gates = {}

        # Parse phase status table rows
        phase_pattern = re.compile(
            r"\|\s*(\S+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*([^|]*)\|\s*([^|]*)\|\s*(\d+)\s*\|\s*([^|]*)\|"
        )

        # Parse group gate table rows
        gate_pattern = re.compile(
            r"\|\s*([^|]+)\|\s*(\w+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(-?\d+)\s*\|\s*(\w+)\s*\|\s*(\d+)/(\d+)\s*\|"
        )

        in_phase_table = False
        in_gate_table = False

        for line in content.split("\n"):
            stripped = line.strip()

            # Detect section transitions
            if "## Item Phase Status" in line:
                in_phase_table = True
                in_gate_table = False
                continue
            if "## Group Gates" in line:
                in_phase_table = False
                in_gate_table = True
                continue
            # Skip header separator rows
            if stripped.startswith("|---") or stripped.startswith("| Item ID") or stripped.startswith("| Group"):
                continue

            if in_phase_table and stripped.startswith("|"):
                m = phase_pattern.match(stripped)
                if m:
                    item_id = m.group(1).strip()
                    phase = m.group(2).strip()
                    status = m.group(3).strip()
                    agent = m.group(4).strip()
                    validation = m.group(5).strip().replace("\\|", "|")
                    iterations = int(m.group(6))
                    output = m.group(7).strip()

                    self._phases.setdefault(item_id, {})[phase] = ItemPhaseStatus(
                        item_id=item_id,
                        phase=phase,
                        status=status,
                        agent_id=agent if agent != "-" else None,
                        validation_status=validation if validation != "-" else None,
                        convergence_iterations=iterations,
                        output_path=output if output != "-" else None,
                    )

            if in_gate_table and stripped.startswith("|"):
                m = gate_pattern.match(stripped)
                if m:
                    group_name = m.group(1).strip()
                    status = m.group(2).strip()
                    tests_before = int(m.group(3))
                    tests_after = int(m.group(4))
                    tests_delta = int(m.group(5))
                    lint_str = m.group(6).strip()
                    items_completed = int(m.group(7))
                    items_total = int(m.group(8))

                    self._gates[group_name] = GroupGateResult(
                        group_name=group_name,
                        status=status,
                        tests_before=tests_before,
                        tests_after=tests_after,
                        tests_delta=tests_delta,
                        lint_clean=(lint_str == "clean"),
                        items_completed=items_completed,
                        items_total=items_total,
                    )
