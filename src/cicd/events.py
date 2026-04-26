"""Structured run-log event emission.

Emits JSON-line events to a configurable output stream (stdout by default).
Events are the harness's machine-readable narrative of a run; the future log
forwarder consumes them.

Event types (all carry type + run_id + timestamp):
  run.start        — additional: spec_ref
  task.start       — additional: task_id, action, adapter_id
  task.evidence    — additional: task_id, evidence_ref
  task.result      — additional: task_id, action, adapter_id, status, findings_ref
  run.end          — additional: status

structlog is used in the module's internal logger (matches harness
conventions); the event stream itself is json.dumps for deterministic,
portable output.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TextIO

import structlog

from .agents import AdapterProtocol, DeterministicAgent, LLMAgent
from .models import TaskResult, TaskStatus
from .registry import CapabilityRegistry

log = structlog.get_logger(__name__)


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _iso(ts: datetime) -> str:
    # Force a Z suffix for UTC to match Sigstore/ISO-8601 convention.
    s = ts.isoformat()
    if s.endswith("+00:00"):
        return s[:-6] + "Z"
    return s


class RunEventEmitter:
    """Emits structured events for one run.

    Construction binds a run_id (ULID/UUID from the orchestrator) and an
    optional spec_ref. Events are written as JSON objects, one per line,
    sorted-keys for deterministic output.
    """

    def __init__(
        self,
        run_id: str,
        spec_ref: str = "",
        out: TextIO | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not run_id:
            raise ValueError("run_id must be non-empty")
        self._run_id = run_id
        self._spec_ref = spec_ref
        self._out: TextIO = out if out is not None else sys.stdout
        self._clock = clock or _default_clock

    # ------------------------------------------------------------ internal
    def _emit(self, payload: dict[str, Any]) -> None:
        enriched = dict(payload)
        enriched["run_id"] = self._run_id
        enriched["timestamp"] = _iso(self._clock())
        line = json.dumps(enriched, sort_keys=True)
        self._out.write(line + "\n")
        self._out.flush()

    # ------------------------------------------------------------- events
    def run_start(self) -> None:
        self._emit({"type": "run.start", "spec_ref": self._spec_ref})

    def task_start(self, task_id: str, action: str, adapter_id: str = "") -> None:
        self._emit(
            {
                "type": "task.start",
                "task_id": task_id,
                "action": action,
                "adapter_id": adapter_id,
            }
        )

    def task_evidence(self, task_id: str, evidence_ref: str) -> None:
        self._emit(
            {
                "type": "task.evidence",
                "task_id": task_id,
                "evidence_ref": evidence_ref,
            }
        )

    def task_result(
        self,
        task_id: str,
        action: str,
        adapter_id: str,
        status: str,
        findings_ref: str = "",
    ) -> None:
        self._emit(
            {
                "type": "task.result",
                "task_id": task_id,
                "action": action,
                "adapter_id": adapter_id,
                "status": status,
                "findings_ref": findings_ref,
            }
        )

    def run_end(self, status: str) -> None:
        self._emit({"type": "run.end", "status": status})


# ------------------------------------------------------- agent integration


class EmittingDeterministicAgent(DeterministicAgent):
    """DeterministicAgent that emits task-lifecycle events via a RunEventEmitter."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        adapter_instances: dict[str, AdapterProtocol],
        emitter: RunEventEmitter,
        context: dict[str, str] | None = None,
    ) -> None:
        super().__init__(registry, adapter_instances, context)
        self._emitter = emitter

    def receive_task(
        self,
        action: str,
        criteria: dict[str, Any],
        inputs: dict[str, Any],
        task_id: str | None = None,
    ) -> TaskResult:
        tid = task_id or _synth_task_id(action)
        # Emit task.start with adapter_id unknown at this point
        self._emitter.task_start(task_id=tid, action=action, adapter_id="")

        result = super().receive_task(action=action, criteria=criteria, inputs=inputs)

        for evidence_ref in result.evidence:
            self._emitter.task_evidence(task_id=tid, evidence_ref=evidence_ref)

        findings_ref = _placeholder_findings_ref(result)
        self._emitter.task_result(
            task_id=tid,
            action=action,
            adapter_id=result.adapter_id,
            status=result.status.value
            if isinstance(result.status, TaskStatus)
            else str(result.status),
            findings_ref=findings_ref,
        )
        return result


class EmittingLLMAgent(LLMAgent):
    """LLMAgent that emits events. Only the final task_result is emitted;
    per-attempt retries are captured in the module logger, not the event stream."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        adapter_instances: dict[str, AdapterProtocol],
        judgment,
        emitter: RunEventEmitter,
        context: dict[str, str] | None = None,
        max_retries: int = 2,
    ) -> None:
        super().__init__(
            registry=registry,
            adapter_instances=adapter_instances,
            judgment=judgment,
            context=context,
            max_retries=max_retries,
        )
        self._emitter = emitter

    def receive_task(
        self,
        action: str,
        criteria: dict[str, Any],
        inputs: dict[str, Any],
        task_id: str | None = None,
    ) -> TaskResult:
        tid = task_id or _synth_task_id(action)
        self._emitter.task_start(task_id=tid, action=action, adapter_id="")
        result = super().receive_task(action=action, criteria=criteria, inputs=inputs)
        for ev in result.evidence:
            self._emitter.task_evidence(task_id=tid, evidence_ref=ev)
        self._emitter.task_result(
            task_id=tid,
            action=action,
            adapter_id=result.adapter_id,
            status=result.status.value
            if isinstance(result.status, TaskStatus)
            else str(result.status),
            findings_ref=_placeholder_findings_ref(result),
        )
        return result


# ----------------------------------------------------------------- helpers


def _synth_task_id(action: str) -> str:
    """Deterministic-ish task_id when the orchestrator doesn't supply one."""
    import uuid

    return f"{action}-{uuid.uuid4().hex[:12]}"


def _placeholder_findings_ref(result: TaskResult) -> str:
    """Until the artifact store wires findings persistence, emit a placeholder
    ref that downstream consumers can still route on."""
    if result.findings is None:
        return ""
    return f"inline://{result.adapter_id or 'unknown'}"
