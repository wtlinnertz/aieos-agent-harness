"""Configuration loading for the AIEOS Agent Harness."""

import os
from pathlib import Path
from dataclasses import dataclass, field

import yaml


@dataclass
class ProviderConfig:
    enabled: bool = False
    model: str = ""
    max_tokens: int = 8192


@dataclass
class RoutingConfig:
    default_strategy: str = "fallback"
    consensus_threshold: float = 0.67
    cost_tiers: list[dict] = field(default_factory=list)


@dataclass
class HarnessConfig:
    aieos_root: str = "../"
    initiative_root: str = ""
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    max_convergence_iterations: int = 3
    observability_log: str = "harness-metrics.jsonl"
    bindings: list[dict] = field(default_factory=list)


def load_config(path: Path) -> HarnessConfig:
    """Load config from YAML file with env var overrides for credentials.

    Credentials (ANTHROPIC_API_KEY, OPENAI_API_KEY) are read from
    environment variables only -- never stored in the YAML file.

    AIEOS_ROOT and AIEOS_INITIATIVE_ROOT env vars override their
    corresponding YAML values when set.

    If the YAML file does not exist, returns a default HarnessConfig.
    """
    raw: dict = {}
    if path.exists():
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}

    # -- Build provider configs --
    providers: dict[str, ProviderConfig] = {}
    for name, pdata in raw.get("providers", {}).items():
        if isinstance(pdata, dict):
            providers[name] = ProviderConfig(
                enabled=pdata.get("enabled", False),
                model=pdata.get("model", ""),
                max_tokens=pdata.get("max_tokens", 8192),
            )

    # -- Build routing config --
    routing_data = raw.get("routing", {})
    if isinstance(routing_data, dict):
        routing = RoutingConfig(
            default_strategy=routing_data.get("default_strategy", "fallback"),
            consensus_threshold=routing_data.get("consensus_threshold", 0.67),
            cost_tiers=routing_data.get("cost_tiers", []),
        )
    else:
        routing = RoutingConfig()

    # -- Build top-level config --
    config = HarnessConfig(
        aieos_root=raw.get("aieos_root", "../"),
        initiative_root=raw.get("initiative_root", ""),
        providers=providers,
        routing=routing,
        max_convergence_iterations=raw.get("max_convergence_iterations", 3),
        observability_log=raw.get("observability_log", "harness-metrics.jsonl"),
        bindings=raw.get("bindings", []),
    )

    # -- Environment variable overrides --
    if os.environ.get("AIEOS_ROOT"):
        config.aieos_root = os.environ["AIEOS_ROOT"]
    if os.environ.get("AIEOS_INITIATIVE_ROOT"):
        config.initiative_root = os.environ["AIEOS_INITIATIVE_ROOT"]

    return config
