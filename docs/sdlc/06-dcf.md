# DCF — Design Context File

## 0. document control

- DCF ID: DCF-HARNESS-001
- Owner: Todd Linnertz
- Date: 2026-03-26
- Status: Draft
- Governance Model Version: 1.3
- Prompt Version: dcf-prompt (unversioned)
- Spec Version: dcf-spec v1.0
- Principles Version: code-craftsmanship v1.1
- Applies To: HARNESS initiative — AIEOS Agent Harness (ECO-009), all TDDs within this project

**Note:** This DCF is retroactive. Design standards below are extracted from the implemented codebase (14 source files, 166 tests, 10 test modules, 4 integration test modules). No ACF exists; PRD-HARNESS-001 constraints (C-1 through C-6) serve as the guardrail source.

---

## 1. purpose

This DCF defines the implementation-level design standards, quality bars, testing expectations, and operational requirements that any Technical Design Document for the HARNESS initiative must comply with. It captures standards and constraints extracted from the existing codebase — not designs or implementations.

---

## 2. design principles (Hard)

- **DP-1: Separate domain logic from infrastructure.** Domain components (Data Models, Invariant Enforcer, Convergence Loop) must not import infrastructure modules (adapters, CLI). Application components (Lifecycle Binder, Routing Engine, Config Loader, Observability Layer, State Manager) depend on domain and on the AgentAdapter Protocol abstraction — never on concrete adapter classes. This is enforced by the layer assignment in SAD-HARNESS-001 §4.
- **DP-2: Protocol-based interfaces over inheritance.** All adapter integration uses Python's `Protocol` (PEP 544) with `@runtime_checkable`. No abstract base classes. Adapters conform structurally without inheriting from harness code. New adapters implement `invoke()`, `health()`, `cost_estimate()`, `provider_name`, and `model_name` — nothing else.
- **DP-3: Stateless invocations.** Generation and validation are always separate `invoke()` calls with no shared session state between them (AIEOS Invariant 1, PRD constraint C-2). Correction in the convergence loop produces a new complete AgentRequest — never an in-place edit. No adapter retains state between calls.
- **DP-4: Disk as system of record.** All mutable state resides on disk: ER state blocks in Markdown, Sherpa Journal entries in Markdown, observability metrics in JSONL (AIEOS Invariant 7, PRD constraints C-5, NG-2). No in-memory caches, no databases, no session stores.
- **DP-5: Human retains freeze authority.** The harness never auto-promotes artifact status from Validated to Frozen (AIEOS Invariant 3, PRD constraint C-3). All lifecycle operations present results for human decision.
- **DP-6: Governance files are read-only inputs.** The harness reads specs, templates, prompts, and validators from the AIEOS governance filesystem. It never writes to, modifies, or deletes governance files (PRD constraint C-4).
- **DP-7: Fail explicitly with structured errors.** All error paths return structured information (InvariantCheck with name/passed/reason, AgentResponse with error details). No silent fallbacks. CircuitBreaker, Tool adapter timeout, and command-not-found all produce explicit, machine-readable error data.
- **DP-8: Lazy initialization over eager construction.** Provider SDK clients (Anthropic, OpenAI) initialize on first `invoke()`, not at construction. Unused providers incur no startup cost and do not require API keys to be present.

---

## 3. quality bars (Hard)

- **QB-1: All interfaces have explicit contracts.** The AgentAdapter Protocol defines exactly 5 members (provider_name, model_name, invoke, health, cost_estimate). AgentRequest has 9 fields. AgentResponse has 12 fields with provenance. All dataclasses have typed fields — no **kwargs, no untyped dicts as primary interfaces.
- **QB-2: Every invariant is a pure function.** Each of the 7 invariant checks in `src/invariants.py` is a standalone function that accepts inputs and returns an InvariantCheck dataclass (name, passed, reason). No side effects, no I/O (except freeze-before-promote which delegates to State Manager's read interface).
- **QB-3: Correction constraints are traceable.** When the convergence loop builds a correction request, each blocking issue is formatted as `[gate_name] description (at: location)` and appended to correction_constraints. The convergence ledger records every iteration's hard_gates, blocking_issues, and completeness_score.
- **QB-4: Provider-specific code is fully contained in adapters.** API calls, message formatting, pricing tables, SDK imports, and authentication all reside exclusively in `src/adapters/*.py`. Core modules reference only the AgentAdapter Protocol. Invariant 6 (check_tool_agnostic_policy) scans governance content for provider-specific terms.
- **QB-5: All data models use standard library only.** The `src/models.py` module depends exclusively on `dataclasses`, `enum`, and `typing` from the Python standard library. No external dependencies in the domain layer.
- **QB-6: Error responses preserve diagnostic context.** AgentResponse includes raw_response (optional dict) for provider-specific diagnostics. InvariantCheck includes reason string. ConvergenceState includes full ledger. No information is silently discarded.
- **QB-7: No hardcoded credentials.** API keys are read exclusively from environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY). Config Loader never reads credentials from YAML. No credential appears in source files, configuration files, or log entries (PRD constraint C-1).

---

## 4. non-Goals enforcement (Hard)

- **NGE-1:** The TDD must not design a graphical user interface (PRD NG-1). The system is CLI-only.
- **NGE-2:** The TDD must not design a database backend (PRD NG-2). All state is Markdown and JSONL on disk.
- **NGE-3:** The TDD must not design auto-freeze capability (PRD NG-3). Freeze is always a human action.
- **NGE-4:** The TDD must not design governance file mutation (PRD NG-4). Governance files are read-only.
- **NGE-5:** The TDD must not design multi-tenant operation (PRD NG-5). Single operator, single machine.
- **NGE-6:** The TDD must not design agent memory or context persistence across invocations (PRD NG-6). Each invocation is stateless.
- **NGE-7:** The TDD must not design real-time streaming (PRD NG-7). All provider interactions are synchronous request-response.
- **NGE-8:** Any component that appears to expand beyond PRD scope must be flagged and removed. "Helpful" additions are not permitted.

---

## 5. operational expectations (Hard)

- **Deployment verification:** The system is a local CLI tool, not a deployed service. Verification is: `pytest -v` passes all tests with zero failures, and `pip install -r requirements-lock.txt` completes without errors. No health check endpoint exists (CLI tool, not a service).
- **Monitoring/alerting:** Per-invocation structured metrics recorded to JSONL (15 fields per InvocationRecord). Cost summary aggregation by provider and artifact type. Provider health summary with derived status (OK/DEGRADED/DOWN based on failure rate thresholds). Cost anomaly detection flagging invocations exceeding 3x rolling mean within a configurable lookback window (default 24 hours). No external monitoring integration (deferred decision in SAD-HARNESS-001 §11).
- **Auditability:** Every invocation produces a JSONL record with timestamp, artifact_type, artifact_id, provider, model, strategy, tokens, cost, latency, result, and validation_status. Convergence loops produce a full ledger (iteration-by-iteration hard gate results, blocking issues, completeness scores). ER state block updates are written in-place with Last Updated timestamps. Sherpa Journal entries include UTC timestamps. All evidence is on disk and human-readable.

---

## 6. testing expectations (Hard)

### Required test layers

- **Unit tests:** All domain logic (invariant checks, convergence loop, data models, routing strategies, lifecycle binding, state parsing, observability aggregation, config loading). All 7 invariant checks tested individually. All 4 routing strategies tested (fallback, pipeline, parallel_consensus, cost_aware). Circuit breaker open/close/half-open transitions tested. Convergence staleness and oscillation detection tested. Markdown table parsing for ER state blocks tested. JSONL recording and aggregation tested. Isolated from infrastructure — all tests use MockAdapter, no API keys required.
- **Integration tests:** Full lifecycle flow (generate then validate via mock providers). Convergence loop end-to-end (multi-iteration with mock adapters returning progressive validation results). Multi-provider routing with circuit breaker interaction. Lens orchestration flows.

### Evidence requirements

- pytest execution report (pass/fail per test, execution time)
- All 166+ tests pass with zero failures
- No test requires network access or real provider API keys (except tests marked `--run-slow`)

### Evidence management

- **Formats:** pytest terminal output, pytest JUnit XML (if CI configured)
- **Storage:** Local execution; CI pipeline output when CI is configured
- **Retention:** Test code versioned alongside source code in the repository
- **Accessibility:** Reproducible by running `pytest -v` from the repository root

### Promotion gates

- All unit tests pass (zero failures): `pytest tests/ -v --ignore=tests/integration`
- All integration tests pass: `pytest tests/integration/ -v`
- No test requires real API keys (MockAdapter for all automated tests)
- Verified install completes: `pip install -r requirements-lock.txt`
- Python type annotations present on all public interfaces (dataclasses, Protocol, function signatures)

---

## 7. documentation expectations (Hard)

### Required TDD sections

- Component design for each SAD component (Config Loader, Data Models, Lifecycle Binder, Routing Engine, Convergence Loop, State Manager, Invariant Enforcer, Observability Layer, CLI, Adapter Protocol, and each adapter implementation)
- Interface contracts: AgentAdapter Protocol (5 members), AgentRequest (9 fields), AgentResponse (12 fields), ValidationResult (6 fields), all with typed signatures
- State transitions: Artifact lifecycle (DRAFT → VALIDATED → FREEZE_PENDING → FROZEN), Circuit breaker (CLOSED → OPEN → HALF-OPEN → CLOSED), Convergence (generate → validate → parse → pass/correct/escalate)
- Error handling: Per-component failure modes from SAD-HARNESS-001 §8

### Required diagram types

- Sequence diagram: lifecycle command flow (CLI → Lifecycle Binder → Routing Engine → Adapter → Convergence Loop → State Manager → output)
- Component diagram: layer assignment (Domain/Application/Infrastructure) with dependency arrows

### Required traceability markers

- Every TDD component must reference its SAD component (SAD-HARNESS-001 §4)
- Every test scenario must reference the PRD requirement it validates (FR-N, NFR-N, C-N)
- Every invariant check must reference the AIEOS invariant it enforces (Invariant 1-7)

---

## 8. open items

- OI-1: Per-artifact-type convergence limits not yet implemented (deferred decision in SAD-HARNESS-001 §11). Global default of 3 iterations applies to all artifact types. Revisit if operator feedback indicates artifact types with different convergence characteristics.
- OI-2: Configuration schema validation not implemented (deferred decision in SAD-HARNESS-001 §11). Invalid YAML keys are silently ignored. Revisit when configuration complexity grows.

---

## 9. freeze declaration (when ready)

- Approved By: _pending_
- Date: _pending_

<!-- PRINCIPLES COVERAGE
| Principles File | Section | DCF Section Addressed | Status |
|---|---|---|---|
| code-craftsmanship v1.1 | §1.1 Readability Over Cleverness | §2 DP-7 (explicit errors), §3 QB-1 (explicit contracts) | Addressed |
| code-craftsmanship v1.1 | §1.2 Single Responsibility | §2 DP-1 (layer separation), DP-2 (protocol-based interfaces) | Addressed |
| code-craftsmanship v1.1 | §1.3 Clear Naming | §3 QB-1 (typed fields, no kwargs) | Addressed |
| code-craftsmanship v1.1 | §1.4 Self-Documenting Code | §7 Documentation Expectations | Addressed |
| code-craftsmanship v1.1 | §1.5 No Duplication (DRY) | §3 QB-4 (provider logic in adapters only) | Addressed |
| code-craftsmanship v1.1 | §1.6 Complexity Control | §2 DP-2 (protocol over inheritance), DP-3 (stateless) | Addressed |
| code-craftsmanship v1.1 | §1.7 Explicit Error Handling | §2 DP-7 (structured errors), §3 QB-6 (diagnostic context) | Addressed |
| code-craftsmanship v1.1 | §1.8 Dependency Discipline | §2 DP-1 (domain/app/infra separation), DP-2 (protocol interfaces) | Addressed |
| code-craftsmanship v1.1 | §3 Test Design Standards | §6 Testing Expectations (unit, integration, MockAdapter isolation) | Addressed |
| code-craftsmanship v1.1 | §4 Implementation Standards | §3 QB-5 (stdlib-only domain), QB-7 (no hardcoded credentials) | Addressed |
| code-craftsmanship v1.1 | §5 Red Flag Patterns | §4 Non-Goals Enforcement (NGE-1 through NGE-8) | Addressed |
| code-craftsmanship v1.1 | §6 Refactor Triggers | §3 QB-2 (pure functions for invariants) | Addressed |
-->
