# Threat Assessment

## §1 Scope

The harness orchestrates AI provider calls (Anthropic, OpenAI) and deterministic tools (SAST, linters) for AIEOS artifact generation and validation. It handles:

- **Governance file content** — specs, templates, prompts (public, version-controlled)
- **Initiative artifacts** — PRDs, SADs, TDDs (may contain business-sensitive content)
- **Provider API keys** — via environment variables only
- **Invocation metadata** — cost, tokens, latency (persisted in JSONL logs)

## §2 Input Surface Threats

| Threat | Severity | Existing Mitigation | Residual Risk |
|--------|----------|---------------------|---------------|
| Prompt injection via malformed spec/template content | High | Specs are governance-controlled, versioned in git. Changes require explicit commit. | Compromised git repo could inject malicious content into governance files. |
| Goal hijacking via crafted upstream artifacts | High | Freeze-before-promote ensures upstream is validated before downstream consumption. | A validator PASS on poisoned content would propagate downstream. |
| Context poisoning via artifact store queries | Medium | Artifact store is optional, read-only. Harness does not write to store. | Poisoned store returns misleading context that influences generation. |
| Malformed harness.yaml configuration | Medium | Config validated at load time. Schema enforced before any invocation. | Misconfigured routing could send data to unintended provider. |

## §3 Processing Surface Threats

| Threat | Severity | Existing Mitigation | Residual Risk |
|--------|----------|---------------------|---------------|
| Model hallucination in validation (false PASS) | Critical | Standardized JSON output format. Validator output parsing rejects non-conforming responses. | Well-formed JSON with incorrect judgment (false PASS or false FAIL). |
| Convergence loop manipulation | Medium | Bounded to 3 iterations. Staleness and oscillation detection halt loops. | Crafted FAIL responses could steer correction direction. |
| Provider API key exposure | High | Keys from env vars only, never in YAML or logs. | Process memory dump could expose keys. |
| Provider data retention | Medium | Tool-agnostic policy means no provider lock-in. Provider choice is configurable. | Providers may retain input data per their own data processing policies. |

## §4 Output Surface Threats

| Threat | Severity | Existing Mitigation | Residual Risk |
|--------|----------|---------------------|---------------|
| Generated artifacts containing injected content | High | Separate validation session catches quality and structural issues. | Subtle injection that passes validation (semantically valid but misleading). |
| Observability log tampering | Low | JSONL is append-only by convention. Logs contain metadata only, not content. | No cryptographic integrity protection on log files. |
| ER state manipulation | Medium | State writes follow structured format. Harness never auto-freezes artifacts. | Corrupted state block could mislead next session about artifact status. |

## §5 Mitigation Summary

| Mitigation | Covers | Coverage Assessment |
|------------|--------|---------------------|
| Git-versioned governance files | Input injection | Strong — requires repo compromise to bypass |
| Freeze-before-promote invariant | Goal hijacking | Strong — enforced by invariant check before every downstream invocation |
| Config validation at load | Malformed config | Strong — fails fast before any provider call |
| Standardized JSON validator output | False PASS/FAIL | Moderate — catches format issues but not judgment errors |
| Bounded convergence (max 3) | Loop manipulation | Strong — hard limit prevents unbounded steering |
| Env-var-only API keys | Key exposure | Moderate — protects against config/log leaks but not memory attacks |
| Separate generation/validation sessions | Content injection | Moderate — independent judgment but same model limitations |
| Append-only JSONL logs | Log tampering | Weak — convention only, no cryptographic enforcement |
| Human freeze decision | State manipulation | Strong — human reviews before any promotion |

## §6 Recommendations

Prioritized by risk reduction:

1. **Add content hash verification for governance files before invocation** — detect tampering between git checkout and harness execution.
2. **Consider provider data processing agreements for sensitive initiatives** — especially when initiative artifacts contain business-critical architecture or strategy.
3. **Add JSONL log signing for tamper detection in compliance scenarios** — HMAC or similar integrity check for audit trails.
4. **Monitor for convergence loop steering patterns** — log correction deltas between iterations to detect manipulation attempts.
