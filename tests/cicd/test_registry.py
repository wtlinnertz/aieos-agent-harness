"""M2.1 — capability registry tests.

Covers the five tests called out in the prompt plus a couple targeted
additions for multi-capability adapters and the re-registration update path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.cicd.models import HealthStatus, RegistrationOutcome, RegistryEntry
from src.cicd.registry import CapabilityRegistry


def _always_reject(_entry: RegistryEntry) -> RegistrationOutcome:
    return RegistrationOutcome(accepted=False, diagnostic="rejected for test")


def test_register_adapter_stores_and_retrieves(artifact_store, sample_entry):
    registry = CapabilityRegistry(store=artifact_store)

    outcome = registry.register_adapter(sample_entry)

    assert outcome.accepted is True
    found = registry.find_adapters("test.unit")
    assert len(found) == 1
    assert found[0].adapter_id == sample_entry.adapter_id


def test_find_adapters_returns_empty_for_unknown_action(artifact_store, sample_entry):
    registry = CapabilityRegistry(store=artifact_store)
    registry.register_adapter(sample_entry)

    found = registry.find_adapters("security.dast")

    assert found == []


def test_find_adapters_filters_by_context(artifact_store):
    entry_ci = RegistryEntry(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        capabilities=["test.unit"],
        contract_versions={"test.unit": "1.0.0"},
        attestation_ref="ref-ci",
        registered_at=datetime(2026, 4, 17, tzinfo=UTC),
        context={"environment": "ci"},
    )
    entry_prod = RegistryEntry(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.1.0",
        capabilities=["test.unit"],
        contract_versions={"test.unit": "1.0.0"},
        attestation_ref="ref-prod",
        registered_at=datetime(2026, 4, 17, tzinfo=UTC),
        context={"environment": "prod"},
    )
    registry = CapabilityRegistry(store=artifact_store)
    registry.register_adapter(entry_ci)
    registry.register_adapter(entry_prod)

    found_ci = registry.find_adapters("test.unit", context={"environment": "ci"})
    found_prod = registry.find_adapters("test.unit", context={"environment": "prod"})

    assert [e.attestation_ref for e in found_ci] == ["ref-ci"]
    assert [e.attestation_ref for e in found_prod] == ["ref-prod"]


def test_registry_persists_to_artifact_store(artifact_store, sample_entry):
    """Write-through: the entry is present in the store immediately after register."""
    registry = CapabilityRegistry(store=artifact_store)

    registry.register_adapter(sample_entry)

    key = f"registry/{sample_entry.adapter_id}/{sample_entry.adapter_version}.json"
    assert artifact_store.get(key) is not None


def test_registry_index_rebuilds_from_artifact_store(artifact_store, sample_entry):
    """A fresh registry over the same store finds previously-written entries."""
    first = CapabilityRegistry(store=artifact_store)
    first.register_adapter(sample_entry)

    second = CapabilityRegistry(store=artifact_store)

    rebuilt = second.find_adapters("test.unit")
    assert len(rebuilt) == 1
    assert rebuilt[0].adapter_id == sample_entry.adapter_id
    assert rebuilt[0].contract_versions == sample_entry.contract_versions


def test_registration_rejected_never_persists(artifact_store, sample_entry):
    """Stubbing the verifier to reject keeps the store and index empty."""
    registry = CapabilityRegistry(
        store=artifact_store, attestation_verifier=_always_reject
    )

    outcome = registry.register_adapter(sample_entry)

    assert outcome.accepted is False
    assert outcome.diagnostic == "rejected for test"
    assert registry.find_adapters("test.unit") == []
    key = f"registry/{sample_entry.adapter_id}/{sample_entry.adapter_version}.json"
    assert artifact_store.get(key) is None


def test_multi_capability_adapter_is_findable_under_every_action(
    artifact_store, multi_capability_entry
):
    registry = CapabilityRegistry(store=artifact_store)

    registry.register_adapter(multi_capability_entry)

    assert len(registry.find_adapters("sign.artifact")) == 1
    assert len(registry.find_adapters("sign.attestation")) == 1
    assert len(registry.all_entries()) == 1  # one physical adapter


def test_reregistration_updates_existing_entry(artifact_store):
    """Re-registering the same adapter_id + version replaces, doesn't duplicate."""
    base = RegistryEntry(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        capabilities=["test.unit"],
        contract_versions={"test.unit": "1.0.0"},
        attestation_ref="first",
        registered_at=datetime(2026, 4, 17, tzinfo=UTC),
    )
    updated = RegistryEntry(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        capabilities=["test.unit"],
        contract_versions={"test.unit": "1.0.0"},
        attestation_ref="second",
        registered_at=datetime(2026, 4, 18, tzinfo=UTC),
        health_status=HealthStatus.DEGRADED,
    )
    registry = CapabilityRegistry(store=artifact_store)
    registry.register_adapter(base)
    registry.register_adapter(updated)

    found = registry.find_adapters("test.unit")
    assert len(found) == 1
    assert found[0].attestation_ref == "second"
    assert found[0].health_status == HealthStatus.DEGRADED
