"""Tests for the execution ledger module."""

import pytest

from src.execution_ledger import ExecutionLedger, GroupGateResult, ItemPhaseStatus


class TestRecordPhaseStart:
    def test_status_becomes_running(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        ledger.record_phase_start("WDD-TEST-001", "tests", "mock-agent")

        status = ledger.get_item_status("WDD-TEST-001")
        assert "tests" in status
        assert status["tests"].status == "running"
        assert status["tests"].agent_id == "mock-agent"
        assert status["tests"].started is not None


class TestRecordPhaseComplete:
    def test_status_becomes_passed_with_output(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        ledger.record_phase_start("WDD-TEST-001", "code", "mock-agent")
        ledger.record_phase_complete(
            "WDD-TEST-001", "code", "/out/code.md", "PASS", 1
        )

        status = ledger.get_item_status("WDD-TEST-001")
        assert status["code"].status == "passed"
        assert status["code"].output_path == "/out/code.md"
        assert status["code"].validation_status == "PASS"
        assert status["code"].convergence_iterations == 1
        assert status["code"].completed is not None


class TestRecordPhaseFailed:
    def test_status_becomes_failed(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        ledger.record_phase_start("WDD-TEST-001", "review", "mock-agent")
        ledger.record_phase_failed("WDD-TEST-001", "review", "gate X failed")

        status = ledger.get_item_status("WDD-TEST-001")
        assert status["review"].status == "failed"
        assert "gate X failed" in status["review"].validation_status

    def test_record_phase_failed_without_prior_start(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        ledger.record_phase_failed("WDD-TEST-002", "plan", "unexpected error")

        status = ledger.get_item_status("WDD-TEST-002")
        assert status["plan"].status == "failed"
        assert "unexpected error" in status["plan"].validation_status


class TestRecordGroupGate:
    def test_gate_recorded_correctly(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        gate = GroupGateResult(
            group_name="WG-1",
            status="passed",
            tests_before=10,
            tests_after=15,
            tests_delta=5,
            lint_clean=True,
            items_completed=3,
            items_total=3,
        )
        ledger.record_group_gate(gate)

        result = ledger.get_group_status("WG-1")
        assert result is not None
        assert result.status == "passed"
        assert result.tests_before == 10
        assert result.tests_after == 15
        assert result.tests_delta == 5
        assert result.lint_clean is True
        assert result.items_completed == 3
        assert result.items_total == 3


class TestSaveLoadRoundtrip:
    def test_roundtrip_preserves_data(self, tmp_path):
        ledger_path = tmp_path / "ledger.md"
        ledger = ExecutionLedger(ledger_path)

        # Record some phases
        ledger.record_phase_start("WDD-TEST-001", "tests", "agent-a")
        ledger.record_phase_complete(
            "WDD-TEST-001", "tests", "/out/tests.md", "PASS", 1
        )
        ledger.record_phase_start("WDD-TEST-001", "plan", "agent-a")
        ledger.record_phase_complete(
            "WDD-TEST-001", "plan", "/out/plan.md", "PASS", 2
        )
        ledger.record_phase_start("WDD-TEST-002", "tests", "agent-b")
        ledger.record_phase_failed("WDD-TEST-002", "tests", "timeout")

        # Record a gate
        gate = GroupGateResult(
            group_name="WG-1",
            status="passed",
            tests_before=5,
            tests_after=8,
            tests_delta=3,
            lint_clean=True,
            items_completed=2,
            items_total=2,
        )
        ledger.record_group_gate(gate)

        # Load into a new instance
        ledger2 = ExecutionLedger(ledger_path)

        # Verify phase data
        s1 = ledger2.get_item_status("WDD-TEST-001")
        assert s1["tests"].status == "passed"
        assert s1["tests"].output_path == "/out/tests.md"
        assert s1["tests"].convergence_iterations == 1
        assert s1["plan"].status == "passed"
        assert s1["plan"].output_path == "/out/plan.md"
        assert s1["plan"].convergence_iterations == 2

        s2 = ledger2.get_item_status("WDD-TEST-002")
        assert s2["tests"].status == "failed"

        # Verify gate data
        g = ledger2.get_group_status("WG-1")
        assert g is not None
        assert g.status == "passed"
        assert g.tests_before == 5
        assert g.tests_after == 8
        assert g.tests_delta == 3
        assert g.lint_clean is True
        assert g.items_completed == 2


class TestIsItemPhaseComplete:
    def test_true_for_passed(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        ledger.record_phase_start("WDD-TEST-001", "code", "agent-a")
        ledger.record_phase_complete(
            "WDD-TEST-001", "code", "/out/code.md", "PASS", 1
        )
        assert ledger.is_item_phase_complete("WDD-TEST-001", "code") is True

    def test_false_for_running(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        ledger.record_phase_start("WDD-TEST-001", "code", "agent-a")
        assert ledger.is_item_phase_complete("WDD-TEST-001", "code") is False

    def test_false_for_failed(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        ledger.record_phase_start("WDD-TEST-001", "code", "agent-a")
        ledger.record_phase_failed("WDD-TEST-001", "code", "error")
        assert ledger.is_item_phase_complete("WDD-TEST-001", "code") is False

    def test_false_for_missing(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        assert ledger.is_item_phase_complete("WDD-TEST-001", "code") is False


class TestIsGroupComplete:
    def test_true_when_all_phases_passed(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        for phase in ["tests", "plan", "code", "review"]:
            ledger.record_phase_start("WDD-TEST-001", phase, "agent-a")
            ledger.record_phase_complete(
                "WDD-TEST-001", phase, f"/out/{phase}.md", "PASS", 1
            )
            ledger.record_phase_start("WDD-TEST-002", phase, "agent-a")
            ledger.record_phase_complete(
                "WDD-TEST-002", phase, f"/out/{phase}.md", "PASS", 1
            )

        assert ledger.is_group_complete(
            "WG-1", ["WDD-TEST-001", "WDD-TEST-002"]
        ) is True

    def test_false_when_phase_missing(self, tmp_path):
        ledger = ExecutionLedger(tmp_path / "ledger.md")
        for phase in ["tests", "plan", "code"]:
            ledger.record_phase_start("WDD-TEST-001", phase, "agent-a")
            ledger.record_phase_complete(
                "WDD-TEST-001", phase, f"/out/{phase}.md", "PASS", 1
            )
        # review not completed
        assert ledger.is_group_complete("WG-1", ["WDD-TEST-001"]) is False


class TestResumeFromPartial:
    def test_load_partial_and_verify_status(self, tmp_path):
        ledger_path = tmp_path / "ledger.md"
        ledger = ExecutionLedger(ledger_path)

        # Complete tests and plan phases
        ledger.record_phase_start("WDD-TEST-001", "tests", "agent-a")
        ledger.record_phase_complete(
            "WDD-TEST-001", "tests", "/out/tests.md", "PASS", 1
        )
        ledger.record_phase_start("WDD-TEST-001", "plan", "agent-a")
        ledger.record_phase_complete(
            "WDD-TEST-001", "plan", "/out/plan.md", "PASS", 1
        )

        # Load fresh ledger (simulating resumption)
        ledger2 = ExecutionLedger(ledger_path)

        # Completed phases show as complete
        assert ledger2.is_item_phase_complete("WDD-TEST-001", "tests") is True
        assert ledger2.is_item_phase_complete("WDD-TEST-001", "plan") is True
        # Remaining phases show as incomplete
        assert ledger2.is_item_phase_complete("WDD-TEST-001", "code") is False
        assert ledger2.is_item_phase_complete("WDD-TEST-001", "review") is False
