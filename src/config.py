"""Configuration loading for the AIEOS Agent Harness."""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import yaml

from src.models import AgentSpecies


@dataclass
class ProviderConfig:
    """One provider's settings.

    ``workspace_id`` is optional and provider-interpreted. Anthropic
    identity-linked API keys are not scoped to a workspace by themselves --
    the API refuses the request ("anthropic-workspace-id is required when
    authenticating with an identity-linked API key") until the caller names
    the workspace it acts in. It is an ID, not a secret, but it is still
    account-shaped, so the adapter falls back to ``ANTHROPIC_WORKSPACE_ID``
    from the environment and nothing forces it into a config file.
    """

    enabled: bool = False
    model: str = ""
    max_tokens: int = 8192
    workspace_id: str = ""


@dataclass
class RoutingConfig:
    default_strategy: str = "fallback"
    consensus_threshold: float = 0.67
    cost_tiers: list[dict] = field(default_factory=list)


@dataclass
class RolesConfig:
    """Which provider drives generation, and which drives validation (G-8).

    ``check_generation_validation_separation`` is one of the seven invariants,
    and ``HarnessDriver`` has always taken two adapters -- but the CLI passed
    the SAME adapter twice, so a model graded its own homework. That is the
    weakest possible reading of "separation", and the 2026-07-14 dogfood showed
    the judge is not trustworthy enough for it: the same model+prompt returned
    opposite verdicts on one hard gate across back-to-back runs.

    Both default to None = "first enabled provider", preserving the previous
    behaviour for existing configs. Naming a different validator is the point.
    """

    generate: Optional[str] = None
    validate: Optional[str] = None


@dataclass
class HarnessConfig:
    aieos_root: str = "../"
    initiative_root: str = ""
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    roles: RolesConfig = field(default_factory=RolesConfig)
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
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    # -- Build provider configs --
    providers: dict[str, ProviderConfig] = {}
    for name, pdata in raw.get("providers", {}).items():
        if isinstance(pdata, dict):
            providers[name] = ProviderConfig(
                enabled=pdata.get("enabled", False),
                model=pdata.get("model", ""),
                max_tokens=pdata.get("max_tokens", 8192),
                workspace_id=str(pdata.get("workspace_id", "") or ""),
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

    # -- Build roles config (G-8: which provider generates vs validates) --
    roles_data = raw.get("roles", {})
    if isinstance(roles_data, dict):
        roles = RolesConfig(
            generate=roles_data.get("generate"),
            validate=roles_data.get("validate"),
        )
    else:
        roles = RolesConfig()

    # -- Build top-level config --
    config = HarnessConfig(
        aieos_root=raw.get("aieos_root", "../"),
        initiative_root=raw.get("initiative_root", ""),
        providers=providers,
        roles=roles,
        routing=routing,
        max_convergence_iterations=raw.get("max_convergence_iterations", 3),
        observability_log=raw.get("observability_log", "harness-metrics.jsonl"),
        bindings=raw.get("bindings", []),
    )

    # -- Parse species in bindings --
    for binding in config.bindings:
        if isinstance(binding, dict) and "species" in binding:
            species_str = binding["species"].upper()
            try:
                binding["species"] = AgentSpecies(species_str)
            except ValueError:
                binding["species"] = AgentSpecies.DARK_FACTORY
        elif isinstance(binding, dict):
            binding["species"] = AgentSpecies.DARK_FACTORY

    # -- Environment variable overrides --
    if os.environ.get("AIEOS_ROOT"):
        config.aieos_root = os.environ["AIEOS_ROOT"]
    if os.environ.get("AIEOS_INITIATIVE_ROOT"):
        config.initiative_root = os.environ["AIEOS_INITIATIVE_ROOT"]

    return config
