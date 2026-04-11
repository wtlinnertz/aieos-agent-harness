# PRD: AIEOS Agent Harness

## 0. Document Control

- Product / Initiative Name: AIEOS Agent Harness (ECO-009)
- PRD ID: PRD-HARNESS-001
- Author: Todd Linnertz (extracted from existing codebase by AI)
- Date: 2026-03-26
- Status: Draft
- Governance Model Version: 1.3
- Prompt Version: prd-prompt v1.0
- Spec Version: prd-spec v1.0
- Principles Version: product-craftsmanship v1.0
- Related Links:
  - KER: KER-HARNESS-001 (docs/sdlc/01-ker.md)
  - Product Brief: docs/sdlc/02-product-brief.md
  - Architecture: docs/architecture.md
  - Ecosystem Roadmap: aieos-governance-foundation/docs/ecosystem-roadmap.md (ECO-009)

**Note:** This PRD is retroactive. The system described below is fully implemented. All requirements use "SHALL" to document what the system does, not to plan future work. All facts are extracted from the actual codebase (16 source files, 166 tests).

---

## 1. Problem Statement

**What fails:** The AIEOS governance framework defines a structured artifact lifecycle -- specs, templates, prompts, and validators organized across 16 layers -- but provides no automation layer to orchestrate AI-assisted artifact production. Each artifact generation and validation cycle requires the operator to manually set up AI sessions, manually locate and load the correct spec/template/prompt/validator files for the artifact type, manually verify that upstream artifacts are frozen before generating downstream artifacts, manually track convergence iterations when validation fails, and manually record cost, latency, and token usage. As the framework grows (16 layers, 30+ artifact types, 5 initiative presets), manual orchestration becomes error-prone and unscalable.

**Who is affected:** Framework operators who run AIEOS artifact lifecycle events. These are technical users who manage initiative progression through the AIEOS pipeline. Without automation, they bear the cognitive load of enforcing 7 structural invariants on every invocation, tracking convergence state across generate-validate cycles, and maintaining cost visibility across multiple AI providers.

**Why now:** The AIEOS framework has grown to 16 layers with 12 kits built, 30+ governed artifact types, and multiple active initiatives (CONSOLE, SEARCH, HARNESS). Manual orchestration was acceptable for a single initiative but does not scale. The ecosystem roadmap (Phase 5) identified the Agent Harness as the integration layer between AIEOS governance and AI providers. The system was designed, built, and tested to address this gap. This PRD documents the existing solution retroactively under AIEOS governance.

---

## 2. Goals (What "Success" Means)

- **G-1: Automated artifact lifecycle orchestration.** The system orchestrates the full generate-validate cycle for any AIEOS artifact type via a single CLI command, eliminating manual session setup. Success: the `lifecycle` command produces a generated artifact and its validation result in one invocation.

- **G-2: Programmatic invariant enforcement.** All 7 AIEOS structural invariants are enforced programmatically on every invocation, removing reliance on operator memory. Success: each invariant has a dedicated check function with test coverage, and violations are detected before the operation proceeds.

- **G-3: Resilient multi-provider routing.** The system routes requests across multiple AI providers with automatic failover, circuit breaking, and cost optimization. Success: 4 routing strategies are implemented (fallback, pipeline, parallel_consensus, cost_aware) with circuit breaker protection.

- **G-4: Bounded convergence with escalation.** Failed validations trigger automatic re-generation with correction constraints, bounded to a configurable maximum (default 3 iterations). Success: convergence loop terminates within bounds and detects staleness and oscillation patterns.

- **G-5: Cost and operational observability.** Every invocation records cost, latency, token usage, and result to a persistent log. Success: per-invocation JSONL metrics with cost summary, provider health summary, and anomaly detection (3x rolling mean threshold).

---

## 3. Non-Goals (Hard Exclusions)

- **NG-1: No graphical user interface.** The system is CLI-only. No web dashboard, desktop application, or visual artifact editor.

- **NG-2: No database backend.** All state is stored on disk as Markdown (ER state blocks, Sherpa Journal) and JSONL (metrics). No SQL, NoSQL, or in-memory database.

- **NG-3: No auto-freeze capability.** The system never automatically promotes an artifact from VALIDATED to FROZEN. The freeze decision is always a human action. The harness presents validation results and stops.

- **NG-4: No governance file modification.** The harness consumes AIEOS governance files (specs, templates, prompts, validators) as read-only inputs. It does not create, modify, or delete governance files.

- **NG-5: No multi-tenant operation.** The system operates for a single operator on a single machine. No concurrent user support, authentication, or authorization.

- **NG-6: No agent memory or context persistence.** Each adapter invocation is stateless. There is no session continuity, conversation history, or cross-invocation context beyond what is explicitly passed in the request.

- **NG-7: No real-time streaming.** Adapter invocations are synchronous request-response. No streaming token output or server-sent events.

---

## 4. Users / Personas

- **Framework Operator**
  - Primary behaviors: Runs artifact lifecycle events via CLI (generate, validate, lifecycle commands). Configures provider bindings and routing strategies via YAML. Reviews generated artifacts and validation results. Makes freeze decisions.
  - Constraints: Must have Python 3.11+ environment. Must have API keys for at least one AI provider set as environment variables. Must have access to AIEOS governance framework directory.

- **Initiative Sponsor**
  - Primary behaviors: Reviews cost reports (costs command). Reviews provider health summaries. Monitors convergence iteration counts and escalation frequency.
  - Constraints: Consumes CLI text output and JSONL logs. Does not configure or operate the harness directly.

---

## 5. Requirements

### 5.1 Functional Requirements

**Lifecycle Binding:**
- FR-1: The system SHALL map lifecycle events (PRE_GENERATION, POST_GENERATION, PRE_VALIDATION, POST_VALIDATION, POST_FREEZE, ON_FAILURE) to adapter invocations via YAML-configured bindings.
- FR-2: The system SHALL resolve event bindings with exact artifact type match taking priority over wildcard ("*") bindings.
- FR-3: The system SHALL raise an error when no binding matches a given event and artifact type combination.

**Routing Engine:**
- FR-4: The system SHALL implement fallback routing that tries adapters in order, skipping those with open circuit breakers, and returns the first successful response.
- FR-5: The system SHALL implement pipeline routing that chains adapters sequentially, feeding each adapter's output as the next adapter's input.
- FR-6: The system SHALL implement parallel consensus routing that fans out to all adapters concurrently via thread pool, checks agreement (content length within 20% of median), and requires agreement fraction at or above a configurable threshold (default 0.67).
- FR-7: The system SHALL implement cost-aware routing that sorts adapters by cost_estimate() ascending, optionally filters by minimum tier, and invokes the cheapest available adapter with circuit breaker protection.
- FR-8: The system SHALL maintain a circuit breaker per provider that opens after a configurable number of consecutive failures (default 3) and auto-resets after a configurable timeout (default 60 seconds).

**Convergence Loop:**
- FR-9: The system SHALL run a bounded generate-validate loop where generation and validation are always separate adapter invocations with no shared session state.
- FR-10: The system SHALL build correction requests from blocking issues when validation fails, appending gate name, description, and location as correction constraints for re-generation.
- FR-11: The system SHALL detect staleness (same gate failing with the same description in consecutive iterations) and log a warning.
- FR-12: The system SHALL detect oscillation (a gate that fails in iteration N, passes in N+1, and fails again in N+2) and log a warning.
- FR-13: The system SHALL maintain a convergence ledger recording iteration number, status, hard gate results, blocking issues, and completeness score for each iteration.
- FR-14: The system SHALL parse validation responses by extracting JSON from fenced code blocks or raw JSON content, requiring at minimum the fields: status, summary, hard_gates, blocking_issues.

**Invariant Enforcement:**
- FR-15: The system SHALL verify that generation and validation use different lifecycle events (Invariant 1: generation/validation separation).
- FR-16: The system SHALL verify that all upstream artifacts are frozen before downstream generation begins, using a configurable dependency map of 30+ artifact type relationships (Invariant 2: freeze-before-promote).
- FR-17: The system SHALL verify that no auto-freeze flag was set, ensuring the freeze decision remains human-controlled (Invariant 3: human freeze decision).
- FR-18: The system SHALL verify that convergence iteration count does not exceed the configured maximum (Invariant 4: bounded convergence).
- FR-19: The system SHALL verify that validator output is valid JSON with required fields (status, summary, hard_gates, blocking_issues, warnings, completeness_score), that status is PASS or FAIL, and that the summary contains no suggestion language (Invariant 5: validator output format).
- FR-20: The system SHALL scan governance content for provider-specific terms (OpenAI, Anthropic, Claude, GPT, ChatGPT, Gemini, Copilot) and flag violations (Invariant 6: tool-agnostic policy).
- FR-21: The system SHALL verify that both the ER file and journal file exist on disk (Invariant 7: disk-based state).

**State Management:**
- FR-22: The system SHALL read ER state blocks by parsing the `| Field | Value |` table in section 1b of Engagement Record Markdown files, extracting Current Layer, Current Artifact, Current Step, Frozen Count, Next Action, Blocking On, and Last Updated.
- FR-23: The system SHALL write ER state blocks by performing in-place regex replacement of field values in the ER Markdown file.
- FR-24: The system SHALL scan `docs/sdlc/*.md` files to extract Artifact ID and Status from Document Control tables, returning a mapping of artifact IDs to artifact status (DRAFT, VALIDATED, FREEZE_PENDING, FROZEN).
- FR-25: The system SHALL append formatted journal entries to the Sherpa Journal file as Markdown sections with timestamp and field/value tables.
- FR-26: The system SHALL parse journal entries from Markdown by splitting on `###` headers and extracting field/value pairs from table rows.

**Provider Adapters:**
- FR-27: The system SHALL define an AgentAdapter Protocol requiring: provider_name property, model_name property, invoke(request) method, health() method, and cost_estimate(request) method.
- FR-28: The system SHALL implement an Anthropic adapter that builds system/user messages from AgentRequest fields, calls the Claude Messages API, tracks token usage and cost using per-model pricing tables, and computes an input content hash for provenance.
- FR-29: The system SHALL implement an OpenAI adapter that builds system/user messages from AgentRequest fields, calls the Chat Completions API, tracks token usage and cost using per-model pricing tables, and computes an input content hash for provenance.
- FR-30: The system SHALL implement a Tool adapter that runs external commands as subprocesses, writes the current artifact to a temp file when present, captures stdout as response content, handles timeouts and command-not-found errors, and reports zero cost.
- FR-31: The system SHALL lazily initialize provider SDK clients on first use, not at adapter construction time.

**Observability:**
- FR-32: The system SHALL record per-invocation metrics as JSON lines to a JSONL file, capturing: timestamp, artifact type, artifact ID, event, provider, model, routing strategy, tokens in/out, cost, latency, result, validation status, convergence iteration, and error.
- FR-33: The system SHALL provide cost summary aggregation by provider and by artifact type, with optional initiative filtering.
- FR-34: The system SHALL provide provider health summary with total invocations, failure count, average latency, and current status (OK, DEGRADED, DOWN based on failure rate thresholds).
- FR-35: The system SHALL detect cost anomalies by flagging invocations whose cost exceeds 3x the rolling mean for the same artifact type within a configurable lookback window (default 24 hours).

**CLI:**
- FR-36: The system SHALL provide a `generate` subcommand that resolves kit files for the specified artifact type, collects frozen upstream artifacts, and invokes the configured adapter.
- FR-37: The system SHALL provide a `validate` subcommand that infers artifact type from filename, locates the corresponding validator prompt, and invokes the configured adapter with the artifact content.
- FR-38: The system SHALL provide a `lifecycle` subcommand that runs generation followed by validation in sequence and presents results for human freeze decision without auto-freezing.
- FR-39: The system SHALL provide a `health` subcommand that checks all enabled provider adapters and displays current and historical health status.
- FR-40: The system SHALL provide a `costs` subcommand that displays cost summary from the observability log with optional initiative filtering.

**Configuration:**
- FR-41: The system SHALL load configuration from a YAML file with sections for providers, routing, lifecycle, observability, and bindings.
- FR-42: The system SHALL read API keys exclusively from environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY) and never from the YAML configuration file.
- FR-43: The system SHALL support environment variable overrides for AIEOS_ROOT and AIEOS_INITIATIVE_ROOT.

### 5.2 Non-Functional Requirements

- NFR-1: The system SHALL run on Python 3.11 or later.
- NFR-2: The system SHALL have no database dependency -- all persistent state is stored on disk as Markdown (ER, Journal) and JSONL (metrics).
- NFR-3: The system SHALL execute all 166 tests (unit + integration) without requiring AI provider API keys, using mock adapters for provider interactions.
- NFR-4: The system SHALL use the Anthropic SDK and OpenAI SDK as optional dependencies, imported lazily only when the corresponding adapter is used.
- NFR-5: The system SHALL handle provider timeouts, API errors, and command-not-found conditions gracefully, returning structured error information rather than crashing.
- NFR-6: The system SHALL include five-element provenance fields on AgentResponse (human_author, input_content_hash, modification_record, compliance_attestation) for AI transparency.
- NFR-7: The system SHALL complete circuit breaker reset within the configured timeout period without requiring manual intervention.

---

## 6. Constraints (Hard Guardrails)

- **C-1: No credentials in configuration files.** API keys are read from environment variables only. The YAML configuration file must never contain API keys or secrets.
- **C-2: No combined generation and validation.** Generation and validation must always be separate adapter invocations. A single invoke() call must not both generate and validate an artifact.
- **C-3: No auto-promotion.** The system must never automatically change an artifact's status from VALIDATED to FROZEN. The freeze decision is always a human action.
- **C-4: No governance file mutation.** The harness reads AIEOS governance files (specs, templates, prompts, validators) but must never create, modify, or delete them.
- **C-5: No in-memory state.** The system of record is on disk. ER state blocks, journal entries, and metrics must be persisted to files, not held only in memory.
- **C-6: No provider-specific logic in core modules.** Provider-specific code (API calls, message formatting, pricing tables, SDK imports) must reside exclusively in adapter implementations under `src/adapters/`.

---

## 7. Assumptions

- **A-1:** The AIEOS governance framework is available at a filesystem path configurable via `aieos_root` in harness.yaml or the `AIEOS_ROOT` environment variable. If this assumption is false, no artifact type resolution can occur.
- **A-2:** Initiative projects follow AIEOS directory conventions (`docs/sdlc/*.md` for artifacts, `docs/engagement/er-*.md` for Engagement Records). If this assumption is false, state management and frozen artifact scanning will not function.
- **A-3:** At least one AI provider API key is set as an environment variable when using LLM adapters. If this assumption is false, the harness can only use the Tool adapter and Mock adapter.
- **A-4:** External tools invoked by the Tool adapter are installed and available on the system PATH. If this assumption is false, the Tool adapter's health check will report DOWN.
- **A-5:** The upstream dependency map in `src/invariants.py` (30+ artifact type relationships) accurately reflects the current AIEOS governance model's freeze-before-promote rules. If the governance model adds new artifact types or changes dependencies, the map must be updated.

---

## 8. Out of Scope by Default

Anything not explicitly included in Sections 1 and 5 is out of scope unless added via PRD change. The following items require explicit out-of-scope declaration due to risk of ambiguity:

- **Multi-initiative orchestration.** The harness operates on one initiative at a time. Cross-initiative coordination, dependency tracking, or parallel initiative execution is out of scope.
- **Prompt engineering or optimization.** The harness passes prompts through to providers as-is. It does not optimize, rewrite, or augment prompts.
- **Artifact content quality beyond invariant checks.** The harness enforces structural invariants (format, required fields, no suggestion language). Semantic quality of generated content is outside the harness scope -- that is the validator's job.
- **Provider billing or account management.** The harness estimates and records costs but does not manage provider accounts, billing alerts, or budget enforcement.
- **Adapter hot-reload.** Adding or removing adapters requires restarting the harness. Runtime adapter registration is out of scope.

---

## 9. Open Questions

No unresolved questions that would block architecture. The system is built and tested. The following are tracked for future consideration:

- Q-1: Should the convergence loop support configurable max iterations per artifact type (currently global default of 3)?
- Q-2: Should the observability layer support export to external monitoring systems (Prometheus, OpenTelemetry) in addition to JSONL?
- Q-3: Should the Tool adapter support structured JSON output parsing in addition to raw stdout capture?

---

## 10. Acceptance / Success Criteria

- **AC-1:** All 166 tests pass (unit + integration) without requiring AI provider API keys. Verified by `pytest -v`.
- **AC-2:** All 7 AIEOS invariants have dedicated check functions in `src/invariants.py` with unit test coverage for both passing and failing cases.
- **AC-3:** The 4 routing strategies (fallback, pipeline, parallel_consensus, cost_aware) are implemented and tested with mock adapters.
- **AC-4:** The convergence loop terminates within the configured maximum iterations, detects staleness and oscillation, and records a complete ledger.
- **AC-5:** The CLI provides 5 subcommands (generate, validate, lifecycle, health, costs) that execute end-to-end with mock providers.
- **AC-6:** Per-invocation metrics are recorded to JSONL with cost summary, provider health summary, and anomaly detection functional.
- **AC-7:** Provider adapters conform to the AgentAdapter Protocol (provider_name, model_name, invoke, health, cost_estimate).
- **AC-8:** No API keys appear in harness.yaml.example or any source file.
- **AC-9:** ER state blocks and journal entries are read from and written to disk as Markdown without database dependency.

---

## 11. Freeze Declaration (when ready)

This PRD documents the existing AIEOS Agent Harness (ECO-009) retroactively. All requirements describe implemented functionality extracted from the codebase.

- Approved By: _pending_
- Date: _pending_
