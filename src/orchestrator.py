"""WDD execution orchestrator for the AIEOS Agent Harness.

Composes the parser, context builder, convergence loop, adapters, and
execution ledger into a single engine that drives a WDD through
group-by-group, phase-by-phase execution.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from src.adapters.base import AgentAdapter
from src.context_builder import PHASES, ItemContext, build_context
from src.convergence import ConvergenceLoop
from src.execution_ledger import ExecutionLedger, GroupGateResult
from src.models import AgentRequest, AgentResponse, LifecycleEvent
from src.wdd_parser import ExecutionGroup, ExecutionItem, ExecutionPlan

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Final result of a full WDD execution run."""

    status: str  # "complete", "partial", "failed"
    groups_completed: int
    groups_total: int
    items_completed: int
    items_total: int
    items_failed: int
    items_human_pending: int
    ledger_path: str


class WDDOrchestrator:
    """Drive a parsed WDD through group-by-group execution.

    Supports two execution modes per group:
    - phase_sync (Mode A): All items complete phase N before phase N+1.
    - independent (Mode B): Each item runs all 4 phases independently.

    File overlap between items in a group forces phase_sync mode to
    prevent write conflicts during parallel execution.
    """

    def __init__(
        self,
        execution_plan: ExecutionPlan,
        initiative_path: str,
        adapters: dict[str, AgentAdapter],
        wdd_content: str,
        tdd_content: str,
        acf_content: str,
        dcf_content: str,
        phase_prompts: dict[str, str],  # phase -> prompt content
        generate_adapter_name: str = "default",
        validate_adapter_name: str = "default",
        review_adapter_name: str | None = None,
        max_workers: int = 4,
        ledger_path: str | None = None,
    ):
        self._plan = execution_plan
        self._initiative = Path(initiative_path)
        self._adapters = adapters
        self._wdd_content = wdd_content
        self._tdd_content = tdd_content
        self._acf_content = acf_content
        self._dcf_content = dcf_content
        self._phase_prompts = phase_prompts
        self._gen_name = generate_adapter_name
        self._val_name = validate_adapter_name
        self._review_name = review_adapter_name or generate_adapter_name
        self._max_workers = max_workers

        lp = ledger_path or str(
            self._initiative / "docs" / "sdlc" / "execution-ledger.md"
        )
        self._ledger = ExecutionLedger(lp)

    @property
    def ledger(self) -> ExecutionLedger:
        """Expose the ledger for external inspection (e.g., tests)."""
        return self._ledger

    def execute(self) -> ExecutionResult:
        """Execute the full WDD plan group by group.

        Groups run sequentially. Within each group, items run according
        to the group's mode (phase_sync or independent). A failed group
        gate stops execution of subsequent groups.
        """
        groups_completed = 0
        items_completed = 0
        items_failed = 0
        items_human = 0

        for group in self._plan.groups:
            # Check file overlap before parallel execution
            overlaps = self._check_file_overlap(group.items)
            if overlaps:
                logger.warning(
                    "File overlap in %s: %s. Falling back to sequential.",
                    group.name,
                    overlaps,
                )
                group.mode = "phase_sync"  # Force sequential

            if group.mode == "independent":
                g_completed, g_failed, g_human = self._execute_group_independent(
                    group
                )
            else:
                g_completed, g_failed, g_human = self._execute_group_phase_sync(
                    group
                )

            items_completed += g_completed
            items_failed += g_failed
            items_human += g_human

            # Work group gate
            ai_items = [i for i in group.items if i.assignee_type != "Human"]
            gate = GroupGateResult(
                group_name=group.name,
                status="passed" if g_failed == 0 else "failed",
                items_completed=g_completed,
                items_total=len(ai_items),
            )
            self._ledger.record_group_gate(gate)

            if gate.status == "passed":
                groups_completed += 1
            else:
                break  # Stop on failed gate

        status = (
            "complete"
            if groups_completed == len(self._plan.groups)
            else ("partial" if groups_completed > 0 else "failed")
        )

        return ExecutionResult(
            status=status,
            groups_completed=groups_completed,
            groups_total=len(self._plan.groups),
            items_completed=items_completed,
            items_total=sum(len(g.items) for g in self._plan.groups),
            items_failed=items_failed,
            items_human_pending=items_human,
            ledger_path=str(self._ledger._path),
        )

    def _execute_group_phase_sync(
        self, group: ExecutionGroup
    ) -> tuple[int, int, int]:
        """Mode A: all items complete phase N before phase N+1."""
        completed = 0
        failed = 0
        human = 0

        for phase in PHASES:
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                futures: dict = {}
                for item in group.items:
                    if item.assignee_type == "Human":
                        if phase == "tests":  # Count once
                            human += 1
                        self._ledger.record_phase_start(item.id, phase, "human")
                        self._ledger.record_phase_complete(
                            item.id, phase, "pending-human", "PENDING", 0
                        )
                        continue

                    # Skip if already completed (resumption)
                    if self._ledger.is_item_phase_complete(item.id, phase):
                        if phase == "review":
                            completed += 1
                        continue

                    context = build_context(
                        item=item,
                        wdd_content=self._wdd_content,
                        tdd_content=self._tdd_content,
                        acf_content=self._acf_content,
                        dcf_content=self._dcf_content,
                        phase=phase,
                        prompt_content=self._phase_prompts.get(phase, ""),
                        prior_outputs=self._get_prior_outputs(item.id),
                    )

                    self._ledger.record_phase_start(item.id, phase, self._gen_name)
                    future = executor.submit(
                        self._execute_item_phase, item, phase, context
                    )
                    futures[future] = item.id

                for future in as_completed(futures):
                    item_id = futures[future]
                    try:
                        result = future.result()
                        self._ledger.record_phase_complete(
                            item_id,
                            phase,
                            result["output_path"],
                            result["validation_status"],
                            result["convergence_iterations"],
                        )
                        if phase == "review":
                            completed += 1
                    except Exception as e:
                        self._ledger.record_phase_failed(item_id, phase, str(e))
                        if phase == "review":
                            failed += 1

        return completed, failed, human

    def _execute_group_independent(
        self, group: ExecutionGroup
    ) -> tuple[int, int, int]:
        """Mode B: each item runs all 4 phases independently."""
        completed = 0
        failed = 0
        human = 0

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures: dict = {}
            for item in group.items:
                if item.assignee_type == "Human":
                    human += 1
                    # Record all phases as pending-human
                    for phase in PHASES:
                        self._ledger.record_phase_start(item.id, phase, "human")
                        self._ledger.record_phase_complete(
                            item.id, phase, "pending-human", "PENDING", 0
                        )
                    continue
                future = executor.submit(self._execute_item_all_phases, item)
                futures[future] = item.id

            for future in as_completed(futures):
                item_id = futures[future]
                try:
                    future.result()
                    completed += 1
                except Exception:
                    failed += 1

        return completed, failed, human

    def _execute_item_phase(
        self,
        item: ExecutionItem,
        phase: str,
        context: ItemContext,
    ) -> dict:
        """Execute one phase via convergence loop.

        Returns dict with output_path, validation_status, convergence_iterations.
        """
        # Review phase uses a different adapter for independent evaluation
        if phase == "review":
            gen_adapter = self._adapters[self._review_name]
        else:
            gen_adapter = self._adapters[self._gen_name]
        val_adapter = self._adapters[self._val_name]

        request = AgentRequest(
            artifact_type=f"{item.id}-{phase}",
            event=LifecycleEvent.POST_GENERATION,
            spec_content=context.dcf_sections,
            template_content="",
            prompt_content=context.phase_prompt,
            upstream_artifacts={
                "wdd_item": context.wdd_item_text,
                "tdd": context.tdd_sections,
                "acf": context.acf_sections,
            },
            current_artifact=None,
            correction_constraints=[],
            metadata={
                "item_id": item.id,
                "phase": phase,
                "work_group": item.work_group,
                "human_author": "orchestrator",
            },
        )

        val_request = AgentRequest(
            artifact_type=f"{item.id}-{phase}",
            event=LifecycleEvent.POST_VALIDATION,
            spec_content=context.dcf_sections,
            template_content="",
            prompt_content="Validate this output against the spec constraints.",
            upstream_artifacts={},
            current_artifact=None,
            correction_constraints=[],
            metadata={"item_id": item.id, "phase": phase},
        )

        loop = ConvergenceLoop(gen_adapter, val_adapter)
        response, validation, state = loop.run(request, val_request)

        output_path = str(
            self._initiative / "docs" / "sdlc" / f"{item.id}-{phase}.md"
        )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(response.content)

        return {
            "output_path": output_path,
            "validation_status": validation.status,
            "convergence_iterations": state.current_iteration,
        }

    def _execute_item_all_phases(self, item: ExecutionItem) -> None:
        """Mode B: run all phases sequentially for one item."""
        for phase in PHASES:
            context = build_context(
                item=item,
                wdd_content=self._wdd_content,
                tdd_content=self._tdd_content,
                acf_content=self._acf_content,
                dcf_content=self._dcf_content,
                phase=phase,
                prompt_content=self._phase_prompts.get(phase, ""),
                prior_outputs=self._get_prior_outputs(item.id),
            )
            self._ledger.record_phase_start(item.id, phase, self._gen_name)
            result = self._execute_item_phase(item, phase, context)
            self._ledger.record_phase_complete(
                item.id,
                phase,
                result["output_path"],
                result["validation_status"],
                result["convergence_iterations"],
            )

    def _get_prior_outputs(self, item_id: str) -> dict[str, str]:
        """Get prior phase outputs for this item from disk."""
        outputs = {}
        statuses = self._ledger.get_item_status(item_id)
        for phase in PHASES:
            if phase in statuses and statuses[phase].output_path:
                path = Path(statuses[phase].output_path)
                if path.exists():
                    outputs[phase] = path.read_text()
        return outputs

    @staticmethod
    def _check_file_overlap(
        items: list[ExecutionItem],
    ) -> list[tuple[str, str, set]]:
        """Detect file overlap between items in a group.

        Returns list of (item_a_id, item_b_id, shared_files) tuples.
        """
        overlaps = []
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                shared = set(a.files_touched) & set(b.files_touched)
                if shared:
                    overlaps.append((a.id, b.id, shared))
        return overlaps
