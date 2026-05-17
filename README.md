# AIEOS Agent Harness

A pluggable multi-agent orchestration engine for the AIEOS governance framework. The harness sits between AIEOS governance artifacts (Markdown specs, templates, prompts, validators) and AI providers, orchestrating artifact lifecycle events through configurable routing strategies while enforcing AIEOS structural invariants.

## Getting started

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run unit tests (no API keys needed)
PYTHONPATH=. pytest tests/ -v --ignore=tests/integration

# Run integration tests (mock providers, no API keys needed)
PYTHONPATH=. pytest tests/integration/ -v

# Run all tests
PYTHONPATH=. pytest -v

# CLI health check
PYTHONPATH=. python -m src.cli health
```

## Architecture

Five components work together to orchestrate artifact lifecycles:

1. Lifecycle Binder: maps lifecycle events to adapter invocations
2. Routing Engine: four strategies (fallback, pipeline, parallel consensus, cost-aware)
3. Provider Adapter Layer: pluggable adapters (Anthropic, OpenAI, Tool, Mock)
4. State Manager: reads and writes ER state blocks and Sherpa Journal on disk
5. Observability Layer: per-invocation cost, latency, and token metrics in JSONL

See [docs/architecture.md](docs/architecture.md) for the full component diagram and data flow.

## Configuration

Copy `harness.yaml.example` to `harness.yaml` and set your API keys as environment variables:

```bash
export ANTHROPIC_API_KEY="your-key"
export OPENAI_API_KEY="your-key"
```

See [docs/configuration.md](docs/configuration.md) for the full YAML schema reference.

## Adding providers

The harness uses a Protocol-based adapter interface. Implement `invoke()`, `health()`, and `cost_estimate()` to add a new AI provider or tool.

See [docs/adding-providers.md](docs/adding-providers.md) for a step-by-step guide.

## AIEOS invariants enforced

The harness programmatically enforces seven AIEOS structural invariants:

- Generation/validation separation: generation and validation always use separate invoke() calls
- Freeze-before-promote: upstream artifacts must be frozen before downstream generation
- Human freeze decision: artifacts stay VALIDATED until a human promotes to FROZEN
- Bounded convergence: max 3 generate-validate cycles before escalation
- Validator output format: validators produce PASS/FAIL JSON only, no suggestions
- Tool-agnostic policy: no provider-specific references in governance content
- Disk-based state: ER and Journal files on disk are the system of record

## License

MIT
