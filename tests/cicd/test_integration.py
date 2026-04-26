"""M2.5 — integration-level contract tests across the CI/CD capability substrate.

These wire the four M2 subsystems together and exercise them as a cohesive
whole. They complement the unit tests in test_registry/test_attestation/
test_agents/test_events by checking the seams.

Scenarios covered:
  1. Registry round-trip with real attestation verification.
  2. Attestation verifier rejects three bad-attestation paths: schema
     violation, untrusted identity, and wrong contract version.
  3. Cutover grace logic: all three states (before, at, after) verified
     end-to-end against the registry flow.
  4. Structured events emit to stdout in the expected shape during a task.
  5. End-to-end: agent receives task -> resolves in registry -> invokes
     adapter -> emits events -> returns TaskResult.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

from src.cicd.artifact_store import FilesystemArtifactStore
from src.cicd.attestation import AttestationVerifier, ContractRegistration
from src.cicd.events import EmittingDeterministicAgent, RunEventEmitter
from src.cicd.models import AdapterResult, RegistryEntry, TaskStatus
from src.cicd.registry import CapabilityRegistry


TRUSTED_IDENTITY = (
    "https://github.com/wtlinnertz/adapter-pytest-unit/.github/workflows/"
    "adapter-ci.yml@refs/heads/main"
)


def _attestation_payload(
    *,
    adapter_id: str,
    adapter_version: str,
    contract_id: str,
    contract_version: str,
    signing_identity: str = TRUSTED_IDENTITY,
    result: str = "pass",
    suite_run_id: str = "550e8400-e29b-41d4-a716-446655440000",
    timestamp: str = "2026-04-17T18:30:00Z",
) -> bytes:
    return json.dumps(
        {
            "subject": {"adapter_id": adapter_id, "adapter_version": adapter_version},
            "predicate": {
                "contract_id": contract_id,
                "contract_version": contract_version,
            },
            "suite_run_id": suite_run_id,
            "result": result,
            "signing_identity": signing_identity,
            "timestamp": timestamp,
        }
    ).encode("utf-8")


def _make_entry(
    *,
    adapter_id: str = "adapter-pytest-unit",
    adapter_version: str = "1.0.0",
    action: str = "test.unit",
    attestation_ref: str,
    contract_version: str = "1.0.0",
) -> RegistryEntry:
    return RegistryEntry(
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        capabilities=[action],
        contract_versions={action: contract_version},
        attestation_ref=attestation_ref,
        registered_at=datetime(2026, 4, 17, tzinfo=UTC),
    )


class _MockAdapter:
    """Pytest-looking adapter that returns canned canonical JUnit findings."""

    def __init__(self):
        self.calls = 0

    def execute(self, inputs):
        self.calls += 1
        return AdapterResult(
            findings={
                "name": "unit",
                "tests": 2,
                "failures": 0,
                "errors": 0,
                "testsuite": [
                    {
                        "name": "smoke",
                        "tests": 2,
                        "failures": 0,
                        "errors": 0,
                        "testcase": [
                            {"name": "a", "classname": "smoke"},
                            {"name": "b", "classname": "smoke"},
                        ],
                    }
                ],
            },
            evidence=["junit-report://local/junit.json"],
            exit_code=0,
        )


def _wire_registry(store, contract_version="1.0.0", prior=None, now=None):
    verifier = AttestationVerifier(
        trusted_identities={TRUSTED_IDENTITY},
        contract_registrations={
            "test.unit": ContractRegistration(
                current_version=contract_version,
                prior_versions_cutover=prior or {},
            )
        },
        now=(lambda: now) if now is not None else None,
    )
    return CapabilityRegistry(store=store, attestation_verifier=verifier)


# ------------------------------------------------------- 1. round-trip


def test_integration_registry_round_trip_with_real_verifier(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = _attestation_payload(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        contract_id="test.unit",
        contract_version="1.0.0",
    )
    att_path = tmp_path / "attestation.json"
    att_path.write_bytes(payload)

    registry = _wire_registry(store)
    entry = _make_entry(attestation_ref=f"file://{att_path}")

    outcome = registry.register_adapter(entry)

    assert outcome.accepted is True
    found = registry.find_adapters("test.unit")
    assert len(found) == 1
    assert found[0].adapter_id == "adapter-pytest-unit"
    # Persistence: new registry over same store sees the entry.
    rebuilt = CapabilityRegistry(store=store)
    assert rebuilt.find_adapters("test.unit")[0].adapter_id == "adapter-pytest-unit"


# ------------------------------------------------- 2. reject bad attestations


def test_integration_verifier_rejects_schema_violation(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "store")
    bad = _attestation_payload(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        contract_id="test.unit",
        contract_version="1.0.0",
        result="fail",  # violates const: "pass"
    )
    att = tmp_path / "bad.json"
    att.write_bytes(bad)
    registry = _wire_registry(store)
    entry = _make_entry(attestation_ref=f"file://{att}")

    outcome = registry.register_adapter(entry)

    assert outcome.accepted is False
    assert "schema validation" in outcome.diagnostic


def test_integration_verifier_rejects_untrusted_identity(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = _attestation_payload(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        contract_id="test.unit",
        contract_version="1.0.0",
        signing_identity="https://evil.example/workflows/steal.yml@refs/heads/main",
    )
    att = tmp_path / "untrusted.json"
    att.write_bytes(payload)
    registry = _wire_registry(store)
    entry = _make_entry(attestation_ref=f"file://{att}")

    outcome = registry.register_adapter(entry)

    assert outcome.accepted is False
    assert "trusted set" in outcome.diagnostic


def test_integration_verifier_rejects_wrong_contract_version(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = _attestation_payload(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        contract_id="test.unit",
        contract_version="0.9.0",
    )
    att = tmp_path / "wrong-version.json"
    att.write_bytes(payload)
    registry = _wire_registry(store, contract_version="1.0.0")
    entry = _make_entry(attestation_ref=f"file://{att}")

    outcome = registry.register_adapter(entry)

    assert outcome.accepted is False
    assert "not current" in outcome.diagnostic


# ----------------------------------------------- 3. cutover grace (all three)


def test_integration_cutover_before_after_and_at(tmp_path):
    """Register the SAME prior-version adapter against registries wired for
    three times: before cutover, at cutover, after cutover. First accepts;
    second and third reject."""
    cutover = datetime(2026, 6, 1, tzinfo=UTC)
    payload = _attestation_payload(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        contract_id="test.unit",
        contract_version="1.0.0",  # prior version
    )
    att = tmp_path / "prior.json"
    att.write_bytes(payload)

    # Three separate registries (fresh stores) representing three points in time.
    times = {
        "before": datetime(2026, 5, 1, tzinfo=UTC),
        "at": cutover,
        "after": datetime(2026, 7, 1, tzinfo=UTC),
    }
    outcomes = {}
    for label, t in times.items():
        store = FilesystemArtifactStore(tmp_path / f"store-{label}")
        registry = _wire_registry(
            store,
            contract_version="1.1.0",
            prior={"1.0.0": cutover},
            now=t,
        )
        outcomes[label] = registry.register_adapter(
            _make_entry(attestation_ref=f"file://{att}")
        )

    assert outcomes["before"].accepted is True
    assert outcomes["at"].accepted is False
    assert outcomes["after"].accepted is False
    assert "cutover" in outcomes["after"].diagnostic.lower()


# ----------------------------------------- 4. events during task execution


def test_integration_task_execution_emits_expected_event_sequence(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = _attestation_payload(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        contract_id="test.unit",
        contract_version="1.0.0",
    )
    att = tmp_path / "att.json"
    att.write_bytes(payload)
    registry = _wire_registry(store)
    registry.register_adapter(_make_entry(attestation_ref=f"file://{att}"))

    buf = io.StringIO()
    emitter = RunEventEmitter(
        run_id="run-integration-1",
        spec_ref="file://.aieos/ci.spec.yaml",
        out=buf,
        clock=lambda: datetime(2026, 4, 17, 18, 30, tzinfo=UTC),
    )
    agent = EmittingDeterministicAgent(
        registry=registry,
        adapter_instances={"adapter-pytest-unit": _MockAdapter()},
        emitter=emitter,
    )

    # Run lifecycle: run.start -> receive_task -> run.end
    emitter.run_start()
    result = agent.receive_task(
        action="test.unit",
        criteria={"min_coverage": 80},
        inputs={"source_dir": "./src"},
        task_id="task-0001",
    )
    emitter.run_end(status="pass")

    assert result.status == TaskStatus.COMPLETED

    buf.seek(0)
    lines = [json.loads(line) for line in buf.read().splitlines() if line.strip()]
    assert [e["type"] for e in lines] == [
        "run.start",
        "task.start",
        "task.evidence",
        "task.result",
        "run.end",
    ]
    assert lines[0]["spec_ref"] == "file://.aieos/ci.spec.yaml"
    assert lines[1]["task_id"] == "task-0001"
    assert lines[2]["evidence_ref"] == "junit-report://local/junit.json"
    assert lines[3]["status"] == "completed"
    assert lines[3]["adapter_id"] == "adapter-pytest-unit"
    assert lines[4]["status"] == "pass"
    for event in lines:
        assert event["run_id"] == "run-integration-1"


# -------------------------------------- 5. end-to-end (registry + adapter + agent + events)


def test_integration_end_to_end_task_flow(tmp_path, capsys):
    """Drive one task through the full stack and capture stdout-equivalent
    output via an in-memory StringIO. Asserts the TaskResult is consistent
    with the emitted events."""
    store = FilesystemArtifactStore(tmp_path / "store")
    payload = _attestation_payload(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        contract_id="test.unit",
        contract_version="1.0.0",
    )
    att = tmp_path / "att.json"
    att.write_bytes(payload)
    registry = _wire_registry(store)
    registry.register_adapter(_make_entry(attestation_ref=f"file://{att}"))
    adapter = _MockAdapter()

    buf = io.StringIO()
    emitter = RunEventEmitter(
        run_id="run-e2e",
        out=buf,
        clock=lambda: datetime(2026, 4, 17, 18, 30, tzinfo=UTC),
    )
    agent = EmittingDeterministicAgent(
        registry=registry,
        adapter_instances={"adapter-pytest-unit": adapter},
        emitter=emitter,
    )

    result = agent.receive_task(
        action="test.unit",
        criteria={"min_coverage": 80},
        inputs={"source_dir": "./src"},
        task_id="task-e2e",
    )

    # Adapter was actually invoked
    assert adapter.calls == 1
    # TaskResult reflects adapter output
    assert result.status == TaskStatus.COMPLETED
    assert result.adapter_id == "adapter-pytest-unit"
    assert result.findings["tests"] == 2
    assert result.evidence == ["junit-report://local/junit.json"]
    # Events emitted correspond to the TaskResult
    buf.seek(0)
    events = [json.loads(line) for line in buf.read().splitlines() if line.strip()]
    task_result_event = [e for e in events if e["type"] == "task.result"][0]
    assert task_result_event["status"] == result.status.value
    assert task_result_event["adapter_id"] == result.adapter_id
    # Evidence events match the TaskResult's evidence list
    evidence_events = [
        e["evidence_ref"] for e in events if e["type"] == "task.evidence"
    ]
    assert evidence_events == result.evidence
