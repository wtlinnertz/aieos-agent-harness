"""M2.2 — attestation verification at registration.

Exercises the six scenarios from the prompt plus a few targeted additions:
  - missing attestation_ref
  - fetch failure (unresolvable ref)
  - malformed payload
  - schema-violating payload
  - subject-mismatch (attestation claims a different adapter)
  - wrong contract_id for the claimed capability
  - cutover grace: before, at, and after
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.cicd.attestation import AttestationVerifier, ContractRegistration
from src.cicd.models import RegistryEntry


TRUSTED_IDENTITY = (
    "https://github.com/wtlinnertz/adapter-pytest-unit/.github/workflows/"
    "adapter-ci.yml@refs/heads/main"
)


def _entry(
    *,
    adapter_id: str = "adapter-pytest-unit",
    adapter_version: str = "1.0.0",
    capabilities: list[str] | None = None,
    attestation_ref: str = "mem://att-1",
) -> RegistryEntry:
    return RegistryEntry(
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        capabilities=capabilities or ["test.unit"],
        contract_versions={"test.unit": "1.0.0"},
        attestation_ref=attestation_ref,
        registered_at=datetime(2026, 4, 17, tzinfo=UTC),
    )


def _payload(
    *,
    adapter_id: str = "adapter-pytest-unit",
    adapter_version: str = "1.0.0",
    contract_id: str = "test.unit",
    contract_version: str = "1.0.0",
    result: str = "pass",
    signing_identity: str = TRUSTED_IDENTITY,
    suite_run_id: str = "550e8400-e29b-41d4-a716-446655440000",
    timestamp: str = "2026-04-17T18:30:00Z",
) -> bytes:
    return json.dumps(
        {
            "subject": {"adapter_id": adapter_id, "adapter_version": adapter_version},
            "predicate": {"contract_id": contract_id, "contract_version": contract_version},
            "suite_run_id": suite_run_id,
            "result": result,
            "signing_identity": signing_identity,
            "timestamp": timestamp,
        }
    ).encode("utf-8")


class MemFetcher:
    """In-memory attestation fetcher for tests."""

    def __init__(self, store: dict[str, bytes] | None = None) -> None:
        self._store = store or {}

    def __call__(self, ref: str) -> bytes:
        if ref not in self._store:
            raise FileNotFoundError(f"no attestation at {ref}")
        return self._store[ref]


def _make_verifier(
    *,
    contract_version: str = "1.0.0",
    prior: dict[str, datetime] | None = None,
    now: datetime | None = None,
    fetcher: MemFetcher | None = None,
    trusted: set[str] | None = None,
) -> AttestationVerifier:
    return AttestationVerifier(
        trusted_identities=trusted or {TRUSTED_IDENTITY},
        contract_registrations={
            "test.unit": ContractRegistration(
                current_version=contract_version,
                prior_versions_cutover=prior or {},
            )
        },
        now=(lambda: now) if now is not None else None,
        attestation_fetcher=fetcher or MemFetcher(),
    )


# -------------------------------------------------------------- basic rejects

def test_registration_rejects_missing_attestation():
    verifier = _make_verifier()
    entry = _entry(attestation_ref="")

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "lacks attestation_ref" in outcome.diagnostic


def test_registration_rejects_unresolvable_attestation():
    verifier = _make_verifier(fetcher=MemFetcher({}))  # no entries
    entry = _entry(attestation_ref="mem://not-found")

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "not found" in outcome.diagnostic.lower()


def test_registration_rejects_malformed_payload():
    fetcher = MemFetcher({"mem://bad": b"this is not json"})
    verifier = _make_verifier(fetcher=fetcher)
    entry = _entry(attestation_ref="mem://bad")

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "valid JSON" in outcome.diagnostic


def test_registration_rejects_schema_violation():
    """Predicate with result='fail' violates the const 'pass' constraint."""
    bad = _payload(result="fail")
    fetcher = MemFetcher({"mem://bad-schema": bad})
    verifier = _make_verifier(fetcher=fetcher)
    entry = _entry(attestation_ref="mem://bad-schema")

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "schema validation" in outcome.diagnostic


def test_registration_rejects_subject_mismatch():
    """Attestation claims a different adapter than the registration."""
    payload = _payload(adapter_id="adapter-other")
    fetcher = MemFetcher({"mem://mismatch": payload})
    verifier = _make_verifier(fetcher=fetcher)
    entry = _entry(attestation_ref="mem://mismatch")

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "subject adapter_id" in outcome.diagnostic


def test_registration_rejects_contract_id_not_in_capabilities():
    """Attestation is for sign.artifact but the registration claims test.unit."""
    payload = _payload(contract_id="sign.artifact")
    fetcher = MemFetcher({"mem://wrong-action": payload})
    verifier = _make_verifier(fetcher=fetcher)
    entry = _entry(attestation_ref="mem://wrong-action")

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "not among" in outcome.diagnostic


def test_registration_rejects_invalid_signature():
    """'Invalid signature' in v1 is modeled as an untrusted signing identity."""
    payload = _payload(signing_identity="https://evil.example/workflows/steal.yml@refs/heads/main")
    fetcher = MemFetcher({"mem://untrusted": payload})
    verifier = _make_verifier(fetcher=fetcher)
    entry = _entry(attestation_ref="mem://untrusted")

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "trusted set" in outcome.diagnostic


# ------------------------------------------------------- version / cutover

def test_registration_accepts_current_version():
    payload = _payload(contract_version="1.0.0")
    fetcher = MemFetcher({"mem://current": payload})
    verifier = _make_verifier(contract_version="1.0.0", fetcher=fetcher)
    entry = _entry(attestation_ref="mem://current")

    outcome = verifier(entry)

    assert outcome.accepted is True
    assert "current contract version" in outcome.diagnostic


def test_registration_rejects_wrong_contract_version():
    """Version is neither current nor a known prior."""
    payload = _payload(contract_version="0.9.0")
    fetcher = MemFetcher({"mem://wrong": payload})
    verifier = _make_verifier(contract_version="1.0.0", fetcher=fetcher)
    entry = _entry(attestation_ref="mem://wrong")

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "not current" in outcome.diagnostic


def test_registration_accepts_prior_version_before_cutover():
    cutover = datetime(2026, 6, 1, tzinfo=UTC)
    payload = _payload(contract_version="1.0.0")
    fetcher = MemFetcher({"mem://prior": payload})
    verifier = _make_verifier(
        contract_version="1.1.0",
        prior={"1.0.0": cutover},
        now=datetime(2026, 5, 1, tzinfo=UTC),
        fetcher=fetcher,
    )
    entry = _entry(attestation_ref="mem://prior")

    outcome = verifier(entry)

    assert outcome.accepted is True
    assert "before cutover" in outcome.diagnostic.lower() or "until cutover" in outcome.diagnostic.lower()


def test_registration_rejects_prior_version_after_cutover():
    cutover = datetime(2026, 6, 1, tzinfo=UTC)
    payload = _payload(contract_version="1.0.0")
    fetcher = MemFetcher({"mem://prior": payload})
    verifier = _make_verifier(
        contract_version="1.1.0",
        prior={"1.0.0": cutover},
        now=datetime(2026, 7, 1, tzinfo=UTC),
        fetcher=fetcher,
    )
    entry = _entry(attestation_ref="mem://prior")

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "cutover" in outcome.diagnostic.lower()
    assert "has passed" in outcome.diagnostic.lower()


def test_registration_rejects_prior_version_exactly_at_cutover():
    """At-or-after the cutover instant, the prior version is no longer valid."""
    cutover = datetime(2026, 6, 1, tzinfo=UTC)
    payload = _payload(contract_version="1.0.0")
    fetcher = MemFetcher({"mem://prior": payload})
    verifier = _make_verifier(
        contract_version="1.1.0",
        prior={"1.0.0": cutover},
        now=cutover,  # exactly at the cutover instant
        fetcher=fetcher,
    )
    entry = _entry(attestation_ref="mem://prior")

    outcome = verifier(entry)

    assert outcome.accepted is False


def test_registration_rejects_contract_without_registered_policy():
    """Attestation is for an action the harness has no policy for."""
    entry = _entry(capabilities=["test.contract"])
    payload = _payload(contract_id="test.contract")
    fetcher = MemFetcher({"mem://1": payload})
    # verifier only registered test.unit
    verifier = _make_verifier(fetcher=fetcher)
    entry = RegistryEntry(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        capabilities=["test.contract"],
        contract_versions={"test.contract": "1.0.0"},
        attestation_ref="mem://1",
        registered_at=datetime(2026, 4, 17, tzinfo=UTC),
    )

    outcome = verifier(entry)

    assert outcome.accepted is False
    assert "no registered version policy" in outcome.diagnostic


# ----------------------------------------- end-to-end against registry wiring

def test_registry_uses_real_verifier_and_accepts_current(artifact_store, tmp_path):
    """End-to-end: CapabilityRegistry + AttestationVerifier, current version path."""
    from src.cicd.registry import CapabilityRegistry

    payload = _payload()
    att_path = tmp_path / "att.json"
    att_path.write_bytes(payload)
    entry = _entry(attestation_ref=f"file://{att_path}")
    verifier = AttestationVerifier(
        trusted_identities={TRUSTED_IDENTITY},
        contract_registrations={
            "test.unit": ContractRegistration(current_version="1.0.0")
        },
    )
    registry = CapabilityRegistry(store=artifact_store, attestation_verifier=verifier)

    outcome = registry.register_adapter(entry)

    assert outcome.accepted is True
    found = registry.find_adapters("test.unit")
    assert len(found) == 1
    assert found[0].adapter_id == entry.adapter_id
