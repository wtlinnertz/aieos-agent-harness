"""Shared fixtures for CI/CD capability-substrate tests (M2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.cicd.artifact_store import FilesystemArtifactStore
from src.cicd.models import HealthStatus, RegistryEntry


@pytest.fixture
def artifact_store(tmp_path: Path) -> FilesystemArtifactStore:
    """Ephemeral, per-test filesystem artifact store."""
    return FilesystemArtifactStore(tmp_path / "artifact-store")


@pytest.fixture
def sample_entry() -> RegistryEntry:
    """A plausible registry entry for an adapter claiming test.unit."""
    return RegistryEntry(
        adapter_id="adapter-pytest-unit",
        adapter_version="1.0.0",
        capabilities=["test.unit"],
        contract_versions={"test.unit": "1.0.0"},
        attestation_ref="s3://attestations/adapter-pytest-unit/1.0.0.sigstore.json",
        registered_at=datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC),
        context={"environment": "ci"},
        health_status=HealthStatus.HEALTHY,
    )


@pytest.fixture
def multi_capability_entry() -> RegistryEntry:
    """Adapter claiming two contracts (e.g., adapter-cosign-sign)."""
    return RegistryEntry(
        adapter_id="adapter-cosign-sign",
        adapter_version="1.0.0",
        capabilities=["sign.artifact", "sign.attestation"],
        contract_versions={"sign.artifact": "1.0.0", "sign.attestation": "1.0.0"},
        attestation_ref="s3://attestations/adapter-cosign-sign/1.0.0.sigstore.json",
        registered_at=datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC),
        context={"environment": "ci"},
        health_status=HealthStatus.HEALTHY,
    )
