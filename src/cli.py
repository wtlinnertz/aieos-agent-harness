"""CLI entry point for the AIEOS Agent Harness."""

from __future__ import annotations

import argparse
import json
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


def cmd_research(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Run auto-research optimization against a target metric."""
    from src.auto_research import AutoResearchEngine

    adapters = _build_adapters(config)
    if not adapters:
        print("ERROR: No providers enabled. Check harness.yaml.", file=sys.stderr)
        return 1

    base_prompt_path = Path(args.base_prompt).resolve()
    if not base_prompt_path.exists():
        print(f"ERROR: Base prompt not found: {base_prompt_path}", file=sys.stderr)
        return 1

    prompt_content = base_prompt_path.read_text()
    spec_content = ""
    if args.base_spec:
        spec_path = Path(args.base_spec).resolve()
        if spec_path.exists():
            spec_content = spec_path.read_text()

    request = AgentRequest(
        artifact_type="research",
        event=LifecycleEvent.PRE_GENERATION,
        spec_content=spec_content,
        template_content="",
        prompt_content=prompt_content,
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"base_prompt": str(base_prompt_path)},
    )

    adapter_name, adapter = next(iter(adapters.items()))
    print(f"Running auto-research: {args.metric} {args.target}")
    print(f"Strategy: {args.strategy} | Max experiments: {args.max_experiments}")
    print(f"Provider: {adapter_name}\n")

    engine = AutoResearchEngine(
        adapter=adapter,
        metric_name=args.metric,
        target=args.target,
        max_experiments=args.max_experiments,
    )

    result = engine.optimize(request, variation_strategy=args.strategy)

    print(f"Target met: {result.target_met}")
    print(f"Experiments run: {result.experiments_run}")
    print(f"Best value: {result.best_value}")
    print(f"Total cost: ${result.total_cost_usd:.4f}")

    if result.best_experiment:
        print(f"\n--- Best Experiment ({result.best_experiment.id}) ---")
        print(f"Metric: {result.best_experiment.metric_value}")
        print(f"Description: {result.best_experiment.variation_description}")

    return 0 if result.target_met else 1


def cmd_freeze(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Freeze an artifact through the single freeze authority (ADR-0003).

    The seam a non-Python driver (the console) shells out to. Reads a serialized
    FreezeGateDecision from --decision, applies it via apply_freeze_decision, and
    speaks a machine-readable stdout + exit-code contract: exit 0 with a JSON
    payload on success; non-zero with a structured JSON error (and nothing
    written) on any refused freeze.
    """
    from src.freeze import FreezeError, apply_freeze_decision
    from src.models import DecisionOutcome, FreezeGateDecision

    initiative_path = Path(args.initiative).resolve()
    decision_path = Path(args.decision).resolve()
    if not decision_path.exists():
        print(
            json.dumps({"error": "bad_request", "message": f"Decision file not found: {decision_path}"}),
            file=sys.stderr,
        )
        return 2

    try:
        raw = json.loads(decision_path.read_text())
    except json.JSONDecodeError as exc:
        print(
            json.dumps({"error": "bad_request", "message": f"Invalid decision JSON: {exc}"}),
            file=sys.stderr,
        )
        return 2

    # --decided-by / --artifact override the JSON when provided.
    artifact_id = args.artifact or raw.get("artifact_id", "")
    decided_by = args.decided_by or raw.get("decided_by", "")
    outcome_str = str(raw.get("outcome", "")).upper()
    try:
        outcome = DecisionOutcome(outcome_str)
    except ValueError:
        print(
            json.dumps({"error": "bad_request", "message": f"Unknown decision outcome: {outcome_str!r}"}),
            file=sys.stderr,
        )
        return 2

    decision = FreezeGateDecision(
        artifact_id=artifact_id,
        outcome=outcome,
        content_hash=raw.get("content_hash", ""),
        decided_by=decided_by,
        auto_freeze_attempted=bool(raw.get("auto_freeze_attempted", False)),
        conditions=list(raw.get("conditions", [])),
        rationale=raw.get("rationale", ""),
    )

    # Full bookkeeping (frozen count + journal) when the engagement record exists.
    er_path = initiative_path / "docs" / "engagement" / "er.md"
    journal_path = initiative_path / "docs" / "engagement" / "journal.md"

    try:
        result = apply_freeze_decision(
            initiative_path,
            decision,
            er_path=er_path if er_path.exists() else None,
            journal_path=journal_path if journal_path.exists() else None,
        )
    except FreezeError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "status": "frozen",
                "artifact_id": result.artifact_id,
                "path": result.path,
                "frozen_count": result.frozen_count,
                "decided_by": result.decided_by,
            }
        )
    )
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

    # freeze
    fr = subparsers.add_parser(
        "freeze", help="Freeze an artifact from a decision record (ADR-0003)"
    )
    fr.add_argument("--initiative", required=True, help="Path to initiative project")
    fr.add_argument("--artifact", help="Artifact ID (overrides decision JSON)")
    fr.add_argument(
        "--decision", required=True, help="Path to serialized FreezeGateDecision JSON"
    )
    fr.add_argument("--decided-by", help="Human identity (overrides decision JSON)")

    # health
    subparsers.add_parser("health", help="Check provider health")

    # costs
    costs = subparsers.add_parser("costs", help="Show cost summary")
    costs.add_argument("--initiative", help="Filter by initiative")

    # research
    research = subparsers.add_parser(
        "research", help="Auto-research: optimize a metric"
    )
    research.add_argument(
        "--metric",
        required=True,
        help="Metric to optimize (completeness_score, cost_usd, latency_ms, token_efficiency, first_pass_rate)",
    )
    research.add_argument(
        "--target",
        required=True,
        help="Target value (e.g., '>= 85', '<= 0.05')",
    )
    research.add_argument(
        "--base-prompt",
        required=True,
        help="Path to base prompt file",
    )
    research.add_argument(
        "--base-spec",
        help="Path to spec file (optional context)",
    )
    research.add_argument(
        "--strategy",
        default="prompt_phrasing",
        help="Variation strategy",
    )
    research.add_argument(
        "--max-experiments",
        type=int,
        default=20,
        help="Max experiments to run",
    )

    args = parser.parse_args(argv)
    config = load_config(Path(args.config))

    handlers = {
        "generate": cmd_generate,
        "validate": cmd_validate,
        "lifecycle": cmd_lifecycle,
        "freeze": cmd_freeze,
        "health": cmd_health,
        "costs": cmd_costs,
        "research": cmd_research,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args, config)


if __name__ == "__main__":
    sys.exit(main())
