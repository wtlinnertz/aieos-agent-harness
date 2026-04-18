# Vendored Schemas

Schemas here are vendored copies of frozen artifacts from
`aieos-governance-foundation` at their v1.0 tag. They are used at runtime by
the harness for attestation validation and must be updated only when a
formally-cutover version bump happens upstream.

| File | Upstream | Frozen at |
|---|---|---|
| `conformance-attestation.schema.json` | `aieos-governance-foundation/schema/conformance-attestation.schema.json` | `v1.0-conformance-attestation-schema` |

Do not edit these files directly. Update by re-vendoring from the upstream
tag, noting the new tag here, and running the full test suite.
