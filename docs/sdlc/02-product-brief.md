# Product Brief (PRD Intake)

A lightweight intake form for capturing product intent before generating a PRD.
This is a **human-authored input**, not an AI-generated artifact.

This brief is **retroactive**: the system described below already exists. All information is extracted from the actual codebase, not speculated.

---

## Why

### Objective
- Provide a pluggable multi-agent orchestration engine that connects AIEOS governance artifacts to AI providers and deterministic tools
- Automate the generate-validate-converge lifecycle for AIEOS artifacts while enforcing all AIEOS structural invariants programmatically
- Enable artifact production across multiple AI providers with resilient routing, cost tracking, and bounded convergence

### Current problem
- AIEOS governance defines a structured artifact lifecycle (specs, templates, prompts, validators) but has no automation layer to orchestrate AI-assisted artifact production
- Without orchestration, each artifact generation and validation requires manual session setup, manual upstream dependency checking, manual convergence tracking, and manual cost accounting
- Framework operators must manually enforce invariants (freeze-before-promote, generation/validation separation, bounded convergence) which is error-prone and unscalable
- There is no observability into AI provider costs, latency, or failure rates across artifact production

### Who is impacted:
- AIEOS framework operators (the primary users) who run artifact lifecycle events
- Initiative sponsors who need cost visibility and audit trails for AI-assisted governance

### Quantification:
- 16 AIEOS layers, each with multiple artifact types, each requiring generate + validate cycles
- 7 AIEOS invariants that must be enforced on every invocation: manual enforcement is unsustainable at scale

---

## What

### Functional requirements
- Map artifact lifecycle events (pre_generation, post_generation, pre_validation, post_validation, post_freeze, on_failure) to agent adapter invocations via YAML configuration
- Route requests through adapters using 4 strategies: fallback (try in order), pipeline (sequential chain), parallel_consensus (fan-out with agreement threshold), cost_aware (cheapest first)
- Run bounded convergence loops (generate, validate, correct, retry) with max 3 iterations, staleness detection, and oscillation detection
- Enforce 7 AIEOS invariants programmatically: generation/validation separation, freeze-before-promote, human freeze decision, bounded convergence, validator output format, tool-agnostic policy, disk-based state
- Read/write Engagement Record state blocks and Sherpa Journal entries on disk (Markdown, no database)
- Record per-invocation metrics (cost, latency, tokens, provider, model, result) to JSONL
- Provide cost summary, provider health summary, and cost anomaly detection (3x rolling mean)
- Support pluggable provider adapters via Python Protocol: Anthropic Claude, OpenAI, non-LLM tools (SAST/linters), mock (testing)
- CLI interface with subcommands: generate, validate, lifecycle, health, costs

### Scope
- **In scope:** Orchestration engine, provider adapters, routing strategies, convergence loop, invariant enforcement, state management, observability, CLI
- **Explicitly out of scope:** UI/dashboard, database backend, real-time streaming, multi-tenant operation, agent memory/context persistence across sessions

### Exclusions
- No modification of AIEOS governance files (specs, templates, prompts, validators): the harness consumes them read-only
- No auto-freeze capability: artifact promotion from VALIDATED to FROZEN always requires human decision
- No provider-specific logic in core modules: all provider details live in adapter implementations

### Reference documents
- `CLAUDE.md`: project overview and operating rules
- `docs/architecture.md`: component diagram, request flow, invariant enforcement points
- `harness.yaml.example`: configuration schema
- `aieos-governance-foundation/docs/ecosystem-roadmap.md`: ECO-009 specification

---

## Who

### Target personas
- Framework Operator: runs artifact lifecycle events via CLI, reviews generated artifacts, makes freeze decisions. Technical user comfortable with CLI tools and YAML configuration.
- Initiative Sponsor: reviews cost reports and audit trails. Non-technical user who consumes observability outputs.

### External dependencies
- Anthropic SDK (Claude API)
- OpenAI SDK (Chat Completions API)
- AIEOS governance framework (specs, templates, prompts, validators consumed as read-only Markdown)
- External tools (SAST scanners, linters) invoked via subprocess by the Tool adapter

### Sponsor
- Todd Linnertz (initiative sponsor, framework designer, implementation author)

### Blockers
- None: the system is built and tested (166 tests, all passing)

---

## When

### Release criteria
- All 166 tests pass (unit + integration)
- All 7 AIEOS invariants are enforced with dedicated test coverage
- Provider adapters conform to the AgentAdapter Protocol
- No API keys stored in configuration files

### Success criteria
- Artifact lifecycle events can be orchestrated end-to-end via CLI
- Provider failures are handled via circuit breaker and fallback routing
- Convergence loop terminates within 3 iterations or escalates
- Per-invocation cost and latency are recorded with anomaly detection

### Timeline
- System is complete as of 2026-03-26
- Retroactive governance engagement produces documentation artifacts

---

## How (Non-Functional)

### Non-Functional requirements
- Python 3.11+ runtime
- No database: all state on disk (ER Markdown, Journal Markdown, JSONL metrics)
- Lazy client initialization (provider SDK clients created on first use)
- Circuit breaker with configurable failure threshold and reset timeout
- Tool adapter timeout of 300 seconds per subprocess invocation

### Assumptions
- AIEOS governance framework is available at a configurable root path
- AI provider API keys are set as environment variables
- Initiative projects follow AIEOS directory conventions (docs/sdlc/*.md, docs/engagement/er-*.md)

### Risks
- AI provider API rate limits or outages: mitigated by fallback routing and circuit breaker
- Cost overruns from convergence loops: mitigated by bounded convergence (max 3 iterations) and cost anomaly detection
- Stale convergence (same gate failing repeatedly): mitigated by staleness and oscillation detection with escalation

### Compliance
- No credentials stored in configuration files or source code
- All AI outputs include provenance fields (input content hash, human author, modification record)
- Validators enforce standardized JSON output format with no suggestion language

---

## Completeness checklist

Before handing this to the PRD generation prompt, confirm:

- [x] Problem is clearly stated
- [x] At least one goal or outcome is defined
- [x] Scope boundaries are explicit (in scope and out of scope)
- [x] Primary users or personas are identified
- [x] Known constraints are listed
