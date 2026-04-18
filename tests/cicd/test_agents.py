"""M2.3 — tool-using agent pattern tests.

Covers both agent variants against mock adapters. Verifies:
  - Happy path completes a task.
  - Missing adapter -> TaskResult.FAILED with diagnostic.
  - Ambiguous resolution -> TaskResult.FAILED with diagnostic.
  - Registered-but-uninstantiated -> TaskResult.FAILED with diagnostic.
  - Adapter exception -> TaskResult.FAILED.
  - Non-zero adapter exit -> TaskResult.FAILED.
  - LLM judgment: retry succeeds.
  - LLM judgment: retry respects max_retries.
  - LLM judgment: SKIP yields SKIPPED status.
  - LLM judgment: SURRENDER preserves the failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.cicd.agents import (
    AdapterProtocol,
    Decision,
    DeterministicAgent,
    Judgment,
    LLMAgent,
)
from src.cicd.models import AdapterResult, RegistryEntry, TaskStatus
from src.cicd.registry import CapabilityRegistry


class _CannedAdapter:
    """Mock adapter that returns a canned AdapterResult."""

    def __init__(self, *, findings=None, evidence=None, exit_code: int = 0) -> None:
        self._result = AdapterResult(
            findings=findings or {"tests": 1, "failures": 0, "errors": 0},
            evidence=evidence or ["junit-report-1"],
            exit_code=exit_code,
        )
        self.calls: list[dict[str, Any]] = []

    def execute(self, inputs: dict[str, Any]) -> AdapterResult:
        self.calls.append(dict(inputs))
        return self._result


class _RaisingAdapter:
    """Mock adapter that always raises."""

    def execute(self, inputs: dict[str, Any]) -> AdapterResult:
        raise RuntimeError("adapter blew up")


class _RetryableAdapter:
    """Adapter that fails on the first N attempts then succeeds."""

    def __init__(self, fail_first: int) -> None:
        self._fail_first = fail_first
        self.calls = 0

    def execute(self, inputs: dict[str, Any]) -> AdapterResult:
        self.calls += 1
        if self.calls <= self._fail_first:
            return AdapterResult(findings=None, evidence=[], exit_code=1)
        return AdapterResult(
            findings={"tests": 1, "failures": 0, "errors": 0},
            evidence=["junit-report-retry"],
            exit_code=0,
        )


def _register(registry: CapabilityRegistry, adapter_id: str, action: str) -> None:
    entry = RegistryEntry(
        adapter_id=adapter_id,
        adapter_version="1.0.0",
        capabilities=[action],
        contract_versions={action: "1.0.0"},
        attestation_ref=f"stub-ref-{adapter_id}",
        registered_at=datetime(2026, 4, 17, tzinfo=UTC),
    )
    outcome = registry.register_adapter(entry)
    assert outcome.accepted, outcome.diagnostic


# --------------------------------------------------------- DeterministicAgent

def test_deterministic_agent_happy_path(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-pytest-unit", "test.unit")
    adapter = _CannedAdapter()
    agent = DeterministicAgent(registry, {"adapter-pytest-unit": adapter})

    result = agent.receive_task(
        action="test.unit",
        criteria={"min_coverage": 80},
        inputs={"source_dir": "./src"},
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.adapter_id == "adapter-pytest-unit"
    assert result.findings == {"tests": 1, "failures": 0, "errors": 0}
    assert result.evidence == ["junit-report-1"]
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["source_dir"] == "./src"


def test_deterministic_agent_missing_adapter_fails_loudly(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    agent = DeterministicAgent(registry, {})

    result = agent.receive_task("security.dast", {}, {})

    assert result.status == TaskStatus.FAILED
    assert "no adapter registered" in result.error
    assert result.adapter_id == ""


def test_deterministic_agent_ambiguous_resolution_fails(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-a", "test.unit")
    _register(registry, "adapter-b", "test.unit")
    agent = DeterministicAgent(registry, {"adapter-a": _CannedAdapter(), "adapter-b": _CannedAdapter()})

    result = agent.receive_task("test.unit", {}, {})

    assert result.status == TaskStatus.FAILED
    assert "ambiguous" in result.error


def test_deterministic_agent_registered_but_no_instance(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-pytest-unit", "test.unit")
    agent = DeterministicAgent(registry, {})  # no instance for the registered adapter

    result = agent.receive_task("test.unit", {}, {})

    assert result.status == TaskStatus.FAILED
    assert "no runtime instance" in result.error


def test_deterministic_agent_adapter_exception_becomes_failed_result(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-broken", "test.unit")
    agent = DeterministicAgent(registry, {"adapter-broken": _RaisingAdapter()})

    result = agent.receive_task("test.unit", {}, {})

    assert result.status == TaskStatus.FAILED
    assert "RuntimeError" in result.error
    assert "adapter blew up" in result.error


def test_deterministic_agent_nonzero_exit_code_is_failed(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-flaky", "test.unit")
    agent = DeterministicAgent(
        registry, {"adapter-flaky": _CannedAdapter(exit_code=7)}
    )

    result = agent.receive_task("test.unit", {}, {})

    assert result.status == TaskStatus.FAILED
    assert "exit code 7" in result.error


def test_deterministic_agent_context_filters_lookup(artifact_store):
    """Agents bind to a context; registry lookup should respect it."""
    registry = CapabilityRegistry(store=artifact_store)
    # register two adapters under different contexts for the same action
    for adapter_id, env in [("adapter-ci", "ci"), ("adapter-prod", "prod")]:
        entry = RegistryEntry(
            adapter_id=adapter_id,
            adapter_version="1.0.0",
            capabilities=["test.unit"],
            contract_versions={"test.unit": "1.0.0"},
            attestation_ref=f"stub-{adapter_id}",
            registered_at=datetime(2026, 4, 17, tzinfo=UTC),
            context={"environment": env},
        )
        assert registry.register_adapter(entry).accepted
    agent = DeterministicAgent(
        registry,
        adapter_instances={"adapter-ci": _CannedAdapter(), "adapter-prod": _CannedAdapter()},
        context={"environment": "prod"},
    )

    result = agent.receive_task("test.unit", {}, {})

    assert result.status == TaskStatus.COMPLETED
    assert result.adapter_id == "adapter-prod"


# ------------------------------------------------------------------ LLMAgent

def test_llm_agent_retries_until_success(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-flaky", "test.unit")
    flaky = _RetryableAdapter(fail_first=1)

    def judge(_result, _attempt):
        return Judgment(decision=Decision.RETRY, reason="transient failure")

    agent = LLMAgent(
        registry=registry,
        adapter_instances={"adapter-flaky": flaky},
        judgment=judge,
        max_retries=2,
    )

    result = agent.receive_task("test.unit", {}, {"source_dir": "./src"})

    assert result.status == TaskStatus.COMPLETED
    assert flaky.calls == 2


def test_llm_agent_respects_max_retries(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-permaflaky", "test.unit")
    perma_flaky = _RetryableAdapter(fail_first=10)

    def judge(_result, _attempt):
        return Judgment(decision=Decision.RETRY, reason="keep trying")

    agent = LLMAgent(
        registry=registry,
        adapter_instances={"adapter-permaflaky": perma_flaky},
        judgment=judge,
        max_retries=2,
    )

    result = agent.receive_task("test.unit", {}, {})

    assert result.status == TaskStatus.FAILED
    # 1 initial + 2 retries = 3 calls
    assert perma_flaky.calls == 3


def test_llm_agent_skip_decision_yields_skipped_status(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-broken", "test.unit")

    def judge(_result, _attempt):
        return Judgment(decision=Decision.SKIP, reason="triaged as external issue")

    agent = LLMAgent(
        registry=registry,
        adapter_instances={"adapter-broken": _RaisingAdapter()},
        judgment=judge,
    )

    result = agent.receive_task("test.unit", {}, {})

    assert result.status == TaskStatus.SKIPPED
    assert "triaged" in result.error


def test_llm_agent_surrender_preserves_failure(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-broken", "test.unit")

    def judge(_result, _attempt):
        return Judgment(decision=Decision.SURRENDER, reason="not retryable")

    agent = LLMAgent(
        registry=registry,
        adapter_instances={"adapter-broken": _RaisingAdapter()},
        judgment=judge,
    )

    result = agent.receive_task("test.unit", {}, {})

    assert result.status == TaskStatus.FAILED
    assert "adapter blew up" in result.error


def test_llm_agent_judgment_can_adjust_inputs_on_retry(artifact_store):
    registry = CapabilityRegistry(store=artifact_store)
    _register(registry, "adapter-picky", "test.unit")

    class PickyAdapter:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def execute(self, inputs):
            self.calls.append(dict(inputs))
            # succeeds only when coverage_threshold is present
            if "coverage_threshold" in inputs:
                return AdapterResult(findings={"ok": True}, evidence=[], exit_code=0)
            return AdapterResult(findings=None, evidence=[], exit_code=2)

    picky = PickyAdapter()

    def judge(_result, _attempt):
        return Judgment(
            decision=Decision.RETRY,
            adjusted_inputs={"source_dir": "./src", "coverage_threshold": 80},
            reason="missing coverage threshold; retrying with default",
        )

    agent = LLMAgent(
        registry=registry,
        adapter_instances={"adapter-picky": picky},
        judgment=judge,
    )

    result = agent.receive_task("test.unit", {}, {"source_dir": "./src"})

    assert result.status == TaskStatus.COMPLETED
    assert picky.calls[0] == {"source_dir": "./src"}
    assert picky.calls[1]["coverage_threshold"] == 80
