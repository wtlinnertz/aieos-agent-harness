# TDD: AIEOS Agent Harness

## 0. document control
- System / Component Name: AIEOS Agent Harness (ECO-009)
- TDD ID: TDD-HARNESS-001
- Author: Todd Linnertz (extracted from existing codebase by AI)
- Date: 2026-03-26
- Status: Draft
- Governance Model Version: 1.3
- Prompt Version: tdd-prompt v1.0
- Spec Version: tdd-spec v1.0
- Principles Version: N/A (no principles files exist for this system project; PRD-HARNESS-001 Section 6 Constraints serves as the guardrail source)
- Upstream Artifacts:
  - SAD ID / Link: SAD-HARNESS-001 (docs/sdlc/05-sad.md)
  - ACF ID / Link: ACF-HARNESS-001 (docs/sdlc/04-acf.md)
  - DCF ID / Link: N/A (retroactive governance; no DCF produced)
- Related ADRs: None (architectural decisions embedded in SAD §5)

**Note:** This TDD is retroactive. All technical design descriptions reflect the implemented codebase (16 source files, 166 tests). No DCF exists because this is an system software project governed retroactively.

---

## 1. intent summary

From SAD-HARNESS-001:

- The system automates the AIEOS artifact generate-validate cycle via CLI, resolving governance files (spec, template, prompt, validator) from the AIEOS framework filesystem and dispatching to configured AI provider adapters
- All 7 AIEOS structural invariants are enforced programmatically on every invocation, removing reliance on operator memory
- Requests are routed across multiple AI providers using 4 strategies (fallback, pipeline, parallel_consensus, cost_aware) with circuit breaker protection and automatic failover
- Failed validations trigger bounded convergence loops (default 3 iterations) with staleness and oscillation detection
- Every invocation records cost, latency, token usage, and result to a persistent JSONL observability log with aggregation queries
- The system is CLI-only (NG-1), stores all state on disk as Markdown and JSONL (NG-2), never auto-freezes artifacts (NG-3), and consumes governance files read-only (NG-4)
- Provider adapters implement a Protocol-based plugin interface with lazy SDK initialization; concrete implementations exist for Anthropic Claude, OpenAI, external CLI tools, and a mock test double
- Configuration is loaded from YAML with environment variable overrides; API keys are sourced exclusively from environment variables
- The harness reads Engagement Record state blocks and Sherpa Journal entries from Markdown files on disk, and writes updates via regex-based in-place replacement or append-only operations
- State management, convergence logic, and invariant enforcement use only the Python standard library; provider SDKs are optional and lazy-loaded

---

## 2. scope and non-Goals (Hard boundary)

### In scope

- Lifecycle event binding: map lifecycle events to adapter invocations via YAML-configured EventBinding dataclasses
- Multi-strategy routing engine with 4 strategies and per-provider CircuitBreaker
- Bounded convergence loop with JSON extraction, staleness detection, and oscillation detection
- 7 programmatic invariant checks with a 30+ entry upstream dependency map
- Provider adapter layer: AgentAdapter Protocol with 4 concrete implementations (Anthropic, OpenAI, Tool, Mock)
- Disk-based state management for ER state blocks (read/write via regex) and Sherpa Journal (append-only)
- Per-invocation JSONL observability with cost summary, provider health summary, and cost anomaly detection
- CLI with 5 subcommands: generate, validate, lifecycle, health, costs
- Configuration: YAML loading via yaml.safe_load with environment variable overrides for AIEOS_ROOT, AIEOS_INITIATIVE_ROOT, and API keys

### Explicit non-Goals (Must align with SAD)

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

## 3. technical overview

### Technology stack

- **Language:** Python 3.11+
- **Module System:** Python packages (`src/` with `src/adapters/` subpackage)
- **Configuration:** PyYAML >= 6.0 for YAML parsing
- **AI Providers:** Anthropic SDK >= 0.40 (optional, lazy-imported), OpenAI SDK >= 1.50 (optional, lazy-imported)
- **Testing:** pytest >= 8.0
- **Concurrency:** `concurrent.futures.ThreadPoolExecutor` for parallel_consensus fan-out
- **Persistence:** Filesystem only — Markdown + JSONL, no database
- **CLI:** Python argparse

### Components

**Data Models** (`src/models.py`) — 7 dataclasses and 6 enums defining the shared vocabulary across all components. Zero external dependencies.

**Config Loader** (`src/config.py`) — Loads `harness.yaml` via `yaml.safe_load`, builds typed `HarnessConfig` with nested `ProviderConfig` and `RoutingConfig` dataclasses, applies `AIEOS_ROOT` and `AIEOS_INITIATIVE_ROOT` environment variable overrides.

**Lifecycle Binder** (`src/lifecycle.py`) — Maps `(LifecycleEvent, artifact_type)` pairs to adapter invocations. Resolves exact artifact type matches before wildcard (`"*"`) bindings. Executes single-adapter dispatch.

**Routing Engine** (`src/routing.py`) — Dispatches requests through adapters using 4 strategies. Maintains per-provider `CircuitBreaker` state. Uses `ThreadPoolExecutor` for parallel consensus fan-out.

**Convergence Loop** (`src/convergence.py`) — Bounded generate-validate loop. Extracts JSON from fenced code blocks or raw content. Detects staleness and oscillation patterns. Builds correction requests from blocking issues.

**State Manager** (`src/state.py`) — Reads/writes ER state blocks via regex-based Markdown table parsing. Scans `docs/sdlc/*.md` for frozen artifacts. Appends/reads Sherpa Journal entries as Markdown sections.

**Invariant Enforcer** (`src/invariants.py`) — 7 pure-function invariant checks. Maintains a 30+ entry `UPSTREAM_DEPENDENCIES` map. Scans for suggestion language and provider-specific terms via compiled regex patterns.

**Observability Layer** (`src/observability.py`) — Records `InvocationRecord` as JSONL. Provides cost summary, provider health summary (OK/DEGRADED/DOWN thresholds), and cost anomaly detection (3x rolling mean).

**CLI** (`src/cli.py`) — Entry point with argparse. 5 subcommands. Builds adapters from config with lazy SDK imports. Resolves kit files by scanning `aieos-*` directories.

**AgentAdapter Protocol** (`src/adapters/base.py`) — `@runtime_checkable` Protocol defining the adapter contract: `provider_name`, `model_name`, `invoke()`, `health()`, `cost_estimate()`.

**Anthropic Adapter** (`src/adapters/anthropic.py`) — Claude Messages API integration. Builds system/user messages from AgentRequest. Per-model pricing tables. SHA-256 provenance hash. Lazy SDK client initialization.

**OpenAI Adapter** (`src/adapters/openai.py`) — Chat Completions API integration. Same message-building pattern and provenance hash as Anthropic. Lazy SDK client initialization.

**Tool Adapter** (`src/adapters/tool.py`) — Subprocess execution for SAST/linters. Writes artifact content to temp file, passes path as argument, captures stdout. Handles timeout and command-not-found. Cleans up temp files in `finally` block.

**Mock Adapter** (`src/adapters/mock.py`) — Test double. Preset responses per artifact type, configurable health status, configurable failure, call history tracking.

### Key data flows

1. **Generate flow:** CLI parses args → `load_config()` → `_build_adapters()` → `_resolve_kit_files()` scans `aieos-*` dirs → `_collect_upstream_artifacts()` scans `docs/sdlc/*.md` → builds `AgentRequest` → `adapter.invoke()` → prints `AgentResponse`
2. **Lifecycle flow:** Generate flow + build validation request with `current_artifact = gen_response.content` → separate `adapter.invoke()` → print validation result → present for human freeze
3. **Convergence flow:** `ConvergenceLoop.run()` iterates: generate → validate → parse JSON → if FAIL: detect staleness/oscillation → `_build_correction_request()` → re-generate → repeat until PASS or max_iterations
4. **Routing flow:** `RoutingEngine.route()` dispatches to strategy method → strategy invokes adapter(s) with circuit breaker checks → returns `AgentResponse`

### Layer assignment (from SAD §4)

| Component | Layer | Language/Framework | Dependency Constraints |
|-----------|-------|--------------------|----------------------|
| Data Models (`src/models.py`) | Domain | Python 3.11+ (dataclasses, enum) | May not depend on any other component or external library |
| Invariant Enforcer (`src/invariants.py`) | Domain | Python 3.11+ (json, re) | May depend on Data Models and State Manager read interface only; may not depend on adapters, routing, or config |
| Convergence Loop (`src/convergence.py`) | Domain | Python 3.11+ (json, re) | May depend on Data Models and AgentAdapter Protocol (abstraction) only; may not depend on concrete adapters |
| AgentAdapter Protocol (`src/adapters/base.py`) | Application | Python 3.11+ (Protocol) | May depend on Data Models only; defines interface that infrastructure implements |
| Lifecycle Binder (`src/lifecycle.py`) | Application | Python 3.11+ | May depend on AgentAdapter Protocol and Data Models; may not depend on concrete adapters |
| Routing Engine (`src/routing.py`) | Application | Python 3.11+ (concurrent.futures) | May depend on AgentAdapter Protocol and Data Models; may not depend on concrete adapters |
| Config Loader (`src/config.py`) | Application | Python 3.11+ + PyYAML | May depend on Data Models; may not depend on adapters or domain logic |
| Observability Layer (`src/observability.py`) | Application | Python 3.11+ (json, statistics) | May depend on Data Models only; may not depend on adapters or domain logic |
| State Manager (`src/state.py`) | Application | Python 3.11+ (re, pathlib) | May depend on Data Models; may not depend on adapters, routing, or config. Spans Application/Infrastructure due to Markdown file I/O |
| CLI (`src/cli.py`) | Infrastructure | Python 3.11+ (argparse) | May depend on all Application and Domain components. Top-level orchestrator |
| Anthropic Adapter (`src/adapters/anthropic.py`) | Infrastructure | Python 3.11+ + Anthropic SDK | Implements AgentAdapter Protocol. May depend on Data Models. SDK lazy-imported |
| OpenAI Adapter (`src/adapters/openai.py`) | Infrastructure | Python 3.11+ + OpenAI SDK | Implements AgentAdapter Protocol. May depend on Data Models. SDK lazy-imported |
| Tool Adapter (`src/adapters/tool.py`) | Infrastructure | Python 3.11+ (subprocess, tempfile) | Implements AgentAdapter Protocol. May depend on Data Models |
| Mock Adapter (`src/adapters/mock.py`) | Infrastructure | Python 3.11+ | Implements AgentAdapter Protocol. May depend on Data Models. Test infrastructure only |

**Dependency Direction Rule:** Dependencies point inward only. Domain depends on nothing. Application defines interfaces; Infrastructure implements them. The State Manager's span across Application/Infrastructure is documented in SAD §4 as an accepted deviation — no separate persistence abstraction exists because Markdown files are the only persistence mechanism by design (NG-2, C-5).

---

## 4. interfaces and contracts (Hard)

### 4.1 agentAdapter protocol (`src/adapters/base.py`)

**SAD Component:** Adapter Protocol

```
@runtime_checkable
class AgentAdapter(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def invoke(self, request: AgentRequest) -> AgentResponse: ...

    def health(self) -> HealthStatus: ...

    def cost_estimate(self, request: AgentRequest) -> float: ...
```

- **Inputs:**
  - `invoke(request: AgentRequest)` — see AgentRequest dataclass (§4.18)
  - `health()` — no arguments
  - `cost_estimate(request: AgentRequest)` — same AgentRequest
- **Outputs:**
  - `invoke` → `AgentResponse` (content, provider, model, tokens_in, tokens_out, cost_usd, latency_ms, raw_response, provenance fields)
  - `health` → `HealthStatus` enum (OK, DEGRADED, DOWN)
  - `cost_estimate` → `float` (estimated cost in USD)
- **Error modes:**
  - `invoke` may raise `RuntimeError` (configured to fail), `ImportError` (SDK not installed), provider-specific API errors (auth, rate limit, timeout)
  - `health` and `cost_estimate` must not raise; return fallback values on failure
- **Backward compatibility:** This is an internal protocol. No external consumers. Protocol shape changes require updating all 4 adapter implementations.

---

### 4.2 anthropicAdapter (`src/adapters/anthropic.py`)

**SAD Component:** Anthropic Adapter

- **Constructor:** `__init__(self, model: str = "claude-sonnet-4-20250514", max_tokens: int = 8192, api_key: str | None = None)`
  - `api_key` falls back to `os.environ.get("ANTHROPIC_API_KEY", "")`
  - SDK client (`self._client`) initialized to `None` — lazy init on first `_get_client()` call

- **`_build_messages(request: AgentRequest) -> tuple[str, str]`** (static method)
  - System message = `request.spec_content`
  - User message = concatenation of: `request.prompt_content` + `"\n\n## Template\n\n" + request.template_content` + per-upstream `"\n\n## {artifact_id}\n\n{content}"` + (if `current_artifact`): `"\n\n## Current Artifact\n\n" + content` + (if `correction_constraints`): `"\n\n## Correction Constraints\n\n"` + bulleted list
  - Returns `(system_message, user_message)`

- **`invoke(request: AgentRequest) -> AgentResponse`**
  - Calls `_get_client()` (lazy init via `anthropic.Anthropic(api_key=...)`)
  - Calls `client.messages.create(model=, max_tokens=, system=, messages=[{role: "user", content: user_message}])`
  - Extracts content from `response.content` blocks (concatenates `.text` attributes)
  - Reads `response.usage.input_tokens` and `response.usage.output_tokens`
  - Computes `cost_usd` from `_DEFAULT_PRICING` per-model table (per 1K tokens): `(tokens_in / 1000) * pricing["input"] + (tokens_out / 1000) * pricing["output"]`
  - Computes `input_content_hash` as SHA-256 hex digest of `spec_content + template_content + prompt_content + concatenated upstream artifact values`
  - Returns `AgentResponse` with all fields populated

- **`_DEFAULT_PRICING`:**
  - `"claude-sonnet-4-20250514"`: input=0.003, output=0.015
  - `"claude-opus-4-20250514"`: input=0.015, output=0.075
  - `"claude-haiku-3-20250307"`: input=0.00025, output=0.00125
  - Default fallback (unknown model): input=0.003, output=0.015

- **`health() -> HealthStatus`:** Returns `DOWN` if `_api_key` is empty string. Otherwise attempts `_get_client()` — returns `OK` on success, `DOWN` on exception.

- **`cost_estimate(request: AgentRequest) -> float`:** Heuristic: 1 token per 4 chars. `estimated_input_tokens = (len(system_message) + len(user_message)) / 4`. `estimated_output_tokens = min(estimated_input_tokens * 2, max_tokens)`. Applies pricing table.

- **Error modes:**
  - `ImportError` if `anthropic` package not installed (raised on first `_get_client()`)
  - `anthropic.AuthenticationError` if API key invalid
  - `anthropic.RateLimitError` if rate limited
  - `anthropic.APIConnectionError` / `anthropic.APITimeoutError` for network issues

---

### 4.3 openAIAdapter (`src/adapters/openai.py`)

**SAD Component:** OpenAI Adapter

- **Constructor:** `__init__(self, model: str = "gpt-4o", max_tokens: int = 8192, api_key: str | None = None)`
  - `api_key` falls back to `os.environ.get("OPENAI_API_KEY", "")`
  - Lazy client init same as Anthropic

- **`_build_messages(request: AgentRequest) -> tuple[str, str]`** — identical logic to AnthropicAdapter._build_messages

- **`invoke(request: AgentRequest) -> AgentResponse`**
  - Calls `client.chat.completions.create(model=, max_tokens=, messages=[{role: "system", content: system_message}, {role: "user", content: user_message}])`
  - Extracts `response.choices[0].message.content`
  - Reads `response.usage.prompt_tokens` and `response.usage.completion_tokens`
  - Cost and provenance hash computation identical to Anthropic
  - `raw_response` includes `id` and `finish_reason` (vs. Anthropic's `stop_reason`)

- **`_DEFAULT_PRICING`:**
  - `"gpt-4o"`: input=0.005, output=0.015
  - `"gpt-4o-mini"`: input=0.00015, output=0.0006
  - `"gpt-4-turbo"`: input=0.01, output=0.03
  - Default fallback: input=0.005, output=0.015

- **Error modes:** Same pattern as Anthropic — `ImportError`, auth errors, rate limits, network timeouts

---

### 4.4 toolAdapter (`src/adapters/tool.py`)

**SAD Component:** Tool Adapter

- **Constructor:** `__init__(self, name: str, command: str, args: list[str] | None = None, timeout: int = 300)`

- **`invoke(request: AgentRequest) -> AgentResponse`**
  - Builds command: `[self._command] + self._args`
  - If `request.current_artifact` is set: writes to `tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, prefix=f"harness_{request.artifact_type}_")`, appends temp file path to command
  - Calls `subprocess.run(cmd, capture_output=True, text=True, timeout=self._timeout)`
  - Content = `result.stdout` (plus `"\n\nSTDERR:\n" + result.stderr` if `returncode != 0` and stderr non-empty)
  - `tokens_in = 0`, `tokens_out = 0`, `cost_usd = 0.0`
  - `raw_response = {"returncode": result.returncode, "stderr": result.stderr}`
  - `finally` block: `tmp_file.unlink()` if temp file exists
  - On `subprocess.TimeoutExpired`: returns response with content = `f"Tool '{name}' timed out after {timeout}s"`, `raw_response = {"error": "timeout"}`
  - On `FileNotFoundError`: returns response with content = `f"Tool '{name}' command not found: {command}"`, `raw_response = {"error": "command_not_found"}`

- **`health() -> HealthStatus`:** Returns `OK` if `shutil.which(self._command)` is not None, else `DOWN`.

- **`cost_estimate(request: AgentRequest) -> float`:** Always returns `0.0`.

- **Error modes:** Timeout and command-not-found are handled gracefully (return AgentResponse, do not raise). Other subprocess errors propagate.

---

### 4.5 mockAdapter (`src/adapters/mock.py`)

**SAD Component:** Mock Adapter

- **Constructor:** `__init__(self, provider_name: str = "mock-provider", model_name: str = "mock-model-v1", preset_responses: dict[str, str] | None = None, health_status: HealthStatus = HealthStatus.OK, should_fail: bool = False, failure_message: str = "Mock adapter configured to fail")`

- **`invoke(request: AgentRequest) -> AgentResponse`**
  - Appends `("invoke", (request,))` to `self.call_history`
  - If `should_fail`: raises `RuntimeError(failure_message)`
  - Content = `preset_responses.get(request.artifact_type, f"Mock response for {request.artifact_type}")`
  - Fixed values: `tokens_in=100`, `tokens_out=200`, `cost_usd=0.001`, `latency_ms=50.0`
  - Computes same SHA-256 `input_content_hash` as real adapters

- **`health() -> HealthStatus`:** Returns `self._health_status`. Records in `call_history`.

- **`cost_estimate(request: AgentRequest) -> float`:** Returns `0.001`. Records in `call_history`.

---

### 4.6 lifecycleBinder (`src/lifecycle.py`)

**SAD Component:** Lifecycle Binder

- **Constructor:** `__init__(self, bindings: list[EventBinding], adapters: dict[str, AgentAdapter])`

- **`resolve(event: LifecycleEvent, artifact_type: str) -> list[tuple[AgentAdapter, RoutingStrategy, dict]]`**
  - Iterates all bindings matching `event`
  - Builds two lists: `exact` (binding.artifact_type == artifact_type) and `wildcard` (binding.artifact_type == "*")
  - For each matching binding, looks up each `adapter_name` in `self._adapters`; appends `(adapter, binding.strategy, binding.config)` if found
  - Returns `exact` if non-empty, else `wildcard`

- **`execute(event: LifecycleEvent, request: AgentRequest) -> AgentResponse`**
  - Calls `resolve(event, request.artifact_type)`
  - If empty: raises `RuntimeError(f"No binding found for event={event.value} artifact_type={request.artifact_type}")`
  - Takes first resolved tuple `(adapter, _, _)`, calls `adapter.invoke(request)`

- **Error modes:**
  - `RuntimeError` if no binding matches
  - Propagates any exception from `adapter.invoke()`

---

### 4.7 eventBinding dataclass (`src/lifecycle.py`)

- `event: LifecycleEvent` — which lifecycle event this binding handles
- `artifact_type: str` — `"*"` for all types, or specific type (e.g., `"SAD"`)
- `adapter_names: list[str]` — names referencing adapters in the adapter registry (default: `[]`)
- `strategy: RoutingStrategy` — routing strategy for this binding (default: `FALLBACK`)
- `config: dict` — strategy-specific config (e.g., `{"threshold": 0.67}`) (default: `{}`)

---

### 4.8 circuitBreaker (`src/routing.py`)

**SAD Component:** Routing Engine (circuit breaker subcomponent)

- **Constructor:** `__init__(self, max_failures: int = 3, reset_seconds: float = 60.0)`
  - Internal state: `_failures: dict[str, int]` (per-provider failure count), `_open_since: dict[str, float]` (per-provider open timestamp from `time.monotonic()`)

- **`record_failure(provider: str) -> None`:** Increments `_failures[provider]`. If count >= `max_failures`, sets `_open_since[provider] = time.monotonic()` (opens circuit).

- **`record_success(provider: str) -> None`:** Removes provider from both `_failures` and `_open_since` (closes circuit).

- **`is_open(provider: str) -> bool`:** If provider not in `_open_since`: returns `False`. If `time.monotonic() - opened_at >= reset_seconds`: clears both dicts for provider (half-open → retry), returns `False`. Otherwise returns `True`.

---

### 4.9 routingEngine (`src/routing.py`)

**SAD Component:** Routing Engine

- **Constructor:** `__init__(self, circuit_breaker: CircuitBreaker | None = None)` — defaults to `CircuitBreaker()` if not provided

- **`route(strategy: RoutingStrategy, adapters: list[AgentAdapter], request: AgentRequest, config: dict) -> AgentResponse`**
  - Dispatch table maps `RoutingStrategy` enum values to private strategy methods
  - Raises `ValueError(f"Unknown routing strategy: {strategy}")` for unknown strategy

- **`_fallback(adapters, request, config) -> AgentResponse`**
  - Iterates adapters in order. Skips if `circuit_breaker.is_open(provider_name)` (appends "circuit breaker open" to errors list).
  - Calls `adapter.invoke(request)`. On success: `record_success(provider)`, return response.
  - On exception: `record_failure(provider)`, append error, continue to next.
  - If all fail: raises `RuntimeError(f"All adapters failed in fallback chain: {errors}")`

- **`_pipeline(adapters, request, config) -> AgentResponse`**
  - Requires at least one adapter (raises `ValueError` otherwise)
  - Invokes adapters sequentially. Each step's `response.content` becomes `current_artifact` in the next step's request (new `AgentRequest` constructed with all other fields from original)
  - On step failure: raises `RuntimeError(f"Pipeline step {i} ({provider_name}) failed: {exc}")`
  - Returns final response

- **`_parallel_consensus(adapters, request, config) -> AgentResponse`**
  - `threshold = config.get("threshold", 0.67)`
  - Creates `ThreadPoolExecutor(max_workers=len(adapters))`
  - Submits all `adapter.invoke(request)` as futures
  - Collects responses and errors via `as_completed()`
  - If no responses: raises `RuntimeError(f"All adapters failed in parallel consensus: {errors}")`
  - Computes agreement: `_count_agreeing(responses)` / total (responses + errors)
  - If agreement >= threshold: returns `responses[0]`
  - Else: raises `ValueError(f"Parallel consensus disagreement: {agreement_count}/{total} agreed (threshold={threshold})")`

- **`_count_agreeing(responses) -> int`** (static method)
  - Agreement = content lengths within 20% of median length
  - `median_length = sorted(lengths)[len(lengths) // 2]`
  - `tolerance = 0.20`
  - Counts responses where `abs(length - median_length) / median_length <= tolerance`
  - Edge case: if `median_length == 0`, counts responses with length 0

- **`_cost_aware(adapters, request, config) -> AgentResponse`**
  - `min_tier = config.get("min_tier")` — optional minimum cost threshold
  - Calls `adapter.cost_estimate(request)` for each adapter; filters out those below `min_tier` if set
  - Sorts remaining by cost ascending
  - Invokes in sorted order with circuit breaker checks (same pattern as fallback)
  - If all fail: raises `RuntimeError(f"All adapters failed in cost-aware routing: {errors}")`

- **Error modes:**
  - `ValueError` for unknown strategy, pipeline with no adapters, consensus disagreement
  - `RuntimeError` for all-adapters-failed in fallback, parallel_consensus, cost_aware

---

### 4.10 convergenceLoop (`src/convergence.py`)

**SAD Component:** Convergence Loop

- **Constructor:** `__init__(self, generate_adapter: AgentAdapter, validate_adapter: AgentAdapter, max_iterations: int = 3)`

- **`run(gen_request: AgentRequest, val_request: AgentRequest) -> tuple[AgentResponse, ValidationResult, ConvergenceState]`**
  - Initializes `ConvergenceState(artifact_id=gen_request.metadata.get("artifact_id", "unknown"), artifact_type=gen_request.artifact_type, max_iterations=self._max)`
  - For each iteration `[0, max_iterations)`:
    1. `state.current_iteration = iteration + 1`
    2. Generate: `gen_response = self._gen.invoke(current_gen_request)`
    3. Validate: builds new `AgentRequest` from `val_request` fields with `current_artifact = gen_response.content`, calls `self._val.invoke(validation_request)` — **separate invoke() call, no shared session state**
    4. Parse: `result = parse_validation_result(val_response)`
    5. Record ledger entry: `{"iteration", "status", "hard_gates", "blocking_issues", "completeness_score"}`
    6. If `result.status == "PASS"`: return `(gen_response, result, state)`
    7. If FAIL and `iteration < max - 1`: check staleness/oscillation (log warnings), build correction request, set `current_gen_request` to corrected request
  - If max iterations reached: return `(gen_response, result, state)` — result.status will be "FAIL", indicating escalation needed

- **Error modes:**
  - Propagates adapter exceptions from `invoke()` calls
  - `ValueError` from `parse_validation_result()` if validation response contains no valid JSON or missing required fields

---

### 4.11 parse_validation_result (`src/convergence.py`)

- **Input:** `response: AgentResponse`
- **Output:** `ValidationResult`
- **Algorithm:**
  1. Strip `response.content`
  2. Try fenced JSON: regex `r"```json\s*\n(.*?)\n\s*```"` with `re.DOTALL`
  3. If no fence: try raw JSON: regex `r"\{.*\}"` with `re.DOTALL`
  4. If no JSON found: raise `ValueError(f"No JSON found in validation response: {content[:200]}")`
  5. Parse via `json.loads(raw)`. If invalid: raise `ValueError(f"Invalid JSON in validation response: {exc}")`
  6. Check required fields: `{"status", "summary", "hard_gates", "blocking_issues"}`. If missing: raise `ValueError(f"Validation JSON missing required fields: {missing}")`
  7. Construct `ValidationResult` with `completeness_score = int(data.get("completeness_score", 0))`

---

### 4.12 _detect_staleness (`src/convergence.py`)

- **Input:** `state: ConvergenceState`
- **Output:** `bool`
- **Algorithm:** Requires >= 2 ledger entries. Finds gates that FAIL in both last 2 entries. For each common failed gate, checks if `blocking_issues` have the same `description`. Returns `True` if any gate has identical description in both entries.

---

### 4.13 _detect_oscillation (`src/convergence.py`)

- **Input:** `state: ConvergenceState`
- **Output:** `bool`
- **Algorithm:** Requires >= 3 ledger entries. Takes last 3 entries. For each gate that FAIL in entry[0], checks if it PASS in entry[1] (not in failed set) and FAIL again in entry[2]. Returns `True` if any gate follows this flip-flop pattern.

---

### 4.14 _build_correction_request (`src/convergence.py`)

- **Input:** `original: AgentRequest, blocking_issues: list[dict]`
- **Output:** `AgentRequest` (clone with appended correction constraints)
- **Algorithm:** For each blocking issue, formats as `"[{gate}] {description}"` (plus `" (at: {location})"` if location present). Appends all to a copy of `original.correction_constraints`. Returns new `AgentRequest` with all other fields from `original`.

---

### 4.15 state manager functions (`src/state.py`)

**SAD Component:** State Manager

**`read_er_state_block(er_path: Path) -> ERStateBlock`**
- Reads file text. Extracts each field using regex: `r"\|\s*{field_name}\s*\|\s*(.*?)\s*\|"` with `re.IGNORECASE`
- Fields extracted: Current Layer, Current Artifact, Current Step, Frozen Count (parsed to int, default "0"), Next Action, Blocking On, Last Updated
- Returns `ERStateBlock` dataclass

**`write_er_state_block(er_path: Path, state: ERStateBlock) -> None`**
- Reads file text. For each field, applies regex substitution: `r"(\|\s*{field_name}\s*\|)\s*.*?\s*\|"` → `r"\1 {value} |"` with `re.IGNORECASE`
- Writes modified text back to same file (in-place replacement)

**`read_frozen_artifacts(initiative_path: Path) -> dict[str, ArtifactStatus]`**
- Scans `initiative_path / "docs" / "sdlc" / "*.md"` (sorted glob)
- For each file: extracts `Artifact ID` field via regex `r"\|\s*Artifact\s+ID\s*\|\s*(.*?)\s*\|"`, extracts `Status` field via regex `r"\|\s*Status\s*\|\s*(.*?)\s*\|"`
- Normalizes status: `.strip().upper().replace(" ", "_")` then maps to `ArtifactStatus` enum (falls back to `DRAFT` on `ValueError`)
- Returns `{artifact_id: ArtifactStatus}` mapping

**`is_artifact_frozen(initiative_path: Path, artifact_id: str) -> bool`**
- Calls `read_frozen_artifacts()`, checks if `artifacts.get(artifact_id) == ArtifactStatus.FROZEN`

**`append_journal_entry(journal_path: Path, entry_type: str, fields: dict) -> None`**
- Generates UTC timestamp: `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`
- Writes formatted Markdown section: `### {entry_type}: {timestamp}` followed by `| Field | Value |` table with field/value rows
- Opens file in append mode (`"a"`)

**`read_journal_entries(journal_path: Path) -> list[dict]`**
- Reads full file text
- Splits on `### ` headers using regex: `r"###\s+(.+?)\s+--\s+(\S+)"`
- For each header: extracts `entry_type` and `timestamp`, then parses table rows in the section between this header and the next
- Table row regex: `r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|"` — skips rows where key is "Field", "-------", "---", or starts with "---"
- Returns list of dicts, each containing `entry_type`, `timestamp`, and all parsed field/value pairs

- **Error modes:**
  - `read_er_state_block`: Returns empty strings / 0 for fields not found (no exception)
  - `write_er_state_block`: Fields not matching the regex are silently not updated
  - `read_frozen_artifacts`: Returns empty dict if `docs/sdlc/` does not exist. Silently skips files without Artifact ID or Status fields
  - `read_journal_entries`: Raises `FileNotFoundError` if journal path does not exist

---

### 4.16 invariant check functions (`src/invariants.py`)

**SAD Component:** Invariant Enforcer

All functions return `InvariantCheck(name: str, passed: bool, reason: str)`.

**1. `check_generation_validation_separation(gen_event: LifecycleEvent, val_event: LifecycleEvent) -> InvariantCheck`**
- `passed = gen_event != val_event`
- Fails if same event used for both generation and validation

**2. `check_freeze_before_promote(initiative_path: Path, artifact_type: str, upstream_deps: dict[str, list[str]]) -> InvariantCheck`**
- Looks up `required = upstream_deps.get(artifact_type, [])`
- If no requirements: PASS with reason "No upstream dependencies"
- Calls `state_module.read_frozen_artifacts(initiative_path)` to get frozen artifacts
- Extracts type prefixes from artifact IDs by splitting on `"-"` and taking first part
- Checks all required types are in frozen types set
- FAIL if any missing: reason = `f"Missing frozen upstream artifacts: {missing}"`

**3. `check_human_freeze_decision(auto_freeze_attempted: bool) -> InvariantCheck`**
- `passed = not auto_freeze_attempted`
- Fails if auto_freeze_attempted is True

**4. `check_bounded_convergence(cstate: ConvergenceState) -> InvariantCheck`**
- `passed = cstate.current_iteration <= cstate.max_iterations`
- Fails if iteration exceeds max

**5. `check_validator_output_format(response_text: str) -> tuple[InvariantCheck, ValidationResult | None]`**
- Attempts `json.loads(response_text)`. FAIL if invalid JSON.
- Checks required fields: `["status", "summary", "hard_gates", "blocking_issues", "warnings", "completeness_score"]`. FAIL if any missing.
- Checks `status` is "PASS" or "FAIL". FAIL otherwise.
- Checks `summary` against `_SUGGESTION_PATTERNS`: regex `r"\b(consider|suggest|recommend|you might|you could|try|perhaps)\b"` (case-insensitive). FAIL if suggestion language found.
- On PASS: returns `(InvariantCheck(passed=True), ValidationResult)`

**6. `check_tool_agnostic_policy(content: str) -> InvariantCheck`**
- Scans content against `_TOOL_SPECIFIC_TERMS`: regex `r"\b(OpenAI|Anthropic|Claude|GPT|ChatGPT|Gemini|Copilot)\b"`
- FAIL if any match found: reason = `f"Provider-specific reference found: '{match.group()}'"`

**7. `check_disk_based_state(er_path: Path, journal_path: Path) -> InvariantCheck`**
- Checks `er_path.exists()` and `journal_path.exists()`
- FAIL if either missing: reason = `f"Missing files: {', '.join(missing)}"`

**UPSTREAM_DEPENDENCIES map (30 entries):**

| Artifact Type | Required Frozen Upstream |
|--------------|------------------------|
| PRD | (none) |
| ACF | PRD |
| SAD | PRD |
| TDD | ACF, SAD |
| WDD | TDD |
| ORD | WDD |
| QAER | ORD |
| VP | QAER |
| TCR | VP |
| QGR | TCR |
| RER | ORD |
| RCF | RER |
| RP | RCF |
| RR | RP |
| RHR | RR |
| SDR | DPRD |
| DPRD | (none) |
| SOER | DPRD |
| VER | SOER |
| TM | SAD |
| SAR | TDD |
| DAR | TDD |
| CER | (none) |
| CSPEC | TDD |
| FFLR | (none) |
| DSR | TDD |
| PDR | (none) |
| ISPEC | (none) |
| EM | (none) |
| UDR | (none) |
| ARR | TDD |
| SKA | (none) |
| DHR | (none) |

---

### 4.17 observability layer (`src/observability.py`)

**SAD Component:** Observability Layer

- **Constructor:** `__init__(self, log_path: Path)`

**`record(invocation: InvocationRecord) -> None`**
- Creates parent directories (`log_path.parent.mkdir(parents=True, exist_ok=True)`)
- Serializes via `_record_to_dict()`: converts `InvocationRecord` to dict using `dataclasses.asdict()`, then replaces `event` and `strategy` enum fields with `.value` strings
- Appends `json.dumps(dict) + "\n"` to log file

**`read_records(since: datetime | None = None) -> list[InvocationRecord]`**
- Returns empty list if log file does not exist
- Reads line by line, skips empty lines, skips lines that fail `json.loads()`
- Deserializes via `_dict_to_record()`: constructs `InvocationRecord` with enum reconstruction (`LifecycleEvent(d["event"])`, `RoutingStrategy(d["strategy"])`)
- If `since` is set: parses `record.timestamp` via `datetime.fromisoformat(timestamp.replace("Z", "+00:00"))`, skips records before `since`. Silently skips records with unparseable timestamps.

**`cost_summary(initiative: str | None = None) -> dict`**
- Reads all records. If `initiative` provided: filters to records where `initiative in r.artifact_id`
- Aggregates: `total_cost` (sum), `cost_by_provider` (dict), `cost_by_artifact_type` (dict), `invocation_count` (len)
- All cost values rounded to 6 decimal places
- Returns structured dict

**`provider_health_summary() -> dict[str, dict]`**
- Reads all records. Groups by provider.
- Per provider: `total_invocations`, `failures` (count where `r.result == "failure"` or `r.error` is truthy), `avg_latency_ms` (mean, rounded to 1 decimal), `current_status`
- Status derivation: `"OK"` if failures == 0, `"DEGRADED"` if failure rate < 50%, `"DOWN"` if failure rate >= 50%

**`detect_cost_anomaly(invocation: InvocationRecord, window_hours: int = 24) -> str | None`**
- Reads records since `datetime.now(timezone.utc) - timedelta(hours=window_hours)`
- Filters to same `artifact_type` as invocation
- If no records or mean cost <= 0: returns None
- If `invocation.cost_usd > 3 * mean_cost`: returns warning string with cost ratio
- Otherwise returns None

---

### 4.18 data models (`src/models.py`)

**SAD Component:** Data Models

#### Enums

**`ArtifactStatus`:** `DRAFT`, `VALIDATED`, `FREEZE_PENDING`, `FROZEN`

**`LifecycleEvent`:** `PRE_GENERATION`, `POST_GENERATION`, `PRE_VALIDATION`, `POST_VALIDATION`, `POST_FREEZE`, `ON_FAILURE`

**`RoutingStrategy`:** `PARALLEL_CONSENSUS`, `PIPELINE`, `FALLBACK`, `COST_AWARE`

**`HealthStatus`:** `OK`, `DEGRADED`, `DOWN`

**`DecisionOutcome`:** `APPROVE`, `APPROVE_WITH_CONDITIONS`, `BLOCK`, `REMEDIATE_AND_RETRY`, `REQUIRE_REDESIGN`, `ROLLBACK`

#### Dataclasses

**`AgentRequest`** — 9 fields, no defaults:
| Field | Type | Description |
|-------|------|-------------|
| `artifact_type` | `str` | e.g., "SAD", "TDD" |
| `event` | `LifecycleEvent` | Which lifecycle event triggered this request |
| `spec_content` | `str` | Content of the artifact's spec file |
| `template_content` | `str` | Content of the artifact's template file |
| `prompt_content` | `str` | Content of the generation or validation prompt |
| `upstream_artifacts` | `dict[str, str]` | Map of artifact_id → content for frozen upstream artifacts |
| `current_artifact` | `Optional[str]` | Current artifact content (for validation or correction) |
| `correction_constraints` | `list[str]` | Blocking issues from previous validation (for correction) |
| `metadata` | `dict[str, str]` | Key-value metadata (initiative path, human_author, artifact_id) |

**`AgentResponse`** — 12 fields (8 required, 4 optional with defaults):
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `content` | `str` | (required) | Generated or validated artifact content |
| `provider` | `str` | (required) | Provider name (e.g., "anthropic", "openai") |
| `model` | `str` | (required) | Model identifier |
| `tokens_in` | `int` | (required) | Input token count |
| `tokens_out` | `int` | (required) | Output token count |
| `cost_usd` | `float` | (required) | Actual cost in USD |
| `latency_ms` | `float` | (required) | Request latency in milliseconds |
| `raw_response` | `Optional[dict]` | `None` | Provider-specific response metadata |
| `human_author` | `Optional[str]` | `None` | Provenance: human who initiated the request |
| `input_content_hash` | `Optional[str]` | `None` | Provenance: SHA-256 hash of all input content |
| `modification_record` | `Optional[list[dict]]` | `None` | Provenance: list of modifications made |
| `compliance_attestation` | `Optional[str]` | `None` | Provenance: compliance statement |

**`ValidationResult`** — 6 fields, no defaults:
| Field | Type | Description |
|-------|------|-------------|
| `status` | `str` | "PASS" or "FAIL" |
| `summary` | `str` | One-sentence verdict |
| `hard_gates` | `dict[str, str]` | Gate name → "PASS" or "FAIL" |
| `blocking_issues` | `list[dict]` | Each: `{"gate": str, "description": str, "location": str}` |
| `warnings` | `list[dict]` | Each: `{"description": str, "location": str}` |
| `completeness_score` | `int` | 0-100 |

**`ERStateBlock`** — 7 fields, no defaults:
| Field | Type | Description |
|-------|------|-------------|
| `current_layer` | `str` | e.g., "Layer 4 (EEK)" |
| `current_artifact` | `str` | e.g., "TDD-TEST-001" |
| `current_step` | `str` | e.g., "Generation" |
| `frozen_count` | `int` | Number of frozen artifacts |
| `next_action` | `str` | Next step for operator |
| `blocking_on` | `str` | What's blocking progress |
| `last_updated` | `str` | ISO 8601 timestamp |

**`InvocationRecord`** — 15 fields (14 required, 1 optional):
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timestamp` | `str` | (required) | ISO 8601 timestamp |
| `artifact_type` | `str` | (required) | e.g., "SAD" |
| `artifact_id` | `str` | (required) | e.g., "SAD-INIT-001" |
| `event` | `LifecycleEvent` | (required) | Which lifecycle event |
| `provider` | `str` | (required) | Provider name |
| `model` | `str` | (required) | Model identifier |
| `strategy` | `RoutingStrategy` | (required) | Which routing strategy |
| `tokens_in` | `int` | (required) | Input tokens |
| `tokens_out` | `int` | (required) | Output tokens |
| `cost_usd` | `float` | (required) | Cost in USD |
| `latency_ms` | `float` | (required) | Latency in ms |
| `result` | `str` | (required) | "success" or "failure" |
| `validation_status` | `Optional[str]` | (required) | "PASS", "FAIL", or None |
| `convergence_iteration` | `int` | (required) | Current iteration number |
| `error` | `Optional[str]` | `None` | Error message if failed |

**`ConvergenceState`** — 5 fields (2 required, 3 with defaults):
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `artifact_id` | `str` | (required) | Artifact being converged |
| `artifact_type` | `str` | (required) | Artifact type |
| `max_iterations` | `int` | `3` | Maximum convergence iterations |
| `current_iteration` | `int` | `0` | Current iteration number |
| `ledger` | `list[dict]` | `[]` (field default_factory) | Per-iteration validation results |

**`InvariantCheck`** — 3 fields, no defaults:
| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Invariant check name (e.g., "freeze_before_promote") |
| `passed` | `bool` | Whether the check passed |
| `reason` | `str` | Human-readable explanation |

---

### 4.19 config dataclasses (`src/config.py`)

**SAD Component:** Config Loader

**`ProviderConfig`:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `False` | Whether this provider is active |
| `model` | `str` | `""` | Model identifier |
| `max_tokens` | `int` | `8192` | Maximum output tokens |

**`RoutingConfig`:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_strategy` | `str` | `"fallback"` | Default routing strategy |
| `consensus_threshold` | `float` | `0.67` | Agreement threshold for parallel_consensus |
| `cost_tiers` | `list[dict]` | `[]` | Cost tier definitions |

**`HarnessConfig`:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `aieos_root` | `str` | `"../"` | Path to AIEOS governance framework root |
| `initiative_root` | `str` | `""` | Path to initiative project root |
| `providers` | `dict[str, ProviderConfig]` | `{}` | Provider configurations keyed by name |
| `routing` | `RoutingConfig` | `RoutingConfig()` | Routing configuration |
| `max_convergence_iterations` | `int` | `3` | Maximum convergence loop iterations |
| `observability_log` | `str` | `"harness-metrics.jsonl"` | Path to JSONL metrics log |
| `bindings` | `list[dict]` | `[]` | Lifecycle event binding definitions |

**`load_config(path: Path) -> HarnessConfig`:**
- If file does not exist: returns default `HarnessConfig`
- Opens file, calls `yaml.safe_load(f)` (returns `{}` if file is empty/None)
- Iterates `raw.get("providers", {})` — builds `ProviderConfig` for each dict entry
- Builds `RoutingConfig` from `raw.get("routing", {})`
- Builds `HarnessConfig` from top-level fields
- Applies env var overrides: `AIEOS_ROOT` → `config.aieos_root`, `AIEOS_INITIATIVE_ROOT` → `config.initiative_root` (only if env var is set, checked via `os.environ.get()`)

---

### 4.20 CLI functions (`src/cli.py`)

**SAD Component:** CLI

**`main(argv: list[str] | None = None) -> int`**
- Creates argparse parser with `--config` (default: `"harness.yaml"`)
- Adds 5 subparsers: generate, validate, lifecycle, health, costs
- Parses args, calls `load_config(Path(args.config))`
- Dispatches to handler function via dict lookup, returns handler's int exit code

**`cmd_generate(args, config) -> int`**
- `_build_adapters(config)` — lazy-imports and constructs enabled adapters
- `_resolve_kit_files(aieos_root, artifact_type)` — scans `aieos-*` dirs for spec/template/prompt
- `_collect_upstream_artifacts(initiative_path)` — scans SDLC dir for frozen artifacts
- Builds `AgentRequest` with `event=PRE_GENERATION`, invokes first adapter
- Prints content length, tokens, cost, latency, generated content
- Returns 0 on success, 1 on error

**`cmd_validate(args, config) -> int`**
- Reads artifact file from `args.artifact`
- Infers type from filename: `stem.split("-", 1)[-1].upper()` (e.g., "03-sad.md" → "SAD")
- Locates validator prompt from kit dirs: `docs/validators/{type}-validator.md`
- Builds `AgentRequest` with `event=PRE_VALIDATION`, `current_artifact=artifact_content`
- Returns 0 on success, 1 on error

**`cmd_lifecycle(args, config) -> int`**
- Step 1: Generate (same as cmd_generate)
- Step 2: Validate (builds new request with `current_artifact=gen_response.content`, `event=PRE_VALIDATION`)
- Prints total cost, validation result
- Prints `"Artifact ready for human review. Freeze? (harness does not auto-freeze)"` — **never auto-promotes**
- Returns 0 on success, 1 on error

**`cmd_health(args, config) -> int`**
- Calls `adapter.health()` for each enabled adapter
- Prints provider/model/status table
- Calls `ObservabilityLayer.provider_health_summary()` for historical data
- Returns 0 if all OK, 1 if any non-OK

**`cmd_costs(args, config) -> int`**
- Calls `ObservabilityLayer.cost_summary(initiative=args.initiative)`
- Prints total invocations, total cost, cost by provider, cost by artifact type
- Returns 0

**`_build_adapters(config: HarnessConfig) -> dict[str, object]`**
- Iterates `config.providers`. For each enabled provider:
  - `"anthropic"` → lazy-imports `AnthropicAdapter`, constructs with `model`, `max_tokens`
  - `"openai"` → lazy-imports `OpenAIAdapter`, constructs with `model`, `max_tokens`
- Returns `{name: adapter}` dict

**`_resolve_kit_files(aieos_root: Path, artifact_type: str) -> tuple[str, str, str]`**
- Iterates `sorted(aieos_root.iterdir())`, filtering to dirs starting with `"aieos-"`
- For each kit dir, checks for: `docs/specs/{type_lower}-spec.md`, `docs/artifacts/{type_lower}-template.md`, `docs/prompts/{type_lower}-prompt.md`
- Reads content of each file if it exists. Breaks after finding the first kit with a matching spec.
- Returns `(spec_content, template_content, prompt_content)` — empty strings if not found

**`_collect_upstream_artifacts(initiative_path: Path) -> dict[str, str]`**
- Scans `docs/sdlc/*.md` (sorted glob). For each file with both Artifact ID and Status fields where status == "FROZEN": includes `{artifact_id: full_file_content}`

---

### 4.21 state transition tables

#### ArtifactStatus transitions

The harness reads artifact status but never writes it. The status lifecycle is managed by the operator.

| Current State | Trigger | Next State | Actor |
|--------------|---------|------------|-------|
| DRAFT | Artifact generated or edited | DRAFT | Operator/Harness (content only) |
| DRAFT | Validation passes | VALIDATED | Operator (manual status update) |
| VALIDATED | Operator approves for freeze | FREEZE_PENDING | Operator |
| FREEZE_PENDING | Operator confirms freeze | FROZEN | Operator |
| FROZEN | (terminal — immutable) | — | — |
| VALIDATED | Re-edit / correction applied | DRAFT | Operator |
| FREEZE_PENDING | Operator rescinds approval | VALIDATED | Operator |

#### CircuitBreaker state transitions

| Current State | Trigger | Next State | Details |
|--------------|---------|------------|---------|
| CLOSED | `record_success(provider)` | CLOSED | Failure count and open timestamp cleared |
| CLOSED | `record_failure(provider)` where count < max_failures | CLOSED | Failure count incremented |
| CLOSED | `record_failure(provider)` where count >= max_failures | OPEN | `_open_since[provider] = time.monotonic()` |
| OPEN | `is_open()` called while `time.monotonic() - opened_at < reset_seconds` | OPEN | Returns `True`; provider skipped by routing |
| OPEN | `is_open()` called while `time.monotonic() - opened_at >= reset_seconds` | HALF-OPEN (CLOSED) | Both dicts cleared; returns `False`; allows retry |
| HALF-OPEN | Retry succeeds → `record_success(provider)` | CLOSED | Dicts cleared |
| HALF-OPEN | Retry fails → `record_failure(provider)` | OPEN (after max_failures reached again) | Failure count starts from 1; may take multiple failures to re-open |

Note: "HALF-OPEN" is implicit — the circuit breaker clears state on timeout and allows retry. If the retry fails, the failure count begins accumulating from 1, so the circuit does not immediately re-open (requires `max_failures` consecutive failures again).

#### ConvergenceState lifecycle

| Phase | Trigger | State Change | Terminal? |
|-------|---------|-------------|----------|
| Init | `ConvergenceLoop.run()` called | `current_iteration=0`, `ledger=[]` | No |
| Generate | Iteration starts | `current_iteration` incremented | No |
| Validate | Generate completes | Validation request built with `current_artifact` | No |
| Parse | Validation response received | `parse_validation_result()` called; ledger entry appended | No |
| PASS | `result.status == "PASS"` | Return `(response, result, state)` | Yes |
| Correct | FAIL and iteration < max-1 | Staleness/oscillation checked; correction request built | No |
| Escalate | FAIL and iteration == max | Return `(response, result, state)` with status "FAIL" | Yes |
| Error | Adapter raises exception | Exception propagates (uncaught) | Yes |

---

## 5. build and deployment approach (Deterministic)

### Build steps

1. **Prerequisites:** Python 3.11+ installed. pip available.
2. **Install dependencies (verified):** `pip install -r requirements-lock.txt` (pinned with hashes for reproducibility)
3. **Install dependencies (development):** `pip install -r requirements.txt` (includes pytest and dev tools)
4. **No compilation step.** Python is interpreted. No build artifacts beyond the pip install.
5. **Verify installation:** `python -c "from src import models; print('OK')"`

### Deployment steps

1. **Clone repository** to the operator's workstation
2. **Install dependencies** per build steps above
3. **Copy `harness.yaml.example` to `harness.yaml`** and configure: provider models, enabled flags, bindings, routing strategy, observability log path
4. **Set environment variables:**
   - `ANTHROPIC_API_KEY` (if using Anthropic adapter)
   - `OPENAI_API_KEY` (if using OpenAI adapter)
   - `AIEOS_ROOT` (optional override for AIEOS governance framework path)
   - `AIEOS_INITIATIVE_ROOT` (optional override for initiative project path)
5. **Verify provider health:** `python -m src.cli health`

### Configuration inputs required

| Input | Source | Required? | Default |
|-------|--------|-----------|---------|
| `harness.yaml` | File | Yes | Uses defaults if file missing |
| `ANTHROPIC_API_KEY` | Env var | If Anthropic adapter used | (none) |
| `OPENAI_API_KEY` | Env var | If OpenAI adapter used | (none) |
| `AIEOS_ROOT` | Env var or YAML | Yes (for generate/validate/lifecycle) | `"../"` |
| `AIEOS_INITIATIVE_ROOT` | Env var or YAML | If using initiative path | `""` |

### Secrets required (Names only; no values)

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`

---

## 6. failure handling and rollback (Hard)

### Failure modes

| Failure Mode | Detection Signal | Rollback/Compensation | Partial Failure Behavior |
|-------------|-----------------|----------------------|------------------------|
| AI provider API unavailable | `adapter.invoke()` raises exception; `health()` returns `DOWN` | CircuitBreaker opens after `max_failures` (default 3) consecutive failures. Fallback routing skips to next adapter. Circuit auto-resets after `reset_seconds` (default 60s). | Operation continues with alternate provider if available. If all providers fail, `RuntimeError` raised with all error details. |
| Malformed validation JSON | `parse_validation_result()` raises `ValueError` — no JSON found, invalid JSON, or missing required fields | Convergence loop treats iteration as wasted. Previous correction constraints carry forward. Loop bounded to `max_iterations`. | Iteration consumed without progress. Operator sees parse error in output. |
| Convergence staleness | `_detect_staleness()` returns `True` — same gate fails with identical description in consecutive iterations | Warning logged via `logging.warning()`. Loop continues to `max_iterations`. Ledger preserves evidence. | Remaining iterations likely wasted but bounded. Operator reviews ledger for root cause. |
| Convergence oscillation | `_detect_oscillation()` returns `True` — gate flip-flops across 3 iterations | Warning logged. Loop continues to bound. Ledger preserves oscillation pattern. | Indicates conflicting correction vs. generation; operator must adjust prompt or spec. |
| Missing frozen upstream | `check_freeze_before_promote()` returns `passed=False` with list of missing types | Generation blocked before any provider invocation. `InvariantCheck.reason` identifies missing artifacts. | No partial execution — invariant check runs before generation starts. |
| API key missing | `health()` returns `DOWN` (empty `_api_key`). Lazy client init fails on first `invoke()`. | Fallback routing skips DOWN providers. CLI reports error. | Other providers with valid keys continue to function. |
| Tool command not found | `FileNotFoundError` caught in `ToolAdapter.invoke()`. `health()` returns `DOWN` via `shutil.which()`. | Returns `AgentResponse` with error description in content. Does not crash. | Operation completes with error response. Other tools/providers unaffected. |
| Tool timeout | `subprocess.TimeoutExpired` caught in `ToolAdapter.invoke()` | Returns `AgentResponse` with timeout description. Default timeout: 300s, configurable per tool. | Operation completes with error response. |
| YAML config file missing | `load_config()` checks `path.exists()` | Returns default `HarnessConfig`. No crash. | All fields use defaults. No providers enabled unless configured via alternate mechanism. |
| JSONL log corrupted | `json.loads(line)` fails for individual lines during `read_records()` | Silently skips corrupted lines. Valid records still returned. | Aggregation queries may be incomplete but do not fail. |
| ER/Journal file missing | `check_disk_based_state()` returns `passed=False`. `read_er_state_block()` raises `FileNotFoundError`. | Invariant check blocks operation. Operator must create files. | No partial execution. |

---

## 7. observability (Hard)

### Logs

- **Per-invocation JSONL log** (`harness-metrics.jsonl` by default): Each `InvocationRecord` appended as a single JSON line. Fields: timestamp, artifact_type, artifact_id, event, provider, model, strategy, tokens_in, tokens_out, cost_usd, latency_ms, result, validation_status, convergence_iteration, error.
- **Python logging:** `src/routing.py` and `src/convergence.py` use `logging.getLogger(__name__)` for warning-level messages (staleness detected, oscillation detected).
- **CLI stdout:** Human-readable output for all 5 subcommands: content length, token counts, cost, latency, validation results, health tables, cost summaries.

### Metrics (Derived from JSONL)

- **Cost summary:** Total cost, cost by provider, cost by artifact type, invocation count. Optional initiative filtering.
- **Provider health summary:** Per provider: total invocations, failures, average latency, derived status (OK/DEGRADED/DOWN).
- **Cost anomaly detection:** Flags invocations where cost exceeds 3x the rolling mean for the same artifact type within a configurable lookback window (default 24 hours).

### Traces

Not applicable. Single-process, single-machine operation. No distributed tracing.

### Evidence required to prove success

- `harness-metrics.jsonl` contains one `InvocationRecord` per successful invocation with `result: "success"` and actual cost/token data
- `health` subcommand shows all enabled providers as `OK`
- `costs` subcommand returns non-zero invocation count and aggregated cost data
- Convergence loop ledger in `ConvergenceState` shows iteration progression and terminal PASS status
- All 7 invariant checks return `passed: True` for valid operations

---

## 8. testing strategy (Hard)

### Test summary

166 tests total. All run without API keys.

### Unit tests (per module)

| Module | Test File | Count | Key Behaviors Tested |
|--------|-----------|-------|---------------------|
| models | `tests/test_models.py` | ~ | Enum values, dataclass construction, field types and defaults, AgentResponse provenance fields |
| config | `tests/test_config.py` | ~ | YAML loading, default values, env var overrides (AIEOS_ROOT, AIEOS_INITIATIVE_ROOT), missing file fallback, ProviderConfig/RoutingConfig construction |
| lifecycle | `tests/test_lifecycle.py` | ~ | resolve() exact vs wildcard priority, resolve() with missing adapters, execute() happy path, execute() no binding RuntimeError |
| routing | `tests/test_routing.py` | ~ | fallback happy/all-fail, pipeline sequential chaining, parallel_consensus agreement/disagreement, cost_aware sorting/min_tier filtering, CircuitBreaker open/close/reset cycle |
| convergence | `tests/test_convergence.py` | ~ | parse_validation_result() with fenced JSON / raw JSON / missing JSON / invalid JSON / missing fields, ConvergenceLoop.run() PASS on first iteration / FAIL with correction / max iterations escalation, staleness detection, oscillation detection, correction request building |
| state | `tests/test_state.py` | ~ | read_er_state_block() field extraction, write_er_state_block() in-place update, read_frozen_artifacts() status mapping, is_artifact_frozen(), append_journal_entry() format, read_journal_entries() parsing |
| invariants | `tests/test_invariants.py` | ~ | All 7 checks: generation_validation_separation, freeze_before_promote (with/without frozen upstream), human_freeze_decision, bounded_convergence, validator_output_format (valid/invalid JSON, missing fields, suggestion language), tool_agnostic_policy (clean/provider-specific), disk_based_state (both/missing files) |
| observability | `tests/test_observability.py` | ~ | record() JSONL append, read_records() with/without since filter, cost_summary() aggregation and initiative filtering, provider_health_summary() status derivation, detect_cost_anomaly() threshold behavior |
| adapters | `tests/test_adapters.py` | ~ | MockAdapter preset responses, call history recording, should_fail behavior, ToolAdapter invoke/timeout/command-not-found, health checks |
| cli | `tests/test_cli.py` | ~ | main() argument parsing, _resolve_kit_files() kit scanning, _collect_upstream_artifacts() frozen filtering, _build_adapters() lazy import, subcommand routing |

### Integration tests (Mock providers)

| Test File | Count | Scenario |
|-----------|-------|----------|
| `tests/integration/test_single_lifecycle.py` | 10 | End-to-end lifecycle: generate → validate → present for freeze. Verifies invariant enforcement, state updates, and observability recording across component boundaries. |
| `tests/integration/test_convergence_loop.py` | 3 | Multi-iteration convergence: PASS on retry, max iterations escalation, staleness/oscillation detection with full ledger verification. |
| `tests/integration/test_lens_orchestration.py` | 20 | Multi-adapter routing scenarios across all 4 strategies. Circuit breaker integration. Consensus threshold behavior. Cost-aware provider selection. |
| `tests/integration/test_multi_provider.py` | — | Multi-provider fallback and failover scenarios with CircuitBreaker state transitions. |

### Slow tests (Real API keys)

- Gated behind `--run-slow` pytest flag
- Require `ANTHROPIC_API_KEY` environment variable
- Test actual Anthropic API invocation with token counting and cost verification
- Not run in standard test suite — only for manual provider integration verification

### Failure tests

- MockAdapter with `should_fail=True` → verifies RuntimeError propagation through routing and lifecycle
- All-adapters-fail in fallback chain → verifies RuntimeError with collected error messages
- Consensus disagreement → verifies ValueError with threshold details
- Pipeline step failure → verifies RuntimeError with step number and provider name
- parse_validation_result with invalid JSON → verifies ValueError
- Missing frozen upstream → verifies InvariantCheck.passed == False
- Auto-freeze attempt → verifies InvariantCheck.passed == False
- Suggestion language in validator output → verifies InvariantCheck.passed == False
- Tool timeout and command-not-found → verifies graceful error response (no crash)

### Pass/Fail criteria

- All 166 tests must pass: `pytest -v` returns exit code 0
- Zero tests require API keys or network access in the standard run
- Each invariant check has at least one positive (passes) and one negative (fails) test
- Each routing strategy has at least one happy-path and one all-fail test
- Integration tests exercise full component chains (CLI → Config → Adapter → Response → Observability)

---

## 9. operational notes (Minimum runbook)

### Deploy procedure

1. Clone repository to operator workstation
2. `pip install -r requirements-lock.txt`
3. Copy `harness.yaml.example` → `harness.yaml`, configure providers and bindings
4. Set API key environment variables
5. Verify: `python -m src.cli health`

### Verify procedure

1. Run full test suite: `pytest -v`
2. Check provider health: `python -m src.cli health`
3. Run a test generation with mock adapter: configure mock provider in `harness.yaml`, run `python -m src.cli generate --type PRD --initiative <path>`
4. Verify JSONL log created: `cat harness-metrics.jsonl | head -1`

### Rollback procedure

1. `pip install -r requirements-lock.txt` (restore pinned dependency versions)
2. `git checkout: harness.yaml` if configuration was modified
3. JSONL log is append-only; delete or truncate if metrics data is corrupted
4. ER state block and journal are Markdown files; restore from version control if corrupted

### Ownership/On-call expectations

- Single operator (Todd Linnertz) for all domains: invariant checks, routing strategies, convergence loop, adapter conformance, integration tests
- Quarterly review cadence per eval domain ownership table
- No on-call rotation — single-operator tool, not a service

---

## 10. dependencies

### Internal (Module-to-Module)

| Module | Depends On |
|--------|-----------|
| `src/models.py` | Python stdlib only (dataclasses, enum) |
| `src/config.py` | PyYAML, `os`, `pathlib` |
| `src/state.py` | `src/models` (ArtifactStatus, ERStateBlock) |
| `src/invariants.py` | `src/models` (ConvergenceState, InvariantCheck, LifecycleEvent, ValidationResult), `src/state` (read_frozen_artifacts) |
| `src/convergence.py` | `src/models` (AgentRequest, AgentResponse, ConvergenceState, LifecycleEvent, ValidationResult), `src/adapters/base` (AgentAdapter) |
| `src/lifecycle.py` | `src/models` (AgentRequest, AgentResponse, LifecycleEvent, RoutingStrategy), `src/adapters/base` (AgentAdapter) |
| `src/routing.py` | `src/models` (AgentRequest, AgentResponse, RoutingStrategy), `src/adapters/base` (AgentAdapter) |
| `src/observability.py` | `src/models` (InvocationRecord, LifecycleEvent, RoutingStrategy) |
| `src/cli.py` | `src/config`, `src/models`, `src/observability`, `src/adapters/anthropic` (lazy), `src/adapters/openai` (lazy) |
| `src/adapters/base.py` | `src/models` (AgentRequest, AgentResponse, HealthStatus) |
| `src/adapters/anthropic.py` | `src/models`, `src/adapters/base`, `anthropic` SDK (lazy import) |
| `src/adapters/openai.py` | `src/models`, `src/adapters/base`, `openai` SDK (lazy import) |
| `src/adapters/tool.py` | `src/models` |
| `src/adapters/mock.py` | `src/models` |

### External (Third-Party)

| Package | Version | Purpose | Required? |
|---------|---------|---------|-----------|
| PyYAML | >= 6.0 | YAML configuration parsing (`yaml.safe_load`) | Yes (runtime) |
| anthropic | >= 0.40 | Anthropic Claude Messages API client | Optional (lazy-loaded, only if Anthropic adapter used) |
| openai | >= 1.50 | OpenAI Chat Completions API client | Optional (lazy-loaded, only if OpenAI adapter used) |
| pytest | >= 8.0 | Test framework | Dev dependency only |

### Standard library dependencies (Key)

| Module | Used By | Purpose |
|--------|---------|---------|
| `dataclasses` | models, config, lifecycle | Dataclass definitions |
| `enum` | models | Enumeration types |
| `json` | convergence, invariants, observability | JSON parsing and serialization |
| `re` | convergence, state, invariants | Regex for Markdown parsing, suggestion scanning, term scanning |
| `concurrent.futures` | routing | ThreadPoolExecutor for parallel consensus |
| `subprocess` | adapters/tool | External tool execution |
| `tempfile` | adapters/tool | Temp file creation for artifact content |
| `hashlib` | adapters/anthropic, adapters/openai, adapters/mock | SHA-256 provenance hash |
| `argparse` | cli | Command-line argument parsing |
| `pathlib` | config, state, observability, cli | Filesystem path operations |
| `statistics` | observability | `mean()` for aggregation |
| `time` | routing, adapters | `time.monotonic()` for latency and circuit breaker timing |
| `logging` | routing, convergence | Warning-level logging for staleness/oscillation |

---

## 11. risks and assumptions

### Risks

- **R-1: Upstream dependency map drift.** The `UPSTREAM_DEPENDENCIES` map in `src/invariants.py` (30+ entries) must match the AIEOS governance model. New kits or changed dependency chains render the map stale, causing incorrect freeze-before-promote enforcement. Mitigation: single-dict, single-file location; test coverage for specific artifact types; update required when new kits are integrated.

- **R-2: Full-file read for observability queries.** `ObservabilityLayer.read_records()` reads the entire JSONL log on every aggregation call. Query latency increases linearly with log size over months of use. Mitigation: append-only log can be rotated or truncated; `detect_cost_anomaly` uses a configurable lookback window (default 24h); acceptable for single-operator usage.

- **R-3: Token estimation accuracy.** `cost_estimate()` uses 1 token per 4 characters heuristic. Actual counts may differ significantly for code-heavy or non-English content. Mitigation: used only for routing order, not billing; actual costs always recorded post-invocation.

- **R-4: Regex-based Markdown parsing fragility.** State Manager uses regex patterns to parse `| Field | Value |` tables. Non-standard Markdown formatting (extra whitespace, missing pipes, alternate table syntax) may cause silent parse failures. Mitigation: the harness generates its own table format (consistent), and AIEOS artifacts follow standardized Document Control formatting.

### Assumptions

- **A-1:** AIEOS governance framework is available at a reachable filesystem path (`AIEOS_ROOT`)
- **A-2:** Initiative projects follow AIEOS directory conventions (`docs/sdlc/*.md`, `docs/engagement/er-*.md`)
- **A-3:** At least one AI provider API key is set when using LLM adapters
- **A-4:** External tools invoked by Tool adapter are installed and on system PATH
- **A-5:** `UPSTREAM_DEPENDENCIES` map accurately reflects the current AIEOS governance model

---

## 12. freeze declaration (when ready)

This TDD documents the existing AIEOS Agent Harness (ECO-009) technical design retroactively. All specifications reflect the implemented codebase (16 source files, 166 tests).

- Approved By: _pending_
- Date: _pending_

<!-- Elicitation: Inversion applied. Key insight: the most dangerous technical design gap is the absence of formal configuration schema validation — invalid YAML keys are silently ignored by load_config(), meaning a misconfigured binding or routing strategy will produce no error at startup and fail only at invocation time with an unhelpful "no binding found" error. -->

<!-- PRINCIPLES COVERAGE
| Principles File | Section | TDD Section Addressed | Status |
|---|---|---|---|
| N/A — no principles files exist for this system project | — | — | N/A — PRD-HARNESS-001 Section 6 Constraints serve as guardrail source; all 6 constraints (C-1 through C-6) are addressed in §4.16 Invariant Check Functions and §6 Failure Handling |
-->
