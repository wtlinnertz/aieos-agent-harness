"""CLI entry point for the AIEOS Agent Harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import HarnessConfig, load_config
from src.models import (
    AgentRequest,
    AgentResponse,
    HealthStatus,
    LifecycleEvent,
    RoutingStrategy,
)
from src.observability import ObservabilityLayer


def _build_adapters(config: HarnessConfig) -> dict[str, object]:
    """Instantiate enabled provider adapters from config."""
    adapters: dict[str, object] = {}

    for name, pconf in config.providers.items():
        if not pconf.enabled:
            continue

        if name == "anthropic":
            from src.adapters.anthropic import AnthropicAdapter

            adapters[name] = AnthropicAdapter(
                model=pconf.model, max_tokens=pconf.max_tokens
            )
        elif name == "openai":
            from src.adapters.openai import OpenAIAdapter

            adapters[name] = OpenAIAdapter(
                model=pconf.model, max_tokens=pconf.max_tokens
            )

    return adapters


def _resolve_kit_files(
    aieos_root: Path, artifact_type: str
) -> tuple[str, str, str]:
    """Locate spec, template, and prompt files for an artifact type.

    Searches each kit under aieos_root for matching files.
    Returns (spec_content, template_content, prompt_content).
    """
    type_lower = artifact_type.lower()
    spec_content = ""
    template_content = ""
    prompt_content = ""

    for kit_dir in sorted(aieos_root.iterdir()):
        if not kit_dir.is_dir() or not kit_dir.name.startswith("aieos-"):
            continue

        spec_path = kit_dir / "docs" / "specs" / f"{type_lower}-spec.md"
        template_path = (
            kit_dir / "docs" / "artifacts" / f"{type_lower}-template.md"
        )
        prompt_path = kit_dir / "docs" / "prompts" / f"{type_lower}-prompt.md"

        if spec_path.exists():
            spec_content = spec_path.read_text()
        if template_path.exists():
            template_content = template_path.read_text()
        if prompt_path.exists():
            prompt_content = prompt_path.read_text()

        if spec_content:
            break

    return spec_content, template_content, prompt_content


def _collect_upstream_artifacts(initiative_path: Path) -> dict[str, str]:
    """Read all frozen artifacts from the initiative's docs/sdlc directory."""
    import re

    sdlc_dir = initiative_path / "docs" / "sdlc"
    artifacts: dict[str, str] = {}
    if not sdlc_dir.exists():
        return artifacts

    for md_file in sorted(sdlc_dir.glob("*.md")):
        text = md_file.read_text()
        id_match = re.search(
            r"\|\s*Artifact\s+ID\s*\|\s*(.*?)\s*\|", text, re.IGNORECASE
        )
        status_match = re.search(
            r"\|\s*Status\s*\|\s*(.*?)\s*\|", text, re.IGNORECASE
        )
        if id_match and status_match:
            artifact_id = id_match.group(1).strip()
            status = status_match.group(1).strip().upper()
            if status == "FROZEN":
                artifacts[artifact_id] = text

    return artifacts


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_generate(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Run generation for an artifact type."""
    adapters = _build_adapters(config)
    if not adapters:
        print("ERROR: No providers enabled. Check harness.yaml.", file=sys.stderr)
        return 1

    aieos_root = Path(config.aieos_root).resolve()
    initiative_path = Path(args.initiative).resolve()

    spec, template, prompt = _resolve_kit_files(aieos_root, args.type)
    if not spec:
        print(
            f"ERROR: Could not find spec files for artifact type '{args.type}'.",
            file=sys.stderr,
        )
        return 1

    upstream = _collect_upstream_artifacts(initiative_path)

    request = AgentRequest(
        artifact_type=args.type,
        event=LifecycleEvent.PRE_GENERATION,
        spec_content=spec,
        template_content=template,
        prompt_content=prompt,
        upstream_artifacts=upstream,
        current_artifact=None,
        correction_constraints=[],
        metadata={"initiative": str(initiative_path)},
    )

    # Use first available adapter
    adapter_name, adapter = next(iter(adapters.items()))
    print(f"Generating {args.type} using {adapter_name}...")

    try:
        response = adapter.invoke(request)  # type: ignore[union-attr]
    except Exception as exc:
        print(f"ERROR: Generation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Generated {len(response.content)} characters")
    print(f"Tokens: {response.tokens_in} in / {response.tokens_out} out")
    print(f"Cost: ${response.cost_usd:.4f}")
    print(f"Latency: {response.latency_ms:.0f}ms")
    print("\n--- Generated Content ---\n")
    print(response.content)
    return 0


def cmd_validate(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Run validation for an existing artifact."""
    adapters = _build_adapters(config)
    if not adapters:
        print("ERROR: No providers enabled. Check harness.yaml.", file=sys.stderr)
        return 1

    artifact_path = Path(args.artifact).resolve()
    if not artifact_path.exists():
        print(f"ERROR: Artifact not found: {artifact_path}", file=sys.stderr)
        return 1

    artifact_content = artifact_path.read_text()

    # Infer artifact type from filename (e.g., "03-sad.md" -> "SAD")
    stem = artifact_path.stem
    parts = stem.split("-", 1)
    artifact_type = parts[-1].upper() if len(parts) > 1 else stem.upper()

    aieos_root = Path(config.aieos_root).resolve()
    spec, template, prompt = _resolve_kit_files(aieos_root, artifact_type)

    # Look for validator prompt instead
    validator_prompt = ""
    for kit_dir in sorted(aieos_root.iterdir()):
        if not kit_dir.is_dir() or not kit_dir.name.startswith("aieos-"):
            continue
        vp = (
            kit_dir
            / "docs"
            / "validators"
            / f"{artifact_type.lower()}-validator.md"
        )
        if vp.exists():
            validator_prompt = vp.read_text()
            break

    request = AgentRequest(
        artifact_type=artifact_type,
        event=LifecycleEvent.PRE_VALIDATION,
        spec_content=spec,
        template_content=template,
        prompt_content=validator_prompt or prompt,
        upstream_artifacts={},
        current_artifact=artifact_content,
        correction_constraints=[],
        metadata={"artifact_path": str(artifact_path)},
    )

    adapter_name, adapter = next(iter(adapters.items()))
    print(f"Validating {artifact_type} using {adapter_name}...")

    try:
        response = adapter.invoke(request)  # type: ignore[union-attr]
    except Exception as exc:
        print(f"ERROR: Validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Tokens: {response.tokens_in} in / {response.tokens_out} out")
    print(f"Cost: ${response.cost_usd:.4f}")
    print("\n--- Validation Result ---\n")
    print(response.content)
    return 0


def cmd_lifecycle(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Full artifact lifecycle: generate -> validate -> present for freeze."""
    adapters = _build_adapters(config)
    if not adapters:
        print("ERROR: No providers enabled. Check harness.yaml.", file=sys.stderr)
        return 1

    aieos_root = Path(config.aieos_root).resolve()
    initiative_path = Path(args.initiative).resolve()

    spec, template, prompt = _resolve_kit_files(aieos_root, args.type)
    if not spec:
        print(
            f"ERROR: Could not find spec files for artifact type '{args.type}'.",
            file=sys.stderr,
        )
        return 1

    upstream = _collect_upstream_artifacts(initiative_path)
    adapter_name, adapter = next(iter(adapters.items()))

    # Step 1: Generate
    print(f"[1/2] Generating {args.type} using {adapter_name}...")
    gen_request = AgentRequest(
        artifact_type=args.type,
        event=LifecycleEvent.PRE_GENERATION,
        spec_content=spec,
        template_content=template,
        prompt_content=prompt,
        upstream_artifacts=upstream,
        current_artifact=None,
        correction_constraints=[],
        metadata={"initiative": str(initiative_path)},
    )

    try:
        gen_response = adapter.invoke(gen_request)  # type: ignore[union-attr]
    except Exception as exc:
        print(f"ERROR: Generation failed: {exc}", file=sys.stderr)
        return 1

    print(f"  Generated {len(gen_response.content)} characters (${gen_response.cost_usd:.4f})")

    # Step 2: Validate
    print(f"[2/2] Validating {args.type}...")

    # Look for validator prompt
    validator_prompt = ""
    for kit_dir in sorted(aieos_root.iterdir()):
        if not kit_dir.is_dir() or not kit_dir.name.startswith("aieos-"):
            continue
        vp = (
            kit_dir
            / "docs"
            / "validators"
            / f"{args.type.lower()}-validator.md"
        )
        if vp.exists():
            validator_prompt = vp.read_text()
            break

    val_request = AgentRequest(
        artifact_type=args.type,
        event=LifecycleEvent.PRE_VALIDATION,
        spec_content=spec,
        template_content=template,
        prompt_content=validator_prompt or prompt,
        upstream_artifacts=upstream,
        current_artifact=gen_response.content,
        correction_constraints=[],
        metadata={"initiative": str(initiative_path)},
    )

    try:
        val_response = adapter.invoke(val_request)  # type: ignore[union-attr]
    except Exception as exc:
        print(f"ERROR: Validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"  Validation complete (${val_response.cost_usd:.4f})")

    total_cost = gen_response.cost_usd + val_response.cost_usd
    print(f"\nTotal cost: ${total_cost:.4f}")

    print("\n--- Validation Result ---\n")
    print(val_response.content)

    print(
        "\nArtifact ready for human review. "
        "Freeze? (harness does not auto-freeze)"
    )
    return 0


def cmd_health(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Check all provider health."""
    adapters = _build_adapters(config)

    if not adapters:
        print("No providers enabled.")
        return 0

    print(f"{'Provider':<20} {'Model':<30} {'Status':<10}")
    print("-" * 60)

    all_ok = True
    for name, adapter in adapters.items():
        status = adapter.health()  # type: ignore[union-attr]
        model = adapter.model_name  # type: ignore[union-attr]
        status_str = status.value
        print(f"{name:<20} {model:<30} {status_str:<10}")
        if status != HealthStatus.OK:
            all_ok = False

    # Also check observability log
    obs = ObservabilityLayer(Path(config.observability_log))
    health = obs.provider_health_summary()
    if health:
        print("\n--- Historical Health ---")
        print(f"{'Provider':<20} {'Invocations':<15} {'Failures':<10} {'Avg Latency':<15} {'Status':<10}")
        print("-" * 70)
        for provider, data in health.items():
            print(
                f"{provider:<20} "
                f"{data['total_invocations']:<15} "
                f"{data['failures']:<10} "
                f"{data['avg_latency_ms']:.0f}ms{'':<10} "
                f"{data['current_status']:<10}"
            )

    return 0 if all_ok else 1


def cmd_costs(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Show cost summary from observability log."""
    obs = ObservabilityLayer(Path(config.observability_log))
    initiative = getattr(args, "initiative", None)
    summary = obs.cost_summary(initiative=initiative)

    if summary["invocation_count"] == 0:
        print("No invocations recorded yet.")
        return 0

    print(f"Total invocations: {summary['invocation_count']}")
    print(f"Total cost: ${summary['total_cost']:.4f}")

    if summary["cost_by_provider"]:
        print("\nCost by provider:")
        for provider, cost in summary["cost_by_provider"].items():
            print(f"  {provider}: ${cost:.4f}")

    if summary["cost_by_artifact_type"]:
        print("\nCost by artifact type:")
        for atype, cost in summary["cost_by_artifact_type"].items():
            print(f"  {atype}: ${cost:.4f}")

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to command handler."""
    parser = argparse.ArgumentParser(
        description="AIEOS Agent Harness — multi-agent orchestration for AIEOS"
    )
    parser.add_argument(
        "--config",
        default="harness.yaml",
        help="Path to harness config file",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    gen = subparsers.add_parser("generate", help="Generate an artifact")
    gen.add_argument(
        "--type", required=True, help="Artifact type (e.g., SAD, TDD)"
    )
    gen.add_argument(
        "--initiative", required=True, help="Path to initiative project"
    )

    # validate
    val = subparsers.add_parser("validate", help="Validate an artifact")
    val.add_argument(
        "--artifact", required=True, help="Path to artifact file"
    )

    # lifecycle
    lc = subparsers.add_parser("lifecycle", help="Full artifact lifecycle")
    lc.add_argument(
        "--type", required=True, help="Artifact type"
    )
    lc.add_argument(
        "--initiative", required=True, help="Path to initiative project"
    )

    # health
    subparsers.add_parser("health", help="Check provider health")

    # costs
    costs = subparsers.add_parser("costs", help="Show cost summary")
    costs.add_argument("--initiative", help="Filter by initiative")

    args = parser.parse_args(argv)
    config = load_config(Path(args.config))

    handlers = {
        "generate": cmd_generate,
        "validate": cmd_validate,
        "lifecycle": cmd_lifecycle,
        "health": cmd_health,
        "costs": cmd_costs,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args, config)


if __name__ == "__main__":
    sys.exit(main())
