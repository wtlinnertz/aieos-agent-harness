# Configuration Reference

## Configuration File

The harness reads `harness.yaml` from the project root. Copy `harness.yaml.example` to `harness.yaml` and customize.

## YAML Schema

### Top-Level Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `aieos_root` | string | `../` | Path to the AIEOS governance framework root directory |
| `initiative_root` | string | `""` | Path to the initiative project directory (contains `docs/sdlc/`, `docs/engagement/`) |
| `providers` | object | `{}` | Provider configuration (see below) |
| `routing` | object | see below | Routing engine configuration |
| `max_convergence_iterations` | int | `3` | Maximum generate-validate cycles before escalation |
| `observability_log` | string | `harness-metrics.jsonl` | Path to the JSONL metrics log file |
| `bindings` | list | `[]` | Lifecycle event-to-adapter bindings (see below) |

### Provider Configuration

Each key under `providers` is a provider name. Values:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Whether this provider is available for routing |
| `model` | string | `""` | Model identifier (e.g., `claude-sonnet-4-20250514`, `gpt-4o`) |
| `max_tokens` | int | `8192` | Maximum tokens per request |

Example:

```yaml
providers:
  anthropic:
    enabled: true
    model: claude-sonnet-4-20250514
    max_tokens: 8192
  openai:
    enabled: false
    model: gpt-4o
    max_tokens: 8192
```

### Routing Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `default_strategy` | string | `fallback` | Default routing strategy: `fallback`, `parallel_consensus`, `pipeline`, `cost_aware` |
| `consensus_threshold` | float | `0.67` | Fraction of providers that must agree for parallel consensus to succeed |
| `cost_tiers` | list | `[]` | Cost tier definitions for cost-aware routing |

#### Cost Tier Entry

| Key | Type | Description |
|-----|------|-------------|
| `provider` | string | Provider name |
| `model` | string | Model identifier |
| `tier` | string | Tier label (`standard`, `premium`) |
| `cost_per_1k_input` | float | Cost per 1,000 input tokens (USD) |
| `cost_per_1k_output` | float | Cost per 1,000 output tokens (USD) |

Example:

```yaml
routing:
  default_strategy: fallback
  consensus_threshold: 0.67
  cost_tiers:
    - provider: anthropic
      model: claude-sonnet-4-20250514
      tier: standard
      cost_per_1k_input: 0.003
      cost_per_1k_output: 0.015
    - provider: anthropic
      model: claude-opus-4-20250514
      tier: premium
      cost_per_1k_input: 0.015
      cost_per_1k_output: 0.075
```

### Binding Configuration

Each entry in the `bindings` list maps a lifecycle event to one or more adapters.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `event` | string | yes | Lifecycle event: `pre_generation`, `post_generation`, `pre_validation`, `post_validation`, `post_freeze`, `on_failure` |
| `artifact_type` | string | yes | Artifact type (e.g., `SAD`, `TDD`) or `*` for all types |
| `adapters` | list[string] | yes | Ordered list of provider names to invoke |
| `strategy` | string | no | Routing strategy override for this binding (defaults to `routing.default_strategy`) |
| `config` | object | no | Strategy-specific configuration (e.g., `threshold` for consensus) |

#### Binding Examples

**Fallback routing** (try Anthropic, fall back to OpenAI):

```yaml
bindings:
  - event: post_generation
    artifact_type: SAD
    adapters: [anthropic, openai]
    strategy: fallback
```

**Parallel consensus** (require multi-provider agreement):

```yaml
bindings:
  - event: post_generation
    artifact_type: TM
    adapters: [anthropic, openai]
    strategy: parallel_consensus
    config:
      threshold: 0.67
```

**Pipeline** (chain providers sequentially):

```yaml
bindings:
  - event: post_generation
    artifact_type: TDD
    adapters: [anthropic, openai]
    strategy: pipeline
```

**Cost-aware routing** (cheapest available provider):

```yaml
bindings:
  - event: post_generation
    artifact_type: "*"
    adapters: [anthropic, openai]
    strategy: cost_aware
```

**Wildcard binding** (applies to all artifact types unless overridden):

```yaml
bindings:
  - event: post_generation
    artifact_type: "*"
    adapters: [anthropic]
    strategy: fallback
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | When Anthropic enabled | API key for the Anthropic provider |
| `OPENAI_API_KEY` | When OpenAI enabled | API key for the OpenAI provider |
| `AIEOS_ROOT` | No | Overrides `aieos_root` from YAML when set |
| `AIEOS_INITIATIVE_ROOT` | No | Overrides `initiative_root` from YAML when set |

Environment variables always take precedence over YAML values for paths. API keys are never stored in YAML -- they are read exclusively from environment variables.

## Full Example

```yaml
aieos_root: ../
initiative_root: ../aieos-console

providers:
  anthropic:
    enabled: true
    model: claude-sonnet-4-20250514
    max_tokens: 8192
  openai:
    enabled: true
    model: gpt-4o
    max_tokens: 8192

routing:
  default_strategy: fallback
  consensus_threshold: 0.67
  cost_tiers:
    - provider: anthropic
      model: claude-sonnet-4-20250514
      tier: standard
      cost_per_1k_input: 0.003
      cost_per_1k_output: 0.015

max_convergence_iterations: 3
observability_log: harness-metrics.jsonl

bindings:
  - event: post_generation
    artifact_type: SAD
    adapters: [anthropic, openai]
    strategy: fallback

  - event: post_generation
    artifact_type: TM
    adapters: [anthropic, openai]
    strategy: parallel_consensus
    config:
      threshold: 0.67

  - event: post_generation
    artifact_type: "*"
    adapters: [anthropic]
    strategy: fallback
```
