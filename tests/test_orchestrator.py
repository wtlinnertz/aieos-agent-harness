"""Tests for the WDD execution orchestrator."""

import json
import threading

import pytest

from src.adapters.mock import MockAdapter
from src.context_builder import PHASES
from src.execution_ledger import ExecutionLedger
from src.orchestrator import ExecutionResult, WDDOrchestrator
from src.wdd_parser import ExecutionGroup, ExecutionItem, ExecutionPlan


# --- Validation JSON that parse_validation_result accepts ---

PASS_JSON = json.dumps(
    {
        "status": "PASS",
        "summary": "All gates passed.",
        "hard_gates": {"gate_1": "PASS"},
        "blocking_issues": [],
        "warnings": [],
        "completeness_score": 100,
    }
)

FAIL_JSON = json.dumps(
    {
        "status": "FAIL",
        "summary": "Gate failed.",
        "hard_gates": {"gate_1": "FAIL"},
        "blocking_issues": [
            {"gate": "gate_1", "description": "Missing section", "location": "§3"}
        ],
        "warnings": [],
        "completeness_score": 30,
    }
)


class SequentialMockAdapter:
    """Adapter that returns different responses on successive invoke() calls.

    Used to make the validator always return the same PASS JSON regardless of
    artifact_type, or to sequence FAIL then PASS for convergence testing.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._index = 0
        self._lock = threading.Lock()
        self.call_history: list[tuple[str, tuple]] = []
        self._provider_name = "seq-mock"
        self._model_name = "seq-v1"

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def invoke(self, request):
        from src.models import AgentResponse

        with self._lock:
            self.call_history.append(("invoke", (request,)))
            idx = min(self._index, len(self._responses) - 1)
            content = self._responses[idx]
            self._index += 1

        return AgentResponse(
            content=content,
            provider=self._provider_name,
            model=self._model_name,
            tokens_in=50,
            tokens_out=100,
            cost_usd=0.001,
            latency_ms=10.0,
        )

    def health(self):
        from src.models import HealthStatus

        return HealthStatus.OK

    def cost_estimate(self, request):
        return 0.001


# --- Fixtures ---


def _make_item(item_id, group="WG-1", assignee="AI", files=None):
    return ExecutionItem(
        id=item_id,
        title=f"Item {item_id}",
        description=f"Description for {item_id}",
        assignee_type=assignee,
        acceptance_criteria=["AC1: test"],
        complexity="S",
        dependencies=[],
        work_group=group,
        files_touched=files or [],
    )


def _make_plan(groups):
    return ExecutionPlan(wdd_id="WDD-TEST-001", initiative="TEST", groups=groups)


@pytest.fixture
def two_group_plan():
    """Plan with 2 groups: group 1 has 2 AI items, group 2 has 1 AI item."""
    g1 = ExecutionGroup(
        name="WG-1",
        items=[
            _make_item("WDD-TEST-001", "WG-1"),
            _make_item("WDD-TEST-002", "WG-1"),
        ],
        mode="phase_sync",
    )
    g2 = ExecutionGroup(
        name="WG-2",
        items=[_make_item("WDD-TEST-003", "WG-2")],
        mode="phase_sync",
    )
    return _make_plan([g1, g2])


@pytest.fixture
def gen_adapter():
    return MockAdapter(provider_name="gen-mock", model_name="gen-v1")


@pytest.fixture
def val_adapter():
    """Validator that always returns PASS JSON."""
    return SequentialMockAdapter([PASS_JSON] * 200)


@pytest.fixture
def phase_prompts():
    return {p: f"Prompt for {p}" for p in PHASES}


def _build_orchestrator(plan, tmp_path, gen_adapter, val_adapter, phase_prompts, **kwargs):
    return WDDOrchestrator(
        execution_plan=plan,
        initiative_path=str(tmp_path),
        adapters={"default": gen_adapter, "validator": val_adapter},
        wdd_content="# WDD\n\nMock WDD content",
        tdd_content="# TDD\n\nMock TDD content",
        acf_content="# ACF\n\nMock ACF content",
        dcf_content="# DCF\n\nMock DCF content",
        phase_prompts=phase_prompts,
        generate_adapter_name="default",
        validate_adapter_name="validator",
        max_workers=2,
        ledger_path=str(tmp_path / "ledger.md"),
        **kwargs,
    )


# --- Tests ---


class TestModeAPhaseSync:
    def test_all_items_complete_phase_before_next(
        self, two_group_plan, tmp_path, gen_adapter, val_adapter, phase_prompts
    ):
        """In phase_sync mode, all items complete phase N before phase N+1 starts."""
        orch = _build_orchestrator(
            two_group_plan, tmp_path, gen_adapter, val_adapter, phase_prompts
        )
        result = orch.execute()

        assert result.status == "complete"

        # Verify ledger shows both items in group 1 passed all 4 phases
        ledger = orch.ledger
        for item_id in ["WDD-TEST-001", "WDD-TEST-002"]:
            for phase in PHASES:
                assert ledger.is_item_phase_complete(item_id, phase)


class TestModeBIndependent:
    def test_items_run_all_phases(self, tmp_path, gen_adapter, val_adapter, phase_prompts):
        """In independent mode, each item runs all 4 phases."""
        g1 = ExecutionGroup(
            name="WG-1",
            items=[
                _make_item("WDD-TEST-001", "WG-1"),
                _make_item("WDD-TEST-002", "WG-1"),
            ],
            mode="independent",
        )
        plan = _make_plan([g1])
        orch = _build_orchestrator(plan, tmp_path, gen_adapter, val_adapter, phase_prompts)
        result = orch.execute()

        assert result.status == "complete"
        ledger = orch.ledger
        for item_id in ["WDD-TEST-001", "WDD-TEST-002"]:
            for phase in PHASES:
                assert ledger.is_item_phase_complete(item_id, phase)


class TestFileOverlapForcesSequential:
    def test_overlap_forces_phase_sync(self, tmp_path, gen_adapter, val_adapter, phase_prompts):
        """Items with overlapping files_touched should force phase_sync mode."""
        g1 = ExecutionGroup(
            name="WG-1",
            items=[
                _make_item("WDD-TEST-001", "WG-1", files=["src/shared.py", "src/a.py"]),
                _make_item("WDD-TEST-002", "WG-1", files=["src/shared.py", "src/b.py"]),
            ],
            mode="independent",
        )
        plan = _make_plan([g1])
        orch = _build_orchestrator(plan, tmp_path, gen_adapter, val_adapter, phase_prompts)
        result = orch.execute()

        # Should still complete (mode forced to phase_sync)
        assert result.status == "complete"
        # Verify mode was changed
        assert g1.mode == "phase_sync"


class TestHumanItemsSkipped:
    def test_human_items_get_pending_status(
        self, tmp_path, gen_adapter, val_adapter, phase_prompts
    ):
        """Human items should be recorded as pending-human, not executed."""
        g1 = ExecutionGroup(
            name="WG-1",
            items=[
                _make_item("WDD-TEST-001", "WG-1", assignee="AI"),
                _make_item("WDD-TEST-002", "WG-1", assignee="Human"),
            ],
            mode="phase_sync",
        )
        plan = _make_plan([g1])
        orch = _build_orchestrator(plan, tmp_path, gen_adapter, val_adapter, phase_prompts)
        result = orch.execute()

        assert result.items_human_pending == 1
        # Human item has "pending-human" output
        ledger = orch.ledger
        human_status = ledger.get_item_status("WDD-TEST-002")
        for phase in PHASES:
            assert human_status[phase].output_path == "pending-human"
            assert human_status[phase].validation_status == "PENDING"


class TestGroupGateRecorded:
    def test_gate_in_ledger_after_group(
        self, two_group_plan, tmp_path, gen_adapter, val_adapter, phase_prompts
    ):
        """Group gate should appear in ledger after group completes."""
        orch = _build_orchestrator(
            two_group_plan, tmp_path, gen_adapter, val_adapter, phase_prompts
        )
        orch.execute()

        ledger = orch.ledger
        gate = ledger.get_group_status("WG-1")
        assert gate is not None
        assert gate.status == "passed"
        assert gate.items_completed == 2
        assert gate.items_total == 2


class TestFailedGateStopsExecution:
    def test_second_group_not_executed(self, tmp_path, phase_prompts):
        """If first group fails, second group should not execute."""
        fail_adapter = MockAdapter(
            provider_name="fail-mock",
            model_name="fail-v1",
            should_fail=True,
            failure_message="Simulated failure",
        )
        # Validator that returns PASS (won't matter since gen fails)
        val = SequentialMockAdapter([PASS_JSON] * 50)

        g1 = ExecutionGroup(
            name="WG-1",
            items=[_make_item("WDD-TEST-001", "WG-1")],
            mode="phase_sync",
        )
        g2 = ExecutionGroup(
            name="WG-2",
            items=[_make_item("WDD-TEST-002", "WG-2")],
            mode="phase_sync",
        )
        plan = _make_plan([g1, g2])

        orch = _build_orchestrator(
            plan, tmp_path, fail_adapter, val, phase_prompts
        )
        result = orch.execute()

        assert result.status == "failed"
        assert result.groups_completed == 0
        # Group 2 gate should not exist
        ledger = orch.ledger
        assert ledger.get_group_status("WG-2") is None


class TestConvergenceLoopUsed:
    def test_generate_and_validate_are_separate_calls(
        self, tmp_path, gen_adapter, val_adapter, phase_prompts
    ):
        """Generate and validate should be separate invoke() calls."""
        g1 = ExecutionGroup(
            name="WG-1",
            items=[_make_item("WDD-TEST-001", "WG-1")],
            mode="phase_sync",
        )
        plan = _make_plan([g1])
        orch = _build_orchestrator(plan, tmp_path, gen_adapter, val_adapter, phase_prompts)
        orch.execute()

        # Gen adapter should have been called (4 phases)
        assert len(gen_adapter.call_history) >= 4
        # Val adapter should also have been called (4 phases)
        assert len(val_adapter.call_history) >= 4
        # Each gen call should be "invoke"
        for method, args in gen_adapter.call_history:
            assert method == "invoke"
        for method, args in val_adapter.call_history:
            assert method == "invoke"


class TestLedgerUpdatedPerPhase:
    def test_ledger_has_entry_after_each_phase(
        self, tmp_path, gen_adapter, val_adapter, phase_prompts
    ):
        """After each phase completes, the ledger should have an entry."""
        g1 = ExecutionGroup(
            name="WG-1",
            items=[_make_item("WDD-TEST-001", "WG-1")],
            mode="phase_sync",
        )
        plan = _make_plan([g1])
        orch = _build_orchestrator(plan, tmp_path, gen_adapter, val_adapter, phase_prompts)
        orch.execute()

        ledger = orch.ledger
        status = ledger.get_item_status("WDD-TEST-001")
        for phase in PHASES:
            assert phase in status
            assert status[phase].status == "passed"


class TestTwoGroupsSequential:
    def test_group_1_before_group_2(
        self, two_group_plan, tmp_path, gen_adapter, val_adapter, phase_prompts
    ):
        """Group 1 must complete before group 2 starts."""
        orch = _build_orchestrator(
            two_group_plan, tmp_path, gen_adapter, val_adapter, phase_prompts
        )
        result = orch.execute()

        assert result.status == "complete"
        assert result.groups_completed == 2
        assert result.groups_total == 2

        # Both group gates recorded
        ledger = orch.ledger
        assert ledger.get_group_status("WG-1") is not None
        assert ledger.get_group_status("WG-2") is not None
        assert ledger.get_group_status("WG-1").status == "passed"
        assert ledger.get_group_status("WG-2").status == "passed"


class TestExecutionResult:
    def test_correct_counts(
        self, tmp_path, gen_adapter, val_adapter, phase_prompts
    ):
        """Final ExecutionResult should have correct counts."""
        g1 = ExecutionGroup(
            name="WG-1",
            items=[
                _make_item("WDD-TEST-001", "WG-1", assignee="AI"),
                _make_item("WDD-TEST-002", "WG-1", assignee="Human"),
            ],
            mode="phase_sync",
        )
        g2 = ExecutionGroup(
            name="WG-2",
            items=[_make_item("WDD-TEST-003", "WG-2", assignee="AI")],
            mode="phase_sync",
        )
        plan = _make_plan([g1, g2])
        orch = _build_orchestrator(plan, tmp_path, gen_adapter, val_adapter, phase_prompts)
        result = orch.execute()

        assert result.status == "complete"
        assert result.groups_completed == 2
        assert result.groups_total == 2
        assert result.items_completed == 2  # 2 AI items completed
        assert result.items_total == 3  # 2 AI + 1 Human
        assert result.items_failed == 0
        assert result.items_human_pending == 1
        assert "ledger.md" in result.ledger_path
