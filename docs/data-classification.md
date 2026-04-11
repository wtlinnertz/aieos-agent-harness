# Data Classification

## §1 Purpose

Classify all data flowing through agent context windows per AI SDLC Governance Foundation agent-security gate 9. This document identifies what data enters AI provider context, how it is retained, and what responsibilities fall on consuming projects.

## §2 Data Sources

| Data Source | Classification | Description | Retention |
|-------------|---------------|-------------|-----------|
| Spec content | Internal | AIEOS governance specs (public repo) | None — read at invocation |
| Template content | Internal | AIEOS artifact templates (public repo) | None — read at invocation |
| Prompt content | Internal | AIEOS generation/validation prompts (public repo) | None — read at invocation |
| Upstream frozen artifacts | Internal/Confidential | Initiative-specific content (may contain business logic, architecture decisions) | None — read at invocation |
| Correction constraints | Internal | Blocking issues from validation (derived from initiative content) | None — per-invocation |
| Agent metadata | Internal | Initiative name, artifact ID, human author | Persisted in JSONL logs |

## §3 Agent Context Windows

All data sent to AI providers is transient (per-invocation). The harness does not maintain persistent context between invocations. Each `invoke()` call constructs a fresh context from governance files and initiative artifacts.

Provider data retention is governed by the provider's terms of service, not the harness. The harness has no mechanism to enforce deletion on provider infrastructure.

## §4 Sensitive Data Handling

The harness does **NOT** strip, redact, or classify content automatically. If initiative artifacts contain PII, PHI, credentials, or other restricted data, the consuming project is responsible for:

1. **Classifying the data** before invoking the harness
2. **Configuring appropriate providers** with data processing agreements
3. **Excluding restricted data** from agent context (e.g., by redacting artifacts before invocation or selecting providers with appropriate DPAs)

The harness treats all input content as opaque. It does not inspect, filter, or transform artifact content.

## §5 Observability Data

JSONL logs contain invocation metadata only:

- Provider name and model identifier
- Token counts (input, output)
- Cost estimate
- Latency
- Artifact ID and lifecycle event
- Routing strategy used

Logs do **NOT** contain:

- Artifact content
- Spec, template, or prompt content
- Provider responses or generated text
- API keys or credentials

No PII appears in logs unless the artifact ID itself contains PII (unlikely per AIEOS naming convention: `{TYPE}-{INITIATIVE}-{NNN}`).

## §6 Regulatory Applicability

No GDPR/CCPA/HIPAA data processing by default. The harness processes governance Markdown and initiative artifacts with no inherent regulated data.

If a consuming project handles regulated data, they must independently:

1. **Configure provider DPAs** — ensure the selected AI provider has a signed data processing agreement covering the data classification
2. **Ensure data minimization** in agent context — only include necessary content in artifacts sent to providers
3. **Document retention policies** — account for both harness-side retention (JSONL metadata only) and provider-side retention (per provider ToS/DPA)
