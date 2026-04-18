"""Attestation verification for capability registrations.

Gate on register_adapter: parses the attestation payload, validates it
against the frozen conformance-attestation schema, checks the signing
identity against a trusted set, and checks the contract version against a
current-plus-grace policy.

v1 scope: payload-level verification. Crypto unwrapping of the Sigstore
bundle is deferred — the attestation_fetcher is expected to return the
unwrapped conformance-attestation payload JSON. Adapter CI pipelines produce
bundle-wrapped attestations at sign time; the harness operator is
responsible for plugging in the bundle-unwrapping fetcher in production.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from typing import Any

import structlog
from jsonschema import Draft202012Validator

from .models import RegistrationOutcome, RegistryEntry

log = structlog.get_logger(__name__)

AttestationFetcher = Callable[[str], bytes]


def _load_vendored_schema() -> dict[str, Any]:
    schema_bytes = (
        resources.files(__package__)
        .joinpath("schemas")
        .joinpath("conformance-attestation.schema.json")
        .read_bytes()
    )
    return json.loads(schema_bytes)


@dataclass(frozen=True)
class ContractRegistration:
    """Policy for one capability contract: which versions may register now.

    current_version is mandatory. prior_versions_cutover maps older versions
    to the datetime at which they stop being accepted. Before the cutover
    instant, the prior version is still valid; at-or-after, it is rejected.
    """

    current_version: str
    prior_versions_cutover: dict[str, datetime] = field(default_factory=dict)

    def is_version_acceptable(self, version: str, now: datetime) -> tuple[bool, str]:
        if version == self.current_version:
            return True, f"current contract version ({version})"
        cutover = self.prior_versions_cutover.get(version)
        if cutover is None:
            return (
                False,
                f"contract version {version!r} is not current ({self.current_version}) "
                "and not a declared prior version",
            )
        if now < cutover:
            return True, f"prior contract version {version} accepted until cutover at {cutover.isoformat()}"
        return False, f"prior contract version {version} rejected — cutover at {cutover.isoformat()} has passed"


class AttestationVerifier:
    """Pluggable verifier used by CapabilityRegistry.register_adapter.

    Construction injects the trusted-identity set, the per-contract version
    policy, a time source (for deterministic tests), and an attestation
    fetcher (ref -> bytes). The fetcher's default is a file:// scheme
    resolver; tests can substitute any callable.
    """

    def __init__(
        self,
        trusted_identities: Iterable[str],
        contract_registrations: dict[str, ContractRegistration],
        now: Callable[[], datetime] | None = None,
        attestation_fetcher: AttestationFetcher | None = None,
        schema: dict[str, Any] | None = None,
    ) -> None:
        self._trusted = set(trusted_identities)
        self._contracts = dict(contract_registrations)
        self._now = now or (lambda: datetime.now(UTC))
        self._fetch = attestation_fetcher or _file_uri_fetcher
        self._validator = Draft202012Validator(schema or _load_vendored_schema())

    def __call__(self, entry: RegistryEntry) -> RegistrationOutcome:
        if not entry.attestation_ref:
            return RegistrationOutcome(
                accepted=False, diagnostic="registration lacks attestation_ref"
            )

        try:
            raw = self._fetch(entry.attestation_ref)
        except FileNotFoundError as exc:
            return RegistrationOutcome(
                accepted=False,
                diagnostic=f"attestation not found at {entry.attestation_ref!r}: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - fetcher errors are diagnostic
            return RegistrationOutcome(
                accepted=False,
                diagnostic=f"attestation fetch failed for {entry.attestation_ref!r}: {exc}",
            )

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return RegistrationOutcome(
                accepted=False, diagnostic=f"attestation payload is not valid JSON: {exc}"
            )

        schema_errors = sorted(self._validator.iter_errors(payload), key=lambda e: list(e.path))
        if schema_errors:
            msg = schema_errors[0].message
            path = "/".join(str(p) for p in schema_errors[0].path) or "<root>"
            return RegistrationOutcome(
                accepted=False,
                diagnostic=f"attestation fails schema validation at {path}: {msg}",
            )

        # Subject must match the registering adapter.
        subject = payload["subject"]
        if subject["adapter_id"] != entry.adapter_id:
            return RegistrationOutcome(
                accepted=False,
                diagnostic=(
                    f"attestation subject adapter_id {subject['adapter_id']!r} "
                    f"does not match registration {entry.adapter_id!r}"
                ),
            )
        if subject["adapter_version"] != entry.adapter_version:
            return RegistrationOutcome(
                accepted=False,
                diagnostic=(
                    f"attestation subject adapter_version {subject['adapter_version']!r} "
                    f"does not match registration {entry.adapter_version!r}"
                ),
            )

        # Predicate's contract_id must be one of the claimed capabilities.
        predicate = payload["predicate"]
        attested_action = predicate["contract_id"]
        if attested_action not in entry.capabilities:
            return RegistrationOutcome(
                accepted=False,
                diagnostic=(
                    f"attestation contract_id {attested_action!r} is not among "
                    f"registration capabilities {entry.capabilities!r}"
                ),
            )

        # Signing identity must be trusted.
        signer = payload["signing_identity"]
        if signer not in self._trusted:
            return RegistrationOutcome(
                accepted=False,
                diagnostic=f"signing_identity {signer!r} is not in the trusted set",
            )

        # Contract version must be current or prior-within-grace.
        registration = self._contracts.get(attested_action)
        if registration is None:
            return RegistrationOutcome(
                accepted=False,
                diagnostic=(
                    f"contract {attested_action!r} has no registered version policy "
                    "on this harness"
                ),
            )
        attested_version = predicate["contract_version"]
        ok, reason = registration.is_version_acceptable(attested_version, self._now())
        if not ok:
            return RegistrationOutcome(accepted=False, diagnostic=reason)

        log.info(
            "attestation_verified",
            adapter_id=entry.adapter_id,
            adapter_version=entry.adapter_version,
            contract_id=attested_action,
            contract_version=attested_version,
            signing_identity=signer,
            version_status=reason,
        )
        return RegistrationOutcome(accepted=True, diagnostic=reason)


def _file_uri_fetcher(ref: str) -> bytes:
    """Default fetcher: resolves file:// URIs. Anything else raises."""
    if not ref.startswith("file://"):
        raise ValueError(
            f"default fetcher only resolves file:// references; got {ref!r}. "
            "Inject a custom attestation_fetcher for other schemes."
        )
    path = ref[len("file://") :]
    with open(path, "rb") as f:
        return f.read()
