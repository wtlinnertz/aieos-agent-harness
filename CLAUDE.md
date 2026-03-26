# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## What This Repository Is

**AIEOS Agent Harness** (ECO-009) is a pluggable multi-agent orchestration engine for the AIEOS governance framework. It sits between AIEOS governance (Markdown specs, templates, prompts, validators) and AI providers, orchestrating artifact lifecycle events while enforcing AIEOS invariants.

This is an **ecosystem software project**, not an AIEOS kit. It consumes AIEOS governance but lives outside the Markdown framework.

## Tech Stack

- Python 3.11+
- PyYAML for configuration
- Anthropic SDK, OpenAI SDK for AI provider adapters
- pytest for testing
- No database — all state on disk (ER Markdown, Journal Markdown, JSONL metrics)

## Repository Structure

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

## Running Tests

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

## Core Concepts

### Five Components

1. **Lifecycle Binder** — Maps artifact lifecycle events (pre_generation, post_generation, etc.) to agent adapter invocations via YAML config
2. **Routing Engine** — Four strategies: parallel_consensus, pipeline, fallback, cost_aware
3. **Provider Adapter Layer** — Plugin interface (Protocol-based). Implementations: Anthropic, OpenAI, Tool, Mock
4. **State Manager** — Reads/writes ER state block + Sherpa Journal on disk
5. **Observability Layer** — Per-invocation cost, latency, token usage in JSONL

### Seven AIEOS Invariants Enforced

1. Generation and validation in separate invoke() calls
2. Freeze-before-promote (check upstream status before downstream generation)
3. Human freeze decision (present results, never auto-promote)
4. Bounded convergence (max 3 remediate-and-retry, then escalate)
5. Validators judge only (standardized JSON output, no suggestions)
6. Tool-agnostic policy (provider details in adapters, not governance files)
7. Disk-based state (ER + Journal files are system of record)

## Eval Domain Ownership

| Domain | Owner | Expertise Basis | Review Cadence |
|--------|-------|----------------|----------------|
| Invariant checks | Todd Linnertz | Framework designer | Quarterly |
| Routing strategies | Todd Linnertz | Implementation author | Quarterly |
| Convergence loop | Todd Linnertz | Implementation author | Quarterly |
| Adapter conformance | Todd Linnertz | Spec author | Quarterly |
| Integration tests | Todd Linnertz | Test designer | Quarterly |

Last reviewed: 2026-03-26
Next review: 2026-06-26

## Adding a New Provider

1. Create `src/adapters/your_provider.py`
2. Implement the `AgentAdapter` Protocol (invoke, health, cost_estimate)
3. Add provider config section to `harness.yaml`
4. Register in adapter factory (see `docs/adding-providers.md`)

## Configuration

- `harness.yaml` — Bindings, routing strategy, provider settings
- Environment variables — API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY)
- See `harness.yaml.example` for full schema

## What Not To Do

- Do not store API keys in YAML files — use environment variables
- Do not combine generation and validation in the same adapter session
- Do not auto-promote artifacts from Validated to Frozen — human decides
- Do not modify AIEOS governance files (specs, templates, prompts, validators)
- Do not maintain state in memory — write to ER + Journal on disk
- Do not add provider-specific logic to core modules — keep it in adapters
