# WDD: AIEOS Agent Harness

## 0. Document Control
- WDD ID: WDD-HARNESS-001
- Author: Todd Linnertz (retroactive decomposition by AI)
- Date: 2026-03-26
- Status: Draft
- Governance Model Version: 1.3
- Prompt Version: wdd-prompt v1.0
- Spec Version: wdd-spec v1.0
- Principles Version: N/A (no principles files exist for this ecosystem project; PRD-HARNESS-001 Section 6 Constraints serves as the guardrail source)
- Parent TDD:
  - TDD ID / Link: TDD-HARNESS-001 (docs/sdlc/07-tdd.md)
  - TDD Status: Frozen (required)

**Note:** This WDD is retroactive. All work items describe what was actually built. Every item is complete with merged code, passing tests, and evidence in the repository.

---

## 1. Scope and Non-Goals (Copied from TDD)

### In Scope

- Lifecycle event binding: map lifecycle events to adapter invocations via YAML-configured EventBinding dataclasses
- Multi-strategy routing engine with 4 strategies and per-provider CircuitBreaker
- Bounded convergence loop with JSON extraction, staleness detection, and oscillation detection
- 7 programmatic invariant checks with a 30+ entry upstream dependency map
- Provider adapter layer: AgentAdapter Protocol with 4 concrete implementations (Anthropic, OpenAI, Tool, Mock)
- Disk-based state management for ER state blocks (read/write via regex) and Sherpa Journal (append-only)
- Per-invocation JSONL observability with cost summary, provider health summary, and cost anomaly detection
- CLI with 5 subcommands: generate, validate, lifecycle, health, costs
- Configuration: YAML loading via yaml.safe_load with environment variable overrides for AIEOS_ROOT, AIEOS_INITIATIVE_ROOT, and API keys

### Explicit Non-Goals (Must align with SAD)

- No graphical user interface (NG-1)
- No database backend (NG-2)
- No auto-freeze capability (NG-3)
- No governance file modification (NG-4)
- No multi-tenant operation (NG-5)
- No agent memory or context persistence across invocations (NG-6)
- No real-time streaming (NG-7)
- No multi-initiative orchestration
- No prompt engineering or optimization
- No artifact content quality judgment beyond structural invariant checks
- No provider billing or account management
- No adapter hot-reload

---

## 2. Work Items

### WDD Item
- WDD Item ID: WDD-HARNESS-001
- Parent TDD Section: §4.18 Data Models, §3 Technical Overview (Data Models)
- Assignee Type: AI Agent
- Required Capabilities: backend, Python-dataclasses
- Complexity Estimate: M — 5 enums and 7 dataclasses forming the shared vocabulary across all components; multiple concerns but well-understood patterns

#### Intent (1-2 sentences)
Implement the core data models that define the shared vocabulary for all harness components: 5 enums (ArtifactStatus, LifecycleEvent, RoutingStrategy, HealthStatus, DecisionOutcome) and 7 dataclasses (AgentRequest, AgentResponse, ValidationResult, ERStateBlock, InvocationRecord, ConvergenceState, InvariantCheck).

#### In Scope
- 5 enum types with all member values per TDD §4.18
- 7 dataclass definitions with typed fields, defaults, and optional fields per TDD §4.18
- AgentResponse provenance fields (human_author, input_content_hash, modification_record, compliance_attestation)
- Zero external dependencies — Python stdlib only (dataclasses, enum)

#### Out of Scope / Non-Goals
- No business logic — models are pure data containers
- No serialization methods (handled by consuming modules)

#### Inputs
- TDD §4.18 Data Models specification (field names, types, defaults)

#### Outputs
- `src/models.py` — complete module with all enums and dataclasses

#### Acceptance Criteria (Executable)
- AC1: Given models.py is imported, When all 5 enums are accessed, Then each enum has the expected member values (ArtifactStatus: DRAFT/VALIDATED/FREEZE_PENDING/FROZEN; LifecycleEvent: 6 values; RoutingStrategy: 4 values; HealthStatus: 3 values; DecisionOutcome: 6 values). Failure: any enum missing members or having unexpected members.
- AC2: Given models.py is imported, When all 7 dataclasses are instantiated with required fields, Then instances are created with correct types and default values. Failure: TypeError on construction or incorrect default values.
- AC3: Given AgentResponse is instantiated, When provenance fields are omitted, Then they default to None. Failure: provenance fields require explicit values.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_models.py)
- [x] Evidence: `src/models.py` exists with all types; `pytest tests/test_models.py -v` passes

#### Interface Contract References
- Provider: TDD §4.18 — all downstream components consume these types

#### Dependencies
None

#### Rollback / Failure Behavior
Revert `src/models.py` to empty module. All downstream modules will fail to import — no partial state.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-002
- Parent TDD Section: §4.19 Config Dataclasses, §4.20 CLI Functions (load_config)
- Assignee Type: AI Agent
- Required Capabilities: backend, Python-YAML, configuration
- Complexity Estimate: M — Multiple config dataclasses plus YAML loading with env var override logic; 1 dependency on models

#### Intent (1-2 sentences)
Implement the configuration loading system: 3 config dataclasses (ProviderConfig, RoutingConfig, HarnessConfig) and the `load_config()` function that reads YAML files via `yaml.safe_load` and applies environment variable overrides for AIEOS_ROOT and AIEOS_INITIATIVE_ROOT.

#### In Scope
- ProviderConfig, RoutingConfig, HarnessConfig dataclasses per TDD §4.19
- `load_config(path)` function per TDD §4.19
- Environment variable overrides: AIEOS_ROOT, AIEOS_INITIATIVE_ROOT
- Default HarnessConfig when file missing
- `yaml.safe_load` for YAML parsing

#### Out of Scope / Non-Goals
- No schema validation of YAML keys (documented risk in TDD §12)
- No hot-reload of configuration

#### Inputs
- `harness.yaml` (or specified path) — YAML configuration file
- Environment variables: AIEOS_ROOT, AIEOS_INITIATIVE_ROOT

#### Outputs
- `src/config.py` — config module with dataclasses and load function

#### Acceptance Criteria (Executable)
- AC1: Given a valid harness.yaml file exists, When `load_config(path)` is called, Then a HarnessConfig is returned with providers, routing, and top-level fields populated from YAML. Failure: fields not matching YAML content.
- AC2: Given no harness.yaml file exists, When `load_config(path)` is called, Then a default HarnessConfig is returned with all defaults. Failure: raises exception instead of returning defaults.
- AC3: Given AIEOS_ROOT env var is set, When `load_config()` is called, Then `config.aieos_root` equals the env var value (overriding YAML). Failure: YAML value used instead of env var.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_config.py)
- [x] Evidence: `src/config.py` exists; `pytest tests/test_config.py -v` passes

#### Interface Contract References
- Consumer: TDD §4.20 CLI — CLI calls `load_config()` to build HarnessConfig

#### Dependencies
- WDD-HARNESS-001 (uses models.py types)

#### Rollback / Failure Behavior
Revert `src/config.py`. CLI and adapter construction will fail. No state to clean up — config is read-only.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-003
- Parent TDD Section: §4.15 State Manager Functions
- Assignee Type: AI Agent
- Required Capabilities: backend, Markdown-parsing, filesystem-IO
- Complexity Estimate: L — 6 functions spanning read/write for ER state blocks, frozen artifact scanning, and journal operations; regex-based Markdown parsing across multiple file formats

#### Intent (1-2 sentences)
Implement the state manager: read/write ER state blocks via regex-based Markdown table parsing, scan `docs/sdlc/*.md` for frozen artifacts, and append/read Sherpa Journal entries as Markdown sections.

#### In Scope
- `read_er_state_block(er_path)` — regex extraction of 7 fields from Markdown table
- `write_er_state_block(er_path, state)` — in-place regex replacement of field values
- `read_frozen_artifacts(initiative_path)` — glob scan of `docs/sdlc/*.md`, status extraction and normalization
- `is_artifact_frozen(initiative_path, artifact_id)` — frozen check via read_frozen_artifacts
- `append_journal_entry(journal_path, entry_type, fields)` — append Markdown section with table
- `read_journal_entries(journal_path)` — parse `### entry_type -- timestamp` sections

#### Out of Scope / Non-Goals
- No database persistence (NG-2)
- No in-memory state caching across invocations (NG-6)

#### Inputs
- ER Markdown files with `| Field | Value |` tables
- `docs/sdlc/*.md` files with Artifact ID and Status fields
- Sherpa Journal Markdown files

#### Outputs
- `src/state.py` — state manager module with all 6 functions

#### Acceptance Criteria (Executable)
- AC1: Given an ER Markdown file with a state block table, When `read_er_state_block(path)` is called, Then an ERStateBlock is returned with all 7 fields extracted. Failure: fields empty or incorrect when table exists.
- AC2: Given an ER Markdown file, When `write_er_state_block(path, state)` is called, Then the file is modified in-place with updated field values while preserving surrounding content. Failure: file content outside state block is altered, or field values not updated.
- AC3: Given a `docs/sdlc/` directory with frozen and non-frozen artifacts, When `read_frozen_artifacts(path)` is called, Then only FROZEN artifacts are returned in the mapping. Failure: non-frozen artifacts included or frozen artifacts excluded.
- AC4: Given a journal file with multiple entries, When `read_journal_entries(path)` is called, Then all entries are returned with entry_type, timestamp, and field/value pairs parsed. Failure: entries missing or fields not extracted.
- AC5: Given a journal path, When `append_journal_entry(path, type, fields)` is called, Then a new Markdown section is appended with UTC timestamp and field table. Failure: entry not appended or timestamp not UTC.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_state.py)
- [x] Evidence: `src/state.py` exists; `pytest tests/test_state.py -v` passes

#### Interface Contract References
- Provider: TDD §4.15 — Invariant Enforcer (§4.16) consumes `read_frozen_artifacts()`

#### Dependencies
- WDD-HARNESS-001 (uses ArtifactStatus, ERStateBlock from models.py)

#### Rollback / Failure Behavior
Revert `src/state.py`. Invariant checks and CLI state operations will fail. No persistent state corruption — functions only modify files they are explicitly told to modify.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-004
- Parent TDD Section: §4.16 Invariant Check Functions
- Assignee Type: AI Agent
- Required Capabilities: backend, AIEOS-governance, regex
- Complexity Estimate: L — 7 invariant check functions plus a 30+ entry upstream dependency map; cross-cutting concerns spanning governance rules, regex scanning, and filesystem checks

#### Intent (1-2 sentences)
Implement 7 pure-function invariant checks that enforce AIEOS structural rules programmatically: generation/validation separation, freeze-before-promote, human freeze decision, bounded convergence, validator output format, tool-agnostic policy, and disk-based state verification. Includes the 30+ entry UPSTREAM_DEPENDENCIES map.

#### In Scope
- 7 `check_*` functions returning `InvariantCheck` per TDD §4.16
- `UPSTREAM_DEPENDENCIES` dict mapping 30+ artifact types to required frozen upstream types
- Compiled regex patterns for suggestion language (`_SUGGESTION_PATTERNS`) and provider-specific terms (`_TOOL_SPECIFIC_TERMS`)
- JSON parsing and field validation for validator output format check

#### Out of Scope / Non-Goals
- No artifact content quality judgment — only structural invariant enforcement
- No modification of governance files (NG-4)

#### Inputs
- LifecycleEvent pairs (for separation check)
- Initiative filesystem path (for freeze-before-promote, disk-based state)
- ConvergenceState (for bounded convergence)
- Validation response text (for output format)
- Artifact content text (for tool-agnostic policy)

#### Outputs
- `src/invariants.py` — invariant enforcer module with 7 check functions and dependency map

#### Acceptance Criteria (Executable)
- AC1: Given generation and validation use different LifecycleEvents, When `check_generation_validation_separation()` is called, Then `passed=True`. Given same event for both, Then `passed=False`. Failure: incorrect pass/fail.
- AC2: Given all required upstream artifacts are frozen, When `check_freeze_before_promote()` is called, Then `passed=True`. Given a required upstream is not frozen, Then `passed=False` with missing types listed. Failure: allows generation with missing frozen upstream.
- AC3: Given validator JSON with suggestion language ("consider", "suggest", etc.), When `check_validator_output_format()` is called, Then `passed=False`. Failure: suggestion language not detected.
- AC4: Given content containing provider-specific terms ("OpenAI", "Claude", etc.), When `check_tool_agnostic_policy()` is called, Then `passed=False`. Failure: provider terms not detected.
- AC5: Given both ER and Journal files exist on disk, When `check_disk_based_state()` is called, Then `passed=True`. Given either file missing, Then `passed=False`. Failure: missing file not detected.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_invariants.py — positive and negative tests for each check)
- [x] Evidence: `src/invariants.py` exists; `pytest tests/test_invariants.py -v` passes

#### Interface Contract References
- Consumer: TDD §4.15 State Manager — consumes `read_frozen_artifacts()` for freeze-before-promote check

#### Dependencies
- WDD-HARNESS-001 (uses ConvergenceState, InvariantCheck, LifecycleEvent, ValidationResult)
- WDD-HARNESS-003 (uses state.read_frozen_artifacts)

#### Rollback / Failure Behavior
Revert `src/invariants.py`. CLI will lose invariant enforcement — generation proceeds without governance checks. No state corruption.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-005
- Parent TDD Section: §4.1 AgentAdapter Protocol, §4.5 MockAdapter
- Assignee Type: AI Agent
- Required Capabilities: backend, Python-Protocol, API-design
- Complexity Estimate: M — Protocol interface definition plus mock test double; 2 files but well-defined contract with clear interface

#### Intent (1-2 sentences)
Implement the AgentAdapter Protocol interface (`@runtime_checkable`) defining the adapter contract (provider_name, model_name, invoke, health, cost_estimate) and the MockAdapter test double with preset responses, configurable failure, and call history tracking.

#### In Scope
- `AgentAdapter` Protocol with 5 members per TDD §4.1
- `MockAdapter` class with preset_responses, health_status, should_fail, call_history per TDD §4.5
- SHA-256 provenance hash computation in MockAdapter

#### Out of Scope / Non-Goals
- No real API calls — mock only
- No adapter registration or factory (handled by CLI)

#### Inputs
- TDD §4.1 Protocol specification
- TDD §4.5 MockAdapter specification

#### Outputs
- `src/adapters/base.py` — Protocol definition
- `src/adapters/mock.py` — Mock test double

#### Acceptance Criteria (Executable)
- AC1: Given MockAdapter is instantiated, When `isinstance(mock, AgentAdapter)` is checked at runtime, Then it returns True. Failure: Protocol check fails.
- AC2: Given MockAdapter with preset_responses, When `invoke(request)` is called with a matching artifact_type, Then the preset content is returned. Failure: default content returned instead of preset.
- AC3: Given MockAdapter with `should_fail=True`, When `invoke(request)` is called, Then `RuntimeError` is raised with the failure message. Failure: no exception raised.
- AC4: Given MockAdapter, When multiple methods are called, Then all calls are recorded in `call_history`. Failure: call_history missing entries.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_adapters.py — MockAdapter section)
- [x] Evidence: `src/adapters/base.py` and `src/adapters/mock.py` exist; `pytest tests/test_adapters.py -v` passes

#### Interface Contract References
- Provider: TDD §4.1 AgentAdapter Protocol — defines the interface all adapters implement
- Provider: TDD §4.5 MockAdapter — test infrastructure implementing the protocol

#### Dependencies
- WDD-HARNESS-001 (uses AgentRequest, AgentResponse, HealthStatus)

#### Rollback / Failure Behavior
Revert both files. All adapter implementations, routing, lifecycle, and convergence modules will fail to import. No state to clean up.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-006
- Parent TDD Section: §4.6 LifecycleBinder, §4.7 EventBinding
- Assignee Type: AI Agent
- Required Capabilities: backend, event-binding
- Complexity Estimate: M — EventBinding dataclass plus LifecycleBinder with exact/wildcard resolution and single-adapter dispatch; moderate integration surface

#### Intent (1-2 sentences)
Implement the lifecycle binder that maps `(LifecycleEvent, artifact_type)` pairs to adapter invocations. Resolves exact artifact type matches before wildcard (`"*"`) bindings, and dispatches to the first resolved adapter.

#### In Scope
- `EventBinding` dataclass per TDD §4.7
- `LifecycleBinder.resolve()` — exact vs wildcard priority matching per TDD §4.6
- `LifecycleBinder.execute()` — single-adapter dispatch per TDD §4.6
- RuntimeError when no binding matches

#### Out of Scope / Non-Goals
- No multi-adapter dispatch from lifecycle binder (routing engine handles multi-adapter)
- No binding hot-reload

#### Inputs
- List of EventBinding configurations
- Dict of named adapters
- LifecycleEvent and AgentRequest

#### Outputs
- `src/lifecycle.py` — lifecycle binder module with EventBinding and LifecycleBinder

#### Acceptance Criteria (Executable)
- AC1: Given bindings for both exact type "SAD" and wildcard "*", When `resolve(event, "SAD")` is called, Then exact bindings are returned (not wildcard). Failure: wildcard bindings returned when exact match exists.
- AC2: Given no binding matches event/type, When `execute(event, request)` is called, Then RuntimeError is raised with event and artifact_type in message. Failure: no exception or generic error.
- AC3: Given a matching binding, When `execute(event, request)` is called, Then the first resolved adapter's `invoke()` is called and response returned. Failure: wrong adapter invoked or response not returned.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_lifecycle.py)
- [x] Evidence: `src/lifecycle.py` exists; `pytest tests/test_lifecycle.py -v` passes

#### Interface Contract References
- Consumer: TDD §4.1 AgentAdapter Protocol — lifecycle binder invokes adapters through this interface

#### Dependencies
- WDD-HARNESS-001 (uses LifecycleEvent, RoutingStrategy, AgentRequest, AgentResponse)
- WDD-HARNESS-005 (uses AgentAdapter Protocol)

#### Rollback / Failure Behavior
Revert `src/lifecycle.py`. CLI lifecycle command will fail. No state corruption — lifecycle binder is stateless.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-007
- Parent TDD Section: §4.8 CircuitBreaker, §4.9 RoutingEngine
- Assignee Type: AI Agent
- Required Capabilities: backend, concurrency, routing
- Complexity Estimate: L — 4 routing strategies (fallback, pipeline, parallel_consensus, cost_aware) plus CircuitBreaker state machine with time-based reset; ThreadPoolExecutor for parallel fan-out; 5+ acceptance criteria

#### Intent (1-2 sentences)
Implement the routing engine with 4 dispatch strategies (fallback, pipeline, parallel_consensus, cost_aware) and the CircuitBreaker that tracks per-provider failure counts with time-based auto-reset. Parallel consensus uses ThreadPoolExecutor for concurrent adapter invocation.

#### In Scope
- `CircuitBreaker` class with record_failure, record_success, is_open per TDD §4.8
- `RoutingEngine.route()` dispatch to 4 strategy methods per TDD §4.9
- Fallback: sequential with circuit breaker skip, all-fail RuntimeError
- Pipeline: sequential chaining with current_artifact propagation
- Parallel consensus: ThreadPoolExecutor fan-out, agreement threshold, content-length-based comparison
- Cost-aware: cost_estimate sorting, min_tier filtering, fallback-pattern invocation

#### Out of Scope / Non-Goals
- No custom strategy plugins
- No adapter hot-reload

#### Inputs
- RoutingStrategy enum, list of AgentAdapter instances, AgentRequest, config dict

#### Outputs
- `src/routing.py` — routing engine module with CircuitBreaker and RoutingEngine

#### Acceptance Criteria (Executable)
- AC1: Given 3 adapters where first fails, When `_fallback()` is called, Then second adapter is tried and its response returned. Given all fail, Then RuntimeError lists all errors. Failure: stops after first failure or error details missing.
- AC2: Given 2 adapters in pipeline, When `_pipeline()` is called, Then first response content becomes second request's current_artifact. Failure: content not chained.
- AC3: Given 3 adapters with similar content lengths, When `_parallel_consensus()` is called with threshold 0.67, Then first response returned. Given divergent lengths, Then ValueError raised. Failure: wrong threshold behavior.
- AC4: Given adapters with different cost estimates, When `_cost_aware()` is called, Then cheapest adapter tried first. Failure: invocation order not by cost.
- AC5: Given a provider with 3 consecutive failures, When `is_open()` is called, Then returns True. After reset_seconds, Then returns False. Failure: circuit breaker state incorrect.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_routing.py)
- [x] Evidence: `src/routing.py` exists; `pytest tests/test_routing.py -v` passes

#### Interface Contract References
- Consumer: TDD §4.1 AgentAdapter Protocol — routing engine invokes adapters through this interface

#### Dependencies
- WDD-HARNESS-001 (uses RoutingStrategy, AgentRequest, AgentResponse)
- WDD-HARNESS-005 (uses AgentAdapter Protocol)

#### Rollback / Failure Behavior
Revert `src/routing.py`. CLI and lifecycle binder lose multi-adapter routing. CircuitBreaker state is in-memory only — no persistent state to clean up.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-008
- Parent TDD Section: §4.10-§4.14 Convergence Loop
- Assignee Type: AI Agent
- Required Capabilities: backend, JSON-parsing, pattern-detection
- Complexity Estimate: L — Convergence loop with 5 functions (run, parse, detect_staleness, detect_oscillation, build_correction_request); cross-cutting JSON extraction, ledger management, and pattern analysis

#### Intent (1-2 sentences)
Implement the bounded convergence loop that iterates generate-validate cycles up to max_iterations. Includes JSON extraction from fenced/raw content, staleness detection (same gate fails identically in consecutive iterations), oscillation detection (gate flip-flops across 3 iterations), and correction request building from blocking issues.

#### In Scope
- `ConvergenceLoop.run()` — bounded iteration loop per TDD §4.10
- `parse_validation_result()` — fenced JSON, raw JSON, field validation per TDD §4.11
- `_detect_staleness()` — consecutive identical failure detection per TDD §4.12
- `_detect_oscillation()` — 3-entry flip-flop detection per TDD §4.13
- `_build_correction_request()` — constraint appending per TDD §4.14
- Ledger entry recording per iteration

#### Out of Scope / Non-Goals
- No auto-freeze after convergence (NG-3)
- No prompt modification (prompt engineering is out of scope)

#### Inputs
- Generate AgentRequest, Validate AgentRequest
- AgentAdapter instances for generation and validation

#### Outputs
- `src/convergence.py` — convergence module with loop, parser, and detectors

#### Acceptance Criteria (Executable)
- AC1: Given validation passes on first iteration, When `run()` is called, Then returns (response, result, state) with status "PASS" and current_iteration=1. Failure: extra iterations run.
- AC2: Given validation fails all 3 iterations, When `run()` is called, Then returns with status "FAIL" and current_iteration=3 (escalation). Failure: exceeds max_iterations.
- AC3: Given validation response with fenced JSON block, When `parse_validation_result()` is called, Then ValidationResult is extracted. Given no JSON found, Then ValueError raised. Failure: incorrect parsing or no exception.
- AC4: Given same gate fails with identical description in 2 consecutive iterations, When `_detect_staleness()` is called, Then returns True. Failure: staleness not detected.
- AC5: Given a gate that FAIL→PASS→FAIL across 3 iterations, When `_detect_oscillation()` is called, Then returns True. Failure: oscillation not detected.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_convergence.py)
- [x] Evidence: `src/convergence.py` exists; `pytest tests/test_convergence.py -v` passes

#### Interface Contract References
- Consumer: TDD §4.1 AgentAdapter Protocol — convergence loop invokes adapters (generate + validate as separate calls)

#### Dependencies
- WDD-HARNESS-001 (uses AgentRequest, AgentResponse, ConvergenceState, ValidationResult)
- WDD-HARNESS-005 (uses AgentAdapter Protocol)

#### Rollback / Failure Behavior
Revert `src/convergence.py`. CLI lifecycle command loses auto-convergence. No persistent state — convergence state is in-memory only.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-009
- Parent TDD Section: §4.2 AnthropicAdapter
- Assignee Type: AI Agent
- Required Capabilities: backend, API-integration, Anthropic-SDK
- Complexity Estimate: M — Single adapter implementing Protocol; lazy SDK init, message building, pricing table, provenance hash; well-defined contract

#### Intent (1-2 sentences)
Implement the Anthropic Claude adapter with lazy SDK initialization, system/user message building from AgentRequest fields, per-model pricing tables, and SHA-256 provenance hash computation.

#### In Scope
- AnthropicAdapter class implementing AgentAdapter Protocol per TDD §4.2
- `_build_messages()` static method — concatenation of spec, prompt, template, upstream artifacts, current_artifact, correction_constraints
- Lazy `_get_client()` — imports anthropic SDK on first use
- `_DEFAULT_PRICING` table for claude-sonnet, claude-opus, claude-haiku
- `health()` — returns DOWN if API key empty, otherwise tests client init
- `cost_estimate()` — 1 token per 4 chars heuristic
- SHA-256 `input_content_hash` computation

#### Out of Scope / Non-Goals
- No streaming support (NG-7)
- No billing management

#### Inputs
- AgentRequest (spec_content, template_content, prompt_content, upstream_artifacts, current_artifact, correction_constraints)
- ANTHROPIC_API_KEY environment variable

#### Outputs
- `src/adapters/anthropic.py` — Anthropic adapter module

#### Acceptance Criteria (Executable)
- AC1: Given ANTHROPIC_API_KEY is empty, When `health()` is called, Then returns HealthStatus.DOWN. Failure: returns OK without valid key.
- AC2: Given an AgentRequest with all fields populated, When `_build_messages()` is called, Then system_message equals spec_content and user_message contains prompt, template, upstream artifacts, and correction constraints in order. Failure: message content missing or misordered.
- AC3: Given a successful API call, When `invoke()` returns, Then AgentResponse includes content, token counts, cost_usd (from pricing table), latency_ms, and input_content_hash. Failure: missing fields.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_adapters.py — Anthropic section, mock-based)
- [x] Evidence: `src/adapters/anthropic.py` exists; `pytest tests/test_adapters.py -v` passes

#### Interface Contract References
- Provider: TDD §4.1 AgentAdapter Protocol — implements this interface

#### Dependencies
- WDD-HARNESS-001 (uses AgentRequest, AgentResponse, HealthStatus)
- WDD-HARNESS-005 (implements AgentAdapter Protocol)

#### Rollback / Failure Behavior
Revert `src/adapters/anthropic.py`. Anthropic provider becomes unavailable. Other providers continue to function. No state to clean up — adapter is stateless.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-010
- Parent TDD Section: §4.3 OpenAIAdapter
- Assignee Type: AI Agent
- Required Capabilities: backend, API-integration, OpenAI-SDK
- Complexity Estimate: M — Same pattern as Anthropic adapter; lazy SDK, message building, pricing, provenance hash

#### Intent (1-2 sentences)
Implement the OpenAI adapter with lazy SDK initialization, chat completions API integration, per-model pricing tables (gpt-4o, gpt-4o-mini, gpt-4-turbo), and SHA-256 provenance hash. Follows the same message-building pattern as the Anthropic adapter.

#### In Scope
- OpenAIAdapter class implementing AgentAdapter Protocol per TDD §4.3
- `_build_messages()` — identical logic to AnthropicAdapter
- Lazy `_get_client()` — imports openai SDK on first use
- `_DEFAULT_PRICING` for gpt-4o, gpt-4o-mini, gpt-4-turbo
- Chat Completions API call structure (system + user messages)
- Same health, cost_estimate, provenance hash patterns

#### Out of Scope / Non-Goals
- No streaming support (NG-7)
- No assistants API or function calling

#### Inputs
- AgentRequest
- OPENAI_API_KEY environment variable

#### Outputs
- `src/adapters/openai.py` — OpenAI adapter module

#### Acceptance Criteria (Executable)
- AC1: Given OPENAI_API_KEY is empty, When `health()` is called, Then returns HealthStatus.DOWN. Failure: returns OK without valid key.
- AC2: Given a successful API call, When `invoke()` returns, Then AgentResponse includes content from `choices[0].message.content`, prompt_tokens, completion_tokens, cost_usd, and provenance hash. Failure: fields missing or incorrect token field names.
- AC3: Given an unknown model, When cost is calculated, Then default pricing (gpt-4o rates) is used. Failure: KeyError on unknown model.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_adapters.py — OpenAI section, mock-based)
- [x] Evidence: `src/adapters/openai.py` exists; `pytest tests/test_adapters.py -v` passes

#### Interface Contract References
- Provider: TDD §4.1 AgentAdapter Protocol — implements this interface

#### Dependencies
- WDD-HARNESS-001 (uses AgentRequest, AgentResponse, HealthStatus)
- WDD-HARNESS-005 (implements AgentAdapter Protocol)

#### Rollback / Failure Behavior
Revert `src/adapters/openai.py`. OpenAI provider becomes unavailable. Other providers continue to function. No state to clean up.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-011
- Parent TDD Section: §4.4 ToolAdapter
- Assignee Type: AI Agent
- Required Capabilities: backend, subprocess, filesystem-IO
- Complexity Estimate: M — Subprocess execution with temp file management, timeout handling, command-not-found handling; moderate integration surface

#### Intent (1-2 sentences)
Implement the Tool adapter that executes external CLI tools (SAST, linters) via subprocess. Writes artifact content to temp files, passes the path as an argument, captures stdout, and handles timeout and command-not-found gracefully without crashing.

#### In Scope
- ToolAdapter class implementing AgentAdapter Protocol per TDD §4.4
- Temp file creation for artifact content, cleanup in `finally` block
- `subprocess.run()` with `capture_output=True, text=True, timeout=`
- TimeoutExpired and FileNotFoundError handling → graceful AgentResponse (not exception)
- `health()` via `shutil.which()`
- `cost_estimate()` always returns 0.0

#### Out of Scope / Non-Goals
- No interactive tool support
- No tool output parsing (raw stdout returned)

#### Inputs
- Tool name, command, args, timeout configuration
- AgentRequest with current_artifact content

#### Outputs
- `src/adapters/tool.py` — Tool adapter module

#### Acceptance Criteria (Executable)
- AC1: Given a valid tool command, When `invoke()` is called with current_artifact, Then temp file is created, command runs with temp path appended, stdout returned as content, temp file cleaned up. Failure: temp file leaked or command not executed.
- AC2: Given a tool that times out, When `invoke()` is called, Then returns AgentResponse with timeout description in content (no exception raised). Failure: TimeoutExpired propagates.
- AC3: Given a non-existent command, When `invoke()` is called, Then returns AgentResponse with "command not found" in content (no exception raised). Failure: FileNotFoundError propagates.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_adapters.py — ToolAdapter section)
- [x] Evidence: `src/adapters/tool.py` exists; `pytest tests/test_adapters.py -v` passes

#### Interface Contract References
- Provider: TDD §4.1 AgentAdapter Protocol — implements this interface

#### Dependencies
- WDD-HARNESS-001 (uses AgentRequest, AgentResponse, HealthStatus)
- WDD-HARNESS-005 (implements AgentAdapter Protocol)

#### Rollback / Failure Behavior
Revert `src/adapters/tool.py`. Tool-based validation becomes unavailable. Other adapters unaffected. Temp files in OS temp directory may need manual cleanup if crash occurred during development.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-012
- Parent TDD Section: §4.17 Observability Layer
- Assignee Type: AI Agent
- Required Capabilities: backend, JSONL, statistics
- Complexity Estimate: M — JSONL append/read plus 3 aggregation functions (cost summary, provider health, anomaly detection); moderate complexity with time-based filtering

#### Intent (1-2 sentences)
Implement the observability layer that records InvocationRecord entries as JSONL, and provides cost summary aggregation, provider health summary with OK/DEGRADED/DOWN status derivation, and cost anomaly detection using a 3x rolling mean threshold.

#### In Scope
- `record(invocation)` — JSONL append with enum serialization per TDD §4.17
- `read_records(since)` — line-by-line read with optional time filter
- `cost_summary(initiative)` — aggregation by provider and artifact type
- `provider_health_summary()` — per-provider status derivation (0% fail=OK, <50%=DEGRADED, >=50%=DOWN)
- `detect_cost_anomaly(invocation, window_hours)` — 3x mean threshold

#### Out of Scope / Non-Goals
- No external observability platform
- No real-time dashboards

#### Inputs
- InvocationRecord instances
- JSONL log file path

#### Outputs
- `src/observability.py` — observability module with recording and aggregation

#### Acceptance Criteria (Executable)
- AC1: Given an InvocationRecord, When `record()` is called, Then a JSON line is appended to the log file with enum values serialized as strings. Failure: file not created or enum serialization fails.
- AC2: Given a log with records from multiple providers, When `provider_health_summary()` is called, Then per-provider stats include total_invocations, failures, avg_latency_ms, and current_status. Failure: status derivation incorrect.
- AC3: Given an invocation costing 4x the mean for its artifact type, When `detect_cost_anomaly()` is called, Then a warning string is returned. Given normal cost, Then None returned. Failure: anomaly threshold incorrect.
- AC4: Given a `since` datetime, When `read_records(since)` is called, Then only records after that timestamp are returned. Failure: older records included.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_observability.py)
- [x] Evidence: `src/observability.py` exists; `pytest tests/test_observability.py -v` passes

#### Interface Contract References
- None — internal to single component. CLI consumes observability functions directly.

#### Dependencies
- WDD-HARNESS-001 (uses InvocationRecord, LifecycleEvent, RoutingStrategy)

#### Rollback / Failure Behavior
Revert `src/observability.py`. Cost and health reporting unavailable. Existing JSONL log file is not corrupted — append-only writes. CLI health and costs commands will fail.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-013
- Parent TDD Section: §4.20 CLI Functions
- Assignee Type: AI Agent
- Required Capabilities: backend, CLI, argparse, orchestration
- Complexity Estimate: L — 5 subcommands orchestrating all other components; lazy adapter construction, kit file resolution, upstream artifact collection; highest integration surface in the codebase

#### Intent (1-2 sentences)
Implement the CLI entry point with argparse providing 5 subcommands (generate, validate, lifecycle, health, costs). Orchestrates adapter construction via lazy imports, kit file resolution by scanning `aieos-*` directories, upstream artifact collection from SDLC files, and dispatches to the appropriate handler.

#### In Scope
- `main(argv)` — argparse setup with --config and 5 subparsers per TDD §4.20
- `cmd_generate()` — adapter build, kit file resolve, upstream collect, invoke, print
- `cmd_validate()` — artifact read, type inference from filename, validator prompt resolve, invoke
- `cmd_lifecycle()` — generate + validate + "ready for human review" message (never auto-freezes)
- `cmd_health()` — per-adapter health check + historical provider summary
- `cmd_costs()` — cost summary with optional initiative filter
- `_build_adapters()` — lazy SDK import based on config
- `_resolve_kit_files()` — scan aieos-* dirs for spec/template/prompt
- `_collect_upstream_artifacts()` — scan docs/sdlc/*.md for frozen artifacts

#### Out of Scope / Non-Goals
- No auto-freeze (NG-3) — lifecycle prints message only
- No GUI (NG-1)

#### Inputs
- Command-line arguments
- harness.yaml configuration
- AIEOS governance files (read-only)
- Initiative SDLC files (read-only)

#### Outputs
- `src/cli.py` — CLI module with main() and all subcommand handlers

#### Acceptance Criteria (Executable)
- AC1: Given valid args for generate subcommand, When `main(["generate", "--type", "PRD", ...])` is called, Then adapter is built, kit files resolved, request constructed, and response printed. Failure: exception raised or zero output.
- AC2: Given lifecycle subcommand, When generation and validation complete, Then output includes "Artifact ready for human review" and never auto-promotes. Failure: auto-freeze attempted.
- AC3: Given health subcommand with configured adapters, When `main(["health"])` is called, Then per-adapter health status printed. Failure: missing adapters or no output.
- AC4: Given `_resolve_kit_files()` with an AIEOS root containing kit directories, When called with artifact_type "SAD", Then spec, template, and prompt content resolved from the correct kit. Failure: wrong kit or files not found.
- AC5: Given `_collect_upstream_artifacts()` with frozen artifacts in docs/sdlc/, When called, Then dict of artifact_id→content returned for frozen artifacts only. Failure: non-frozen artifacts included.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (tests/test_cli.py)
- [x] Evidence: `src/cli.py` exists; `pytest tests/test_cli.py -v` passes

#### Interface Contract References
- Consumer: All TDD §4 contracts — CLI is the top-level orchestrator consuming all components

#### Dependencies
- WDD-HARNESS-001 (models)
- WDD-HARNESS-002 (config)
- WDD-HARNESS-003 (state — upstream artifact scanning)
- WDD-HARNESS-005 (adapter Protocol + mock)
- WDD-HARNESS-006 (lifecycle binder)
- WDD-HARNESS-007 (routing engine)
- WDD-HARNESS-009 (Anthropic adapter — lazy import)
- WDD-HARNESS-010 (OpenAI adapter — lazy import)
- WDD-HARNESS-012 (observability)

#### Rollback / Failure Behavior
Revert `src/cli.py`. Entire CLI unavailable. No state corruption — CLI is a stateless orchestrator. All component modules remain functional for programmatic use.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-014
- Parent TDD Section: §8 Testing Strategy (Unit Tests)
- Assignee Type: AI Agent
- Required Capabilities: backend, testing, pytest
- Complexity Estimate: L — 133 unit tests across 10 test files covering all modules; each test file tests a different component with positive and negative cases

#### Intent (1-2 sentences)
Implement 133 unit tests across 10 test files covering all source modules. Each module has a corresponding test file with positive (happy path) and negative (failure/edge case) tests. All tests run without API keys using mock adapters.

#### In Scope
- `tests/test_models.py` — enum values, dataclass construction, defaults
- `tests/test_config.py` — YAML loading, defaults, env var overrides
- `tests/test_lifecycle.py` — resolve priority, execute dispatch, no-binding error
- `tests/test_routing.py` — all 4 strategies, circuit breaker states
- `tests/test_convergence.py` — parse, run, staleness, oscillation, correction
- `tests/test_state.py` — ER read/write, frozen scan, journal append/read
- `tests/test_invariants.py` — all 7 checks with positive and negative cases
- `tests/test_observability.py` — record, read, cost summary, health summary, anomaly
- `tests/test_adapters.py` — mock, tool adapters
- `tests/test_cli.py` — arg parsing, kit resolution, upstream collection, adapter build
- `tests/conftest.py` — shared fixtures

#### Out of Scope / Non-Goals
- No real API key tests (gated behind --run-slow)
- No performance benchmarks

#### Inputs
- All source modules in `src/`
- pytest framework

#### Outputs
- 10 test files in `tests/test_*.py`
- `tests/conftest.py` shared fixtures

#### Acceptance Criteria (Executable)
- AC1: Given all tests are run, When `pytest tests/ --ignore=tests/integration -v` is called, Then all 133 unit tests pass with exit code 0. Failure: any test failure or non-zero exit.
- AC2: Given no API keys are set, When unit tests are run, Then all pass (no network calls). Failure: test requires API key.
- AC3: Given each invariant check function, When its test file is run, Then at least one positive (passes) and one negative (fails) test exists. Failure: missing negative test for any check.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Unit tests passing (`pytest tests/ --ignore=tests/integration -v` exit code 0)
- [x] Evidence: 10 test files exist in `tests/`; all pass

#### Interface Contract References
None — internal to single component

#### Dependencies
- WDD-HARNESS-001 through WDD-HARNESS-013 (tests cover all source modules)

#### Rollback / Failure Behavior
Revert test files. Source code unaffected. Test coverage lost — no impact on runtime functionality.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-015
- Parent TDD Section: §8 Testing Strategy (Integration Tests)
- Assignee Type: AI Agent
- Required Capabilities: backend, testing, integration-testing
- Complexity Estimate: L — 30+ integration tests across 4 test files exercising multi-component flows; lifecycle, convergence, multi-provider, and lens orchestration scenarios

#### Intent (1-2 sentences)
Implement 30+ integration tests across 4 test files that exercise full component chains with mock providers. Covers end-to-end lifecycle (generate-validate-present), multi-iteration convergence, multi-provider failover with circuit breaker, and all 4 routing strategy scenarios.

#### In Scope
- `tests/integration/test_single_lifecycle.py` — 10 tests: generate→validate→present flow with invariant enforcement and observability
- `tests/integration/test_convergence_loop.py` — 3 tests: PASS on retry, max iterations, staleness/oscillation
- `tests/integration/test_multi_provider.py` — multi-provider fallback and failover with circuit breaker
- `tests/integration/test_lens_orchestration.py` — 20 tests: all 4 routing strategies, consensus thresholds, cost-aware selection

#### Out of Scope / Non-Goals
- No real API calls — all integration tests use MockAdapter
- No performance or load testing

#### Inputs
- All source modules via MockAdapter configurations
- pytest framework

#### Outputs
- 4 test files in `tests/integration/`

#### Acceptance Criteria (Executable)
- AC1: Given all integration tests are run, When `pytest tests/integration/ -v` is called, Then all tests pass with exit code 0. Failure: any test failure.
- AC2: Given lifecycle integration test, When generate→validate→present flow completes, Then observability record is written and invariant checks pass. Failure: missing observability record or invariant violation.
- AC3: Given convergence integration test with mock validator that fails then passes, When `run()` completes, Then ledger shows iteration progression and final PASS. Failure: incorrect ledger or premature termination.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Integration tests passing (`pytest tests/integration/ -v` exit code 0)
- [x] Evidence: 4 test files exist in `tests/integration/`; all pass

#### Interface Contract References
None — tests exercise cross-component integration

#### Dependencies
- WDD-HARNESS-001 through WDD-HARNESS-013 (integration tests exercise all components)

#### Rollback / Failure Behavior
Revert integration test files. Source code unaffected. Integration test coverage lost — no impact on runtime functionality.

---

### WDD Item
- WDD Item ID: WDD-HARNESS-016
- Parent TDD Section: §9 Operational Notes
- Assignee Type: AI Agent
- Required Capabilities: documentation, technical-writing
- Complexity Estimate: M — 4 documentation files covering architecture, configuration, provider extension, and user-facing README; moderate scope but well-defined content boundaries

#### Intent (1-2 sentences)
Create project documentation: architecture overview (component diagram, data flows), configuration reference (YAML schema, env vars), adding-providers guide (Protocol implementation steps), and README with quickstart, test commands, and project overview.

#### In Scope
- `docs/architecture.md` — component descriptions, layer assignments, data flow diagrams
- `docs/configuration.md` — YAML schema reference, environment variable reference, example configs
- `docs/adding-providers.md` — step-by-step guide for implementing AgentAdapter Protocol
- `README.md` — project overview, quickstart, test commands, structure summary

#### Out of Scope / Non-Goals
- No API reference generation (code is the reference)
- No deployment automation docs (single-operator tool)

#### Inputs
- Source code and TDD for architecture/configuration details
- AgentAdapter Protocol for provider extension guide

#### Outputs
- 3 files in `docs/` + `README.md` at project root

#### Acceptance Criteria (Executable)
- AC1: Given `docs/architecture.md` exists, When read, Then it describes all 13 components with their layer assignments matching TDD §3. Failure: component missing or layer assignment incorrect.
- AC2: Given `docs/configuration.md` exists, When read, Then it documents all HarnessConfig fields, all environment variables, and provides a valid example configuration. Failure: field missing or example invalid.
- AC3: Given `docs/adding-providers.md` exists, When read, Then it lists all 5 AgentAdapter Protocol methods with signatures and provides implementation steps. Failure: method missing or steps incomplete.
- AC4: Given `README.md` exists, When read, Then it includes project description, quickstart commands, and test commands. Failure: missing quickstart or test commands.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Documentation exists and is readable
- [x] Evidence: all 4 files exist at expected paths

#### Interface Contract References
None — documentation artifact

#### Dependencies
- WDD-HARNESS-001 through WDD-HARNESS-015 (documentation describes the implemented system)

#### Rollback / Failure Behavior
Revert documentation files. No impact on runtime functionality. Operator loses reference material.

---

## 3. Work Groups

### Work Group
- Group ID: WG-01
- Group Name: Core Data Layer
- Business Capability: Shared data vocabulary available for all components to build against
- Member Items: WDD-HARNESS-001
- Acceptance Criteria (Group-Level): All 5 enums and 7 dataclasses importable and usable by downstream components; unit tests pass.

### Work Group
- Group ID: WG-02
- Group Name: Configuration and State
- Business Capability: System can load configuration from YAML/env vars and read/write governance state from Markdown files on disk
- Member Items: WDD-HARNESS-002, WDD-HARNESS-003
- Acceptance Criteria (Group-Level): `load_config()` returns valid HarnessConfig; ER state block round-trips correctly; frozen artifact scanning returns accurate results; journal entries persist and parse.

### Work Group
- Group ID: WG-03
- Group Name: Governance Enforcement
- Business Capability: All 7 AIEOS structural invariants are programmatically enforced before any generation or validation proceeds
- Member Items: WDD-HARNESS-004
- Acceptance Criteria (Group-Level): Each of the 7 invariant checks correctly identifies violations; UPSTREAM_DEPENDENCIES map covers 30+ artifact types; no generation proceeds with missing frozen upstream.

### Work Group
- Group ID: WG-04
- Group Name: Adapter Layer
- Business Capability: Pluggable provider interface with concrete adapters for Anthropic, OpenAI, external tools, and test mocking
- Member Items: WDD-HARNESS-005, WDD-HARNESS-009, WDD-HARNESS-010, WDD-HARNESS-011
- Acceptance Criteria (Group-Level): All 4 adapters satisfy `isinstance(adapter, AgentAdapter)` at runtime; mock adapter supports test scenarios; tool adapter handles timeout and command-not-found gracefully; LLM adapters produce provenance hashes.

### Work Group
- Group ID: WG-05
- Group Name: Orchestration Engine
- Business Capability: Lifecycle events dispatch to adapters via configurable bindings; requests route across providers with failover and circuit breaking; failed validations auto-converge within bounded iterations
- Member Items: WDD-HARNESS-006, WDD-HARNESS-007, WDD-HARNESS-008
- Acceptance Criteria (Group-Level): Lifecycle binder resolves exact matches before wildcards; routing engine executes all 4 strategies correctly; convergence loop respects max_iterations bound and detects staleness/oscillation.

### Work Group
- Group ID: WG-06
- Group Name: Observability and CLI
- Business Capability: Operator can invoke all harness capabilities via CLI and inspect cost, health, and invocation history
- Member Items: WDD-HARNESS-012, WDD-HARNESS-013
- Acceptance Criteria (Group-Level): All 5 CLI subcommands execute successfully; JSONL log records invocations; cost summary and health summary return accurate aggregations; cost anomaly detection flags outliers.

### Work Group
- Group ID: WG-07
- Group Name: Test Suite
- Business Capability: Complete automated verification of all components with 166 tests passing without API keys
- Member Items: WDD-HARNESS-014, WDD-HARNESS-015
- Acceptance Criteria (Group-Level): `pytest -v` returns exit code 0 with 166 tests passing; no test requires network access or API keys; integration tests exercise full component chains.

### Work Group
- Group ID: WG-08
- Group Name: Documentation
- Business Capability: Operator has reference material for architecture, configuration, provider extension, and quickstart
- Member Items: WDD-HARNESS-016
- Acceptance Criteria (Group-Level): All 4 documentation files exist and accurately describe the implemented system.

---

## 4. Freeze Declaration (when ready)
This WDD is approved and frozen. Execution may proceed.

- Approved By: Todd Linnertz
- Date: 2026-03-26
