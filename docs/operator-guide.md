# Operator Guide — aieos-agent-harness

Audience: operators deploying and maintaining the AIEOS agent harness.

The harness is the runtime registry for AIEOS capability adapters. It
persists adapter registrations, verifies their conformance attestations,
and exposes a lookup API the pipeline runner's resolver calls during spec
compilation. this covers deployment, registration workflows,
contract-version cutover response, and incident diagnosis.

For companion operator concerns on the pipeline runner, see
[aieos-pipeline-runner/docs/operator-guide.md](https://github.com/wtlinnertz/aieos-pipeline-runner/blob/main/docs/operator-guide.md).

---

## Deployment

### Single-process vs multi-process

The harness registry holds state in an in-memory index keyed by action,
with write-through persistence to a `FilesystemArtifactStore` or an
equivalent backend. A single harness process is fine for small
deployments (dev, one-team staging). Multi-process needs a shared
artifact store with read-through invalidation — the v1 filesystem
implementation doesn't synchronize across processes, so either pin to
one process or wire a shared backend (Redis, S3, or the
aieos-artifact-store backend) before scaling.

### Filesystem artifact store

The simplest deployment:

```python
from pathlib import Path
from src.cicd.artifact_store import FilesystemArtifactStore
from src.cicd.registry import CapabilityRegistry
from src.cicd.attestation import AttestationVerifier, ContractRegistration

store = FilesystemArtifactStore(Path("/var/lib/aieos/harness-registry"))
verifier = AttestationVerifier(
    trusted_identities={
        "https://github.com/wtlinnertz/adapter-pytest-unit/.github/workflows/ci.yml@refs/heads/main",
        # ... one entry per adapter repo's CI identity
    },
    contract_registrations={
        "test.unit": ContractRegistration(current_version="1.0.0"),
        # ... one entry per contract this harness will accept
    },
)
registry = CapabilityRegistry(store=store, attestation_verifier=verifier)
```

The constructor scans the artifact-store prefix on startup and rebuilds
the in-memory index, so restarts preserve every prior registration.

### Configuration surface

Three knobs matter for deployment:

1. **`trusted_identities`** — the exact OIDC subjects your CI pipelines
   use when signing attestations. A wildcard match is not supported by
   design; list every adapter's CI identity explicitly. Unknown
   identities are refused at registration time.
2. **`contract_registrations`** — per-action version policy. Each entry
   declares `current_version` and an optional map of prior versions with
   cutover dates.
3. **`attestation_fetcher`** — how the harness resolves
   `attestation_ref` to bytes. Default resolves `file://` URIs; in
   production, wire it to fetch from your artifact store or a signed
   release asset.

---

## Adapter registration workflow

The harness has no self-service API for adapter registration. New
adapters and new versions go through this process:

1. The adapter's CI publishes a signed conformance attestation (Sigstore
   bundle wrapping the conformance-attestation payload).
2. An operator verifies the attestation out of band: the signing
   identity is in the trusted set, the bundle's signature is valid, the
   payload validates against
   `schema/conformance-attestation.schema.json`, and
   `predicate.contract_id` + `contract_version` match what the operator
   expects.
3. Operator invokes `registry.register_adapter(entry)` with a
   `RegistryEntry` built from the attestation's subject and predicate.
4. The registry persists the entry to the artifact store and adds it to
   the in-memory index under every action in `capabilities`.

The operator is the governance checkpoint. The harness enforces the
structural invariants (schema, signing identity, contract version); the
operator enforces the policy invariants (right adapter at the right time
for the right environment).

### Script template

A one-shot registration script for a single adapter:

```python
from datetime import datetime, UTC
from pathlib import Path
from src.cicd.models import RegistryEntry, HealthStatus

entry = RegistryEntry(
    adapter_id="adapter-pytest-unit",
    adapter_version="1.0.0",
    capabilities=["test.unit"],
    contract_versions={"test.unit": "1.0.0"},
    attestation_ref="file:///path/to/downloaded/attestation.sigstore.json",
    registered_at=datetime.now(UTC),
    context={"environment": "ci"},
    health_status=HealthStatus.HEALTHY,
)
outcome = registry.register_adapter(entry)
if not outcome.accepted:
    print(f"REJECTED: {outcome.diagnostic}")
    sys.exit(1)
print(f"registered {entry.adapter_id}@{entry.adapter_version}")
```

Run once per adapter per target harness. For multi-capability adapters
(cosign-sign), run twice with different `capabilities` and
`attestation_ref` values.

---

## Contract-version cutovers

When a contract advances (say, `test.unit` moves from 1.0.0 to 1.1.0 in
governance-foundation), the harness needs to accept both versions for a
grace period while adapter maintainers re-run conformance against the
new contract. The sequence:

1. **Pre-cutover** — both versions accepted. Add the new version to the
   `contract_registrations` policy with the old version listed as a
   prior with a cutover date:
   ```python
   "test.unit": ContractRegistration(
       current_version="1.1.0",
       prior_versions_cutover={
           "1.0.0": datetime(2026, 7, 1, tzinfo=UTC),
       },
   )
   ```
2. **Announce the cutover.** Publish the date to every adapter owner so
   they re-run conformance and produce a fresh attestation.
3. **At cutover** — existing 1.0.0 registrations stay in the registry
   but the harness refuses new 1.0.0 registrations.
4. **Post-cutover** — prune stale 1.0.0 entries from the registry at a
   time that won't disrupt running pipelines.

The grace semantics are enforced by
`ContractRegistration.is_version_acceptable(version, now)`. Before the
cutover instant, the prior version is accepted with a diagnostic noting
the cutover date. At or after the instant, it is refused with a
diagnostic naming the cutover date that has passed.

Unit tests for this logic live at
`tests/cicd/test_attestation.py::test_registration_accepts_prior_version_before_cutover`
and the companion `_rejects_*_after_cutover` + `_at_cutover_boundary`
cases.

---

## Incident diagnosis

### Symptom: pipeline runner reports `no_adapter` for an action

The harness's registry lookup returned an empty list. Causes, in
likelihood order:

1. Adapter was never registered on this harness. Check with
   `registry.all_entries()` or by inspecting the artifact store's
   `registry/<adapter_id>/` prefix.
2. Adapter is registered but the context filter excluded it. The
   resolver passes a context map to `find_adapters`; entries whose
   `context` doesn't match as a subset of the query are filtered out.
   Check both sides.
3. Adapter is registered but under a different adapter_id than the spec
   expects. Spec's `adapter_preferences` must match `adapter_id`
   exactly.

### Symptom: pipeline runner reports `ambiguous` for an action

More than one adapter satisfies the action in the requested context. The
resolver does not pick by registration order — it refuses. Causes:

1. Two adapter versions are registered and both are in-context. Add an
   explicit version preference in the spec, or unregister the stale
   version.
2. Two different adapters claim the same action (two SAST adapters, for
   instance). Policy decision — pick one in the spec's
   `adapter_preferences`.

### Symptom: registration refused with schema-validation diagnostic

The attestation payload failed to validate against
`schema/conformance-attestation.schema.json`. The diagnostic names the
offending path. Most common:

- `predicate.contract_id` doesn't match the frozen taxonomy pattern
- `contract_version` isn't full semver
- `suite_run_id` is shorter than 16 characters
- `timestamp` isn't ISO 8601 with a Z or offset suffix

Re-run the conformance harness in the adapter's CI to produce a
well-formed payload, then re-register.

### Symptom: registration refused with "signing_identity ... not in the
trusted set"

The adapter's CI identity hasn't been added to the harness's
`trusted_identities`. Either add it (after verifying the identity is the
legitimate CI workflow) or reject the registration if the identity is
unexpected.

### Symptom: in-memory index out of sync with artifact store

Restart the harness process. Construction rebuilds the index from the
store. If inconsistency persists after restart, the artifact store
contains a malformed entry — check the store for corrupt JSON under
`registry/<adapter_id>/`.

---

## Health and observability

The harness emits structured events via `structlog` when adapters register
or are refused. Ship these to your log aggregator:

- `registration_accepted` — `adapter_id`, `adapter_version`,
  `capabilities`
- `registration_rejected` — `adapter_id`, `adapter_version`, `reason`
- `attestation_verified` — fields matching the attestation payload's
  subject + predicate

Alert on a sustained `registration_rejected` rate spike; it indicates
either a bad CI run or an attempted registration bypass.

---

## Related

- [adapter-author-guide.md](https://github.com/wtlinnertz/aieos-governance-foundation/blob/main/docs/adapter-author-guide.md)
  — how new adapters produce the attestations this harness verifies
- [spec-authoring-guide.md](https://github.com/wtlinnertz/aieos-governance-foundation/blob/main/docs/spec-authoring-guide.md)
  — how developers use registered adapters in their CI/CD specs
- [aieos-pipeline-runner operator guide](https://github.com/wtlinnertz/aieos-pipeline-runner/blob/main/docs/operator-guide.md)
  — operator concerns on the runner side
