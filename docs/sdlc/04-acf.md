# Architecture Context (ACF) — AIEOS Agent Harness

## Document control

| Field | Value |
|-------|-------|
| ACF ID | ACF-HARNESS-001 |
| Owner | Todd Linnertz |
| Status | Draft |
| Applicability Scope | AIEOS Agent Harness (ECO-009) — this project only |
| Date | 2026-03-26 |
| Governance Model Version | 1.3 |
| Spec Version | acf-spec v1.0 |
| Principles Version | product-craftsmanship v1.0 |
| Upstream PRD | PRD-HARNESS-001 (docs/sdlc/03-prd.md) |

**Note:** This ACF is retroactive. All constraints documented below are extracted from the existing codebase (16 source files, 166 tests). Guardrails describe what the system enforces, not what is planned.

---

## Runtime and language

- **Runtime environment:** Python 3.11+
- **Language:** Python (standard typing, dataclasses, enums — no strict mode equivalent beyond type annotations)
- **Module system:** Python packages (`src/` package with `src/adapters/` subpackage)

---

## Dependencies

- **Key libraries:**
  - PyYAML >= 6.0 — YAML configuration loading
  - Anthropic SDK >= 0.40 — Claude provider adapter (optional, lazy-imported)
  - OpenAI SDK >= 1.50 — OpenAI provider adapter (optional, lazy-imported)
  - pytest >= 8.0 — test framework (dev dependency only)
- **Dependency philosophy:** Minimal. Core modules (models, routing, convergence, state, invariants, lifecycle) use only the Python standard library. Provider SDKs are optional and lazy-loaded at first use.
- **Explicitly forbidden:**
  - No database libraries (SQLAlchemy, psycopg2, sqlite3, etc.) — PRD NG-2, C-5
  - No web frameworks (Flask, FastAPI, Django) — PRD NG-1
  - No agent frameworks (LangChain, CrewAI, AutoGen) — the harness IS the orchestration layer
  - No ORM of any kind

---

## Configuration and secrets

- **Secrets management:** API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY) are read exclusively from environment variables. The YAML configuration file must never contain API keys or secrets (PRD C-1, FR-42).
- **Non-secret configuration:** `harness.yaml` — contains provider settings (model names, max tokens, enabled flags), routing strategy, lifecycle bindings, convergence limits, and observability log path. Loaded by `src/config.py` via `yaml.safe_load`.
- **Environment variable overrides:** AIEOS_ROOT and AIEOS_INITIATIVE_ROOT override their corresponding YAML values when set (FR-43).
- **What must not be stored in code or config files:** API keys, provider credentials, any authentication tokens.

---

## State and data

- **Where state lives:** On disk as Markdown and JSONL files. No in-memory state persistence across invocations.
  - **ER state block:** `| Field | Value |` table in section 1b of Engagement Record Markdown files. Read via regex parsing, written via in-place regex replacement (`src/state.py`).
  - **Sherpa Journal:** Markdown file with `### entry_type: timestamp` sections containing field/value tables. Append-only writes, parsed on read (`src/state.py`).
  - **Observability metrics:** JSONL file (`harness-metrics.jsonl` by default). Each line is a JSON `InvocationRecord`. Append-only writes, line-by-line reads for aggregation (`src/observability.py`).
- **Data store technology:** Filesystem only. No SQL, NoSQL, or in-memory database (PRD NG-2, NFR-2).
- **Data constraints:** No PII is stored. Metrics include cost, latency, token counts, and provider names — no artifact content in the metrics log.

---

## Deployment and distribution

- **Distribution method:** Python package installed via pip (`pip install -r requirements.txt` or verified `pip install -r requirements-lock.txt`). Not published to PyPI.
- **Target environments:** Developer workstations running macOS or Linux with Python 3.11+.
- **Deployment constraints:**
  - Must run without network access to AI providers when using Mock or Tool adapters (NFR-3 — all 166 tests run without API keys).
  - No containerization requirement. No CI/CD pipeline defined yet.
  - Single-operator, single-machine operation only (PRD NG-5).

---

## Infrastructure and platform

- **Hosting:** Local developer workstation. No cloud deployment.
- **Infrastructure as Code:** None. No infrastructure provisioning.
- **Container orchestration:** None.
- **Networking:** Outbound HTTPS to AI provider APIs (api.anthropic.com, api.openai.com) when using LLM adapters. No inbound connections. No load balancers, CDN, or API gateway.
- **Managed services:** None consumed.
- **Scaling approach:** Not applicable. Single-operator CLI tool.
- **Observability stack:** Self-contained JSONL metrics with built-in aggregation (cost summary, provider health summary, anomaly detection). No external observability platform.
- **Disaster recovery:** Not applicable. JSONL metrics are append-only logs. ER and Journal state is recoverable from the Markdown files on disk.

---

## Testing

- **Test framework:** pytest >= 8.0
- **Test philosophy:** Unit tests + integration tests, all runnable without AI provider API keys. Provider interactions are tested via `MockAdapter` (`src/adapters/mock.py`). 166 tests total. Slow tests (requiring real API keys) are gated behind `--run-slow` flag.
- **Test structure:**
  - `tests/test_*.py` — unit tests per module
  - `tests/integration/` — integration tests with mock providers
  - `tests/conftest.py` — shared fixtures

---

## Integration points

- **External services:**
  - Anthropic Messages API (Claude models) — via `src/adapters/anthropic.py`
  - OpenAI Chat Completions API — via `src/adapters/openai.py`
  - External CLI tools (SAST, linters) — via `src/adapters/tool.py` (subprocess execution)
- **Internal dependencies:**
  - AIEOS governance framework directory (specs, templates, prompts, validators across 12+ kits) — read-only filesystem access, path configured via `aieos_root` in harness.yaml or AIEOS_ROOT env var
  - Initiative project directories following AIEOS conventions (`docs/sdlc/*.md`, `docs/engagement/er-*.md`) — read/write for state management

---

## Constraints and principles

### Architecture principles (from cLAUDE.md and PRD)

1. **Generation/validation separation:** Generation and validation are always separate `invoke()` calls with no shared session state. A single invocation must never both generate and validate an artifact (PRD C-2, FR-15).
2. **Freeze-before-promote:** Upstream artifacts must be frozen before downstream generation begins. Enforced via a 30+ artifact type dependency map in `src/invariants.py` (PRD FR-16).
3. **Human freeze decision:** The system presents validation results and stops. It never auto-promotes an artifact from VALIDATED to FROZEN (PRD C-3, NG-3, FR-17).
4. **Bounded convergence:** The generate-validate loop is bounded to a configurable maximum (default 3 iterations). Staleness and oscillation are detected and logged (PRD FR-18, FR-11, FR-12).
5. **Validators judge only:** Validator output is standardized JSON (status, summary, hard_gates, blocking_issues, warnings, completeness_score). No suggestion language permitted (PRD FR-19).
6. **Tool-agnostic policy:** Provider-specific code lives exclusively in adapter implementations under `src/adapters/`. Core modules and governance files must not reference specific providers (PRD C-6, FR-20).
7. **Disk-based state:** ER state blocks, journal entries, and metrics are persisted to files on disk. No in-memory-only state (PRD C-5, FR-21).

### Hard constraints from PRD

- C-1: No credentials in configuration files (env vars only)
- C-2: No combined generation and validation (separate invocations)
- C-3: No auto-promotion (human freeze decision)
- C-4: No governance file mutation (read-only consumption)
- C-5: No in-memory state (disk is system of record)
- C-6: No provider-specific logic in core modules (adapters only)

### Adapter contract

All provider adapters implement the `AgentAdapter` Protocol (`src/adapters/base.py`): `provider_name` property, `model_name` property, `invoke(request) -> response`, `health() -> HealthStatus`, `cost_estimate(request) -> float`. SDK clients are lazily initialized on first use, not at construction time (PRD FR-31).

---

## Completeness checklist

- [x] Runtime and language are specified (Python 3.11+)
- [x] Key dependencies are listed (PyYAML, Anthropic SDK, OpenAI SDK — all with minimum versions)
- [x] Secrets handling is defined (environment variables only, never in YAML)
- [x] State management approach is clear (Markdown + JSONL on disk, no database)
- [x] Distribution/deployment method is specified (pip install, local workstation)
- [x] Infrastructure and platform details are captured (local only, no cloud)
- [x] Hard constraints from the PRD are referenced (C-1 through C-6, NG-1 through NG-7)
