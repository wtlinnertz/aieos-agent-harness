# Architecture

## Component Diagram

```
+-------------------------------------------------------------+
|                     AIEOS Agent Harness                      |
|                                                              |
|  +------------------+       +-------------------+            |
|  |   Config Loader   |------>|  Lifecycle Binder  |           |
|  | (harness.yaml +   |      | (event bindings,   |           |
|  |  env vars)        |      |  artifact routing)  |          |
|  +------------------+       +--------+----------+            |
|                                      |                       |
|                                      v                       |
|                             +--------+----------+            |
|                             |   Routing Engine    |           |
|                             | (fallback, pipeline |           |
|                             |  consensus, cost)   |           |
|                             +--------+----------+            |
|                                      |                       |
|              +----------+------------+------------+          |
|              |          |            |             |          |
|              v          v            v             v          |
|         +--------+ +--------+ +----------+ +---------+      |
|         |Anthropic| | OpenAI | |   Tool   | |  Mock   |      |
|         |Adapter  | |Adapter | | Adapter  | | Adapter |      |
|         +--------+ +--------+ +----------+ +---------+      |
|              (Provider Adapter Layer)                         |
|                                                              |
|  +------------------+       +---------------------+          |
|  |  State Manager    |       | Observability Layer  |         |
|  | (ER state block,  |      | (JSONL metrics,      |         |
|  |  Sherpa Journal)  |      |  cost tracking)      |         |
|  +------------------+       +---------------------+          |
+-------------------------------------------------------------+
```

## Five Components

### 1. Config Loader (`src/config.py`)

Loads `harness.yaml` and applies environment variable overrides. Produces a `HarnessConfig` dataclass containing provider settings, routing strategy, bindings, and convergence limits. API keys are read exclusively from environment variables and never stored in YAML.

### 2. Lifecycle Binder (`src/lifecycle.py`)

Maps lifecycle events (`PRE_GENERATION`, `POST_GENERATION`, `PRE_VALIDATION`, `POST_VALIDATION`, `POST_FREEZE`, `ON_FAILURE`) to adapter invocations. Each binding specifies an event, an artifact type (or `*` for wildcard), a list of adapter names, and a routing strategy. The binder resolves exact artifact type matches before wildcards.

### 3. Routing Engine (`src/routing.py`)

Receives resolved adapter lists from the Lifecycle Binder and dispatches requests using one of four strategies:

- **Fallback** -- try adapters in order; skip those with open circuit breakers
- **Pipeline** -- sequential chain where each adapter's output feeds the next
- **Parallel Consensus** -- fan out to all adapters; require agreement (content length within 20%) above a configurable threshold
- **Cost-Aware** -- sort adapters by `cost_estimate()`, invoke cheapest first

Includes a `CircuitBreaker` that opens after repeated failures and auto-resets after a configurable timeout.

### 4. State Manager (`src/state.py`)

Reads and writes Markdown-based state files on disk:

- **ER state block**: parses the `| Field | Value |` table in section 1b of the Engagement Record. Supports read and in-place write of Current Layer, Current Artifact, Current Step, Frozen Count, Next Action, Blocking On, and Last Updated.
- **Frozen artifact scanning**: reads `docs/sdlc/*.md` files, extracts Artifact ID and Status from Document Control tables, returns a mapping of artifact IDs to `ArtifactStatus`.
- **Sherpa Journal**: appends formatted Markdown entries (header + field/value table) and parses them back into dicts.

### 5. Observability Layer (`src/observability.py`)

Records per-invocation metrics as JSON lines to a JSONL file. Each `InvocationRecord` captures timestamp, artifact type/ID, event, provider, model, routing strategy, token counts, cost, latency, result, validation status, and convergence iteration. Provides aggregation queries: cost summary (by provider, by artifact type), provider health summary, and cost anomaly detection (flags invocations exceeding 3x the rolling mean).

## Request Flow

```
1. Config loaded from harness.yaml + env vars
         |
2. LifecycleBinder.execute(event, request) called
         |
3. Binder resolves event + artifact_type to (adapter, strategy, config) tuples
         |
4. RoutingEngine.route(strategy, adapters, request, config) dispatches
         |
5. Adapter.invoke(request) calls AI provider / tool / mock
         |
6. AgentResponse returned up the chain
         |
7. State Manager updates ER state block + journal on disk
         |
8. Observability Layer records InvocationRecord to JSONL
```

## Invariant Enforcement Points

All seven AIEOS invariants are enforced programmatically in `src/invariants.py`:

| # | Invariant | Enforcement Point |
|---|-----------|-------------------|
| 1 | Generation/validation separation | `check_generation_validation_separation()` -- called before validation to verify generation and validation use different lifecycle events |
| 2 | Freeze-before-promote | `check_freeze_before_promote()` -- called before downstream generation; scans `docs/sdlc/*.md` for frozen upstream artifacts |
| 3 | Human freeze decision | `check_human_freeze_decision()` -- asserts no auto-freeze flag was set; artifact stays VALIDATED until human promotes |
| 4 | Bounded convergence | `check_bounded_convergence()` -- called each iteration of `ConvergenceLoop`; enforces `current_iteration <= max_iterations` |
| 5 | Validator output format | `check_validator_output_format()` -- parses JSON response; rejects suggestion language; requires PASS/FAIL status |
| 6 | Tool-agnostic policy | `check_tool_agnostic_policy()` -- scans governance content for provider-specific terms (OpenAI, Anthropic, etc.) |
| 7 | Disk-based state | `check_disk_based_state()` -- verifies ER file and journal file both exist on disk |

## State Flow

```
                    +------------------+
                    |   ER Markdown    |
                    | (state block in  |
                    |  section 1b)     |
                    +--------+---------+
                      read   |  write
                      <------+------>
                             |
                    +--------+---------+
                    |  State Manager   |
                    +--------+---------+
                             |
                      read   |  append
                      <------+------>
                             |
                    +--------+---------+
                    | Sherpa Journal   |
                    | (Markdown with   |
                    |  ### entries)    |
                    +------------------+
```

- **ER reads**: `read_er_state_block()` -- parse current layer, artifact, step, frozen count
- **ER writes**: `write_er_state_block()` -- in-place regex replacement of field values
- **Frozen scan**: `read_frozen_artifacts()` -- glob `docs/sdlc/*.md`, extract Status from Document Control
- **Journal appends**: `append_journal_entry()` -- append formatted section with timestamp + field/value table
- **Journal reads**: `read_journal_entries()` -- parse `###` headers and table rows into dicts
