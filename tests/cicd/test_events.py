"""M2.4 — structured run-log emission tests."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

from src.cicd.events import (
    EmittingDeterministicAgent,
    EmittingLLMAgent,
    RunEventEmitter,
)
from src.cicd.models import AdapterResult, RegistryEntry, TaskStatus
from src.cicd.registry import CapabilityRegistry


FIXED_NOW = datetime(2026, 4, 17, 18, 30, 0, tzinfo=UTC)


def _lines(buf: io.StringIO) -> list[dict[str, Any]]:
    buf.seek(0)
    return [json.loads(line) for line in buf.read().splitlines() if line.strip()]


def _register(registry: CapabilityRegistry, adapter_id: str, action: str) -> None:
    entry = RegistryEntry(
        adapter_id=adapter_id,
        adapter_version="1.0.0",
        capabilities=[action],
        contract_versions={action: "1.0.0"},
        attestation_ref=f"stub-{adapter_id}",
        registered_at=datetime(2026, 4, 17, tzinfo=UTC),
    )
    assert registry.register_adapter(entry).accepted


class _Adapter:
    def __init__(self, *, evidence=None, exit_code=0):
        self._evidence = ["junit-report-1"] if evidence is None else list(evidence)
        self._exit_code = exit_code

    def execute(self, inputs):
        return AdapterResult(
            findings={"tests": 1, "failures": 0, "errors": 0},
            evidence=list(self._evidence),
            exit_code=self._exit_code,
        )


# --------------------------------------------------------- emitter shapes


def test_run_start_event_shape():
    buf = io.StringIO()
    emitter = RunEventEmitter(
        run_id="run-1",
        spec_ref="file://.aieos/ci.spec.yaml",
        out=buf,
        clock=lambda: FIXED_NOW,
    )

    emitter.run_start()

    events = _lines(buf)
    assert events == [
        {
            "type": "run.start",
            "run_id": "run-1",
            "spec_ref": "file://.aieos/ci.spec.yaml",
            "timestamp": "2026-04-17T18:30:00Z",
        }
    ]


def test_run_end_event_shape():
    buf = io.StringIO()
    emitter = RunEventEmitter(run_id="run-1", out=buf, clock=lambda: FIXED_NOW)

    emitter.run_end("pass")

    events = _lines(buf)
    assert events == [
        {
            "type": "run.end",
            "run_id": "run-1",
            "status": "pass",
            "timestamp": "2026-04-17T18:30:00Z",
        }
    ]


def test_event_carries_run_id_and_task_id():
    buf = io.StringIO()
    emitter = RunEventEmitter(run_id="run-abc", out=buf, clock=lambda: FIXED_NOW)

    emitter.task_start(
        task_id="t-1", action="test.unit", adapter_id="adapter-pytest-unit"
    )
    emitter.task_evidence(task_id="t-1", evidence_ref="junit-1")
    emitter.task_result(
        task_id="t-1",
        action="test.unit",
        adapter_id="adapter-pytest-unit",
        status="completed",
        findings_ref="inline://adapter-pytest-unit",
    )

    events = _lines(buf)
    for event in events:
        assert event["run_id"] == "run-abc"
        assert event.get("task_id") == "t-1"
    assert [e["type"] for e in events] == [
        "task.start",
        "task.evidence",
        "task.result",
    ]


def test_events_are_valid_json_one_per_line():
    buf = io.StringIO()
    emitter = RunEventEmitter(run_id="run-1", out=buf, clock=lambda: FIXED_NOW)

    emitter.run_start()
    emitter.task_start(task_id="t-1", action="test.unit")
    emitter.task_result(
        task_id="t-1", action="test.unit", adapter_id="adapter-x", status="completed"
    )
    emitter.run_end("pass")

    buf.seek(0)
    lines = buf.read().splitlines()
    # every line parses as valid JSON
    parsed = [json.loads(line) for line in lines if line.strip()]
    assert len(parsed) == 4
    # each event carries the mandatory baseline fields
    for event in parsed:
        assert "type" in event
        assert "run_id" in event
        assert "timestamp" in event


def test_emitter_rejects_empty_run_id():
    import pytest

    with pytest.raises(ValueError):
        RunEventEmitter(run_id="", out=io.StringIO())


# ----------------------------------------------------- agent integration


def test_task_lifecycle_events_emitted_in_order(artifact_store):
    buf = io.StringIO()
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-pytest-unit", "test.unit")
    emitter = RunEventEmitter(run_id="run-1", out=buf, clock=lambda: FIXED_NOW)
    agent = EmittingDeterministicAgent(
        registry=registry,
        adapter_instances={"adapter-pytest-unit": _Adapter(evidence=["ev-a", "ev-b"])},
        emitter=emitter,
    )

    result = agent.receive_task(
        "test.unit", {}, {"source_dir": "./src"}, task_id="t-42"
    )

    assert result.status == TaskStatus.COMPLETED

    events = _lines(buf)
    types = [e["type"] for e in events]
    assert types == ["task.start", "task.evidence", "task.evidence", "task.result"]
    assert events[0] == {
        "type": "task.start",
        "run_id": "run-1",
        "task_id": "t-42",
        "action": "test.unit",
        "adapter_id": "",
        "timestamp": "2026-04-17T18:30:00Z",
    }
    assert events[1]["evidence_ref"] == "ev-a"
    assert events[2]["evidence_ref"] == "ev-b"
    assert events[3]["type"] == "task.result"
    assert events[3]["status"] == "completed"
    assert events[3]["adapter_id"] == "adapter-pytest-unit"


def test_failed_task_still_emits_task_result(artifact_store):
    buf = io.StringIO()
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-pytest-unit", "test.unit")
    emitter = RunEventEmitter(run_id="run-1", out=buf, clock=lambda: FIXED_NOW)
    agent = EmittingDeterministicAgent(
        registry=registry,
        adapter_instances={"adapter-pytest-unit": _Adapter(exit_code=3, evidence=[])},
        emitter=emitter,
    )

    result = agent.receive_task("test.unit", {}, {}, task_id="t-fail")

    assert result.status == TaskStatus.FAILED
    events = _lines(buf)
    assert [e["type"] for e in events] == ["task.start", "task.result"]
    assert events[-1]["status"] == "failed"


def test_llm_agent_emits_single_task_result_after_retries(artifact_store):
    """LLMAgent retries happen inside; only one task.result surfaces."""
    from src.cicd.agents import Decision, Judgment

    class _Flaky:
        def __init__(self):
            self.calls = 0

        def execute(self, inputs):
            self.calls += 1
            if self.calls < 2:
                return AdapterResult(findings=None, evidence=[], exit_code=1)
            return AdapterResult(
                findings={"ok": True}, evidence=["ev-final"], exit_code=0
            )

    buf = io.StringIO()
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-flaky", "test.unit")
    emitter = RunEventEmitter(run_id="run-1", out=buf, clock=lambda: FIXED_NOW)

    def judge(_r, _attempt):
        return Judgment(decision=Decision.RETRY)

    agent = EmittingLLMAgent(
        registry=registry,
        adapter_instances={"adapter-flaky": _Flaky()},
        judgment=judge,
        emitter=emitter,
    )

    result = agent.receive_task("test.unit", {}, {}, task_id="t-retry")

    assert result.status == TaskStatus.COMPLETED
    events = _lines(buf)
    # one task.start, one task.evidence (the final success), one task.result
    types = [e["type"] for e in events]
    assert types == ["task.start", "task.evidence", "task.result"]
    assert events[-1]["status"] == "completed"
