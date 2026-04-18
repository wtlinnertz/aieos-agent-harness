"""Tool-using agent pattern.

Agents sit between the run orchestrator and the capability registry. An agent
receives a task (action + criteria + inputs), looks up the adapter in the
registry, invokes it, and returns a TaskResult. Two variants:

- DeterministicAgent — no LLM. Fixed decision path. Fails loudly on ambiguity
  (multiple registered adapters for the same action) or missing adapters.
- LLMAgent — wraps DeterministicAgent with a judgment callable that can
  decide whether to retry a failed task, adjust inputs, or surrender.

Adapter execution is abstracted behind a protocol; the agent holds a map
from adapter_id to runtime adapter instances (injected at harness boot).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

import structlog

from .models import AdapterResult, TaskResult, TaskStatus
from .registry import CapabilityRegistry

log = structlog.get_logger(__name__)


@runtime_checkable
class AdapterProtocol(Protocol):
    """Runtime interface every CI/CD adapter exposes to the harness."""

    def execute(self, inputs: dict[str, Any]) -> AdapterResult: ...


class Decision(str, Enum):
    RETRY = "retry"
    SURRENDER = "surrender"
    SKIP = "skip"


@dataclass
class Judgment:
    """What the LLM judgment callable returns after a failure."""

    decision: Decision
    adjusted_inputs: dict[str, Any] | None = None
    reason: str = ""


JudgmentCallable = Callable[[TaskResult, int], Judgment]


def _no_adapter_result(action: str, diagnostic: str) -> TaskResult:
    return TaskResult(
        action=action,
        adapter_id="",
        findings=None,
        evidence=[],
        status=TaskStatus.FAILED,
        error=diagnostic,
    )


class DeterministicAgent:
    """Agent with no judgment loop. Used for the no-LLM path."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        adapter_instances: dict[str, AdapterProtocol],
        context: dict[str, str] | None = None,
    ) -> None:
        self._registry = registry
        self._instances = dict(adapter_instances)
        self._context = dict(context or {})

    def receive_task(
        self,
        action: str,
        criteria: dict[str, Any],
        inputs: dict[str, Any],
    ) -> TaskResult:
        entries = self._registry.find_adapters(action, context=self._context)
        if not entries:
            return _no_adapter_result(
                action, f"no adapter registered for action {action!r}"
            )
        if len(entries) > 1:
            candidates = [f"{e.adapter_id}@{e.adapter_version}" for e in entries]
            return _no_adapter_result(
                action,
                f"ambiguous: {len(entries)} adapters satisfy {action!r}: {candidates}",
            )
        entry = entries[0]
        instance = self._instances.get(entry.adapter_id)
        if instance is None:
            return _no_adapter_result(
                action,
                f"adapter {entry.adapter_id!r} is registered but no runtime "
                "instance is available to this agent",
            )

        try:
            result: AdapterResult = instance.execute(inputs)
        except Exception as exc:  # noqa: BLE001 - adapter errors become TaskResult
            log.warning(
                "adapter_raised",
                adapter_id=entry.adapter_id,
                action=action,
                exc_type=type(exc).__name__,
                exc=str(exc),
            )
            return TaskResult(
                action=action,
                adapter_id=entry.adapter_id,
                findings=None,
                evidence=[],
                status=TaskStatus.FAILED,
                error=f"adapter raised {type(exc).__name__}: {exc}",
            )

        status = TaskStatus.COMPLETED if result.exit_code == 0 else TaskStatus.FAILED
        error = None if status == TaskStatus.COMPLETED else f"adapter exit code {result.exit_code}"
        return TaskResult(
            action=action,
            adapter_id=entry.adapter_id,
            findings=result.findings,
            evidence=list(result.evidence),
            status=status,
            error=error,
        )


class LLMAgent:
    """Agent with a loaded judgment callable that can intervene on failures.

    The judgment callable receives the failed TaskResult and the retry count
    so far, and returns a Judgment. RETRY retries (optionally with adjusted
    inputs). SKIP marks the task as skipped. SURRENDER preserves the failure.

    max_retries caps the retry loop so a buggy judgment cannot hang the run.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        adapter_instances: dict[str, AdapterProtocol],
        judgment: JudgmentCallable,
        context: dict[str, str] | None = None,
        max_retries: int = 2,
    ) -> None:
        self._inner = DeterministicAgent(registry, adapter_instances, context)
        self._judgment = judgment
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._max_retries = max_retries

    def receive_task(
        self,
        action: str,
        criteria: dict[str, Any],
        inputs: dict[str, Any],
    ) -> TaskResult:
        current_inputs = dict(inputs)
        retries = 0
        while True:
            result = self._inner.receive_task(action, criteria, current_inputs)
            if result.status == TaskStatus.COMPLETED:
                return result
            if retries >= self._max_retries:
                return result

            judgment = self._judgment(result, retries)
            log.info(
                "llm_judgment",
                action=action,
                attempt=retries + 1,
                decision=judgment.decision.value,
                reason=judgment.reason,
            )
            if judgment.decision == Decision.SURRENDER:
                return result
            if judgment.decision == Decision.SKIP:
                return TaskResult(
                    action=action,
                    adapter_id=result.adapter_id,
                    findings=None,
                    evidence=[],
                    status=TaskStatus.SKIPPED,
                    error=judgment.reason or "LLM judgment skipped this task",
                )
            if judgment.decision == Decision.RETRY:
                if judgment.adjusted_inputs is not None:
                    current_inputs = dict(judgment.adjusted_inputs)
                retries += 1
                continue
            return result  # unknown decision -> preserve failure
