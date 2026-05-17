# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## What this repository is

**AIEOS Agent Harness** (ECO-009) is a pluggable multi-agent orchestration engine for the AIEOS governance framework. It sits between AIEOS governance (Markdown specs, templates, prompts, validators) and AI providers, orchestrating artifact lifecycle events while enforcing AIEOS invariants.

This is an **system software project**, not an AIEOS kit. It consumes AIEOS governance but lives outside the Markdown framework.

## Tech stack

- Python 3.11+
- PyYAML for configuration
- Anthropic SDK, OpenAI SDK for AI provider adapters
- pytest for testing
- No database: all state on disk (ER Markdown, Journal Markdown, JSONL metrics)

## Repository structure

```
src/
  models.py          # Core data models (dataclasses + enums)
  config.py          # YAML + env var config loading
  state.py           # State Manager — ER state block + journal reader/writer
  invariants.py      # 7 AIEOS invariant enforcement checks
  lifecycle.py       # Lifecycle Binder — event-to-agent mapping
  routing.py         # Routing Engine — 4 strategies
  convergence.py     # Bounded convergence loop (max 3 iterations)
  observability.py   # Per-invocation metrics (JSONL)
  cli.py             # CLI entry point
  adapters/
    base.py          # AgentAdapter Protocol
    anthropic.py     # Anthropic Claude adapter
    openai.py        # OpenAI adapter
    tool.py          # Non-LLM tool adapter (SAST, linters)
    mock.py          # Mock adapter for testing
tests/
  conftest.py        # Shared fixtures
  test_*.py          # Unit tests (no API keys needed)
  integration/       # Integration tests (mock providers)
```

## Running tests

```bash
# Unit tests (no API keys needed)
pytest tests/ -v --ignore=tests/integration

# Integration tests (mock providers, no API keys needed)
pytest tests/integration/ -v

# All tests
pytest -v

# Slow tests (requires ANTHROPIC_API_KEY)
pytest -v --run-slow

# Verified install (pinned with hashes)
pip install -r requirements-lock.txt
```

## Core concepts

### Five components

1. Lifecycle Binder: maps artifact lifecycle events (pre_generation, post_generation, etc.) to agent adapter invocations via YAML config
2. Routing Engine: four strategies: parallel_consensus, pipeline, fallback, cost_aware
3. Provider Adapter Layer: plugin interface (Protocol-based). Implementations: Anthropic, OpenAI, Tool, Mock
4. State Manager: reads and writes ER state block + Sherpa Journal on disk
5. Observability Layer: per-invocation cost, latency, token usage in JSONL

### Seven AIEOS invariants enforced

1. Generation and validation in separate invoke() calls
2. Freeze-before-promote (check upstream status before downstream generation)
3. Human freeze decision (present results, never auto-promote)
4. Bounded convergence (max 3 remediate-and-retry, then escalate)
5. Validators judge only (standardized JSON output, no suggestions)
6. Tool-agnostic policy (provider details in adapters, not governance files)
7. Disk-based state (ER + Journal files are system of record)

## Eval domain ownership

| Domain | Owner | Expertise Basis | Review Cadence |
|--------|-------|----------------|----------------|
| Invariant checks | Todd Linnertz | Framework designer | Quarterly |
| Routing strategies | Todd Linnertz | Implementation author | Quarterly |
| Convergence loop | Todd Linnertz | Implementation author | Quarterly |
| Adapter conformance | Todd Linnertz | Spec author | Quarterly |
| Integration tests | Todd Linnertz | Test designer | Quarterly |

Last reviewed: 2026-03-26
Next review: 2026-06-26

## Adding a new provider

1. Create `src/adapters/your_provider.py`
2. Implement the `AgentAdapter` Protocol (invoke, health, cost_estimate)
3. Add provider config section to `harness.yaml`
4. Register in adapter factory (see `docs/adding-providers.md`)

## Configuration

- `harness.yaml` — Bindings, routing strategy, provider settings
- Environment variables — API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY)
- See `harness.yaml.example` for full schema

## What not to do

- Do not store API keys in YAML files — use environment variables
- Do not combine generation and validation in the same adapter session
- Do not auto-promote artifacts from Validated to Frozen — human decides
- Do not modify AIEOS governance files (specs, templates, prompts, validators)
- Do not maintain state in memory — write to ER + Journal on disk
- Do not add provider-specific logic to core modules — keep it in adapters

---

## Spec-Driven CI/CD — agent harness context

This repo is the multi-agent orchestration system bridging governance artifacts and AI providers.
For spec-driven CI/CD, it owns the runtime capability substrate: registry, attestation verification,
tool-using agent pattern, and structured event emission.

### What lives here for CI/CD (M2 outputs)

- Capability registry (artifact-store-backed, in-memory index per process)
- Attestation verification at registration time
- Tool-using agent interface (agent-with-LLM and agent-without-LLM variants)
- Structured run-log emission (stdout events: run.start, task.start, task.evidence, task.result, run.end)
- Contract tests for all of the above

### Implementation plan

The full plan is at: `~/second-brain/AIEOS Spec-Driven CI-CD Implementation Plan.md`

Read the M2 section before starting any task. The harness work depends on M1 artifacts
being frozen in `aieos-governance-foundation` (contracts, conformance attestation schema).

### Key design decisions

- Registry is the artifact store's source of truth; in-memory index is a read-through cache.
- Registration refuses adapters without a valid conformance attestation for current or within-grace contract version.
- The registry lookup API returns an empty list when no adapter satisfies an action. It never silently falls back.
- Structured events emit to stdout. The log forwarder is a separate follow-on; do not build it here.
- Adapters are not skills. Adapters have contracts, conformance, attestation. Skills are agent-side LLM behavior.

### Python conventions

- Type hints on public functions.
- `ruff` for linting. `mypy` if config exists.
- `structlog` over `print`. Logging keys in snake_case.
- Dependency injection for anything that touches the outside world.
- Tests in AAA shape. One behavior per test. Name: `test_<unit>_<condition>_<expected>`.
- 143 existing tests. Do not break them.

### Three invariants (never violate)

1. Separation of concerns.
2. Freeze-before-promote.
3. Validators judge, they don't help.
