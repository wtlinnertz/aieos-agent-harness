"""CLI entry point for the AIEOS Agent Harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

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
        elif name == "mock":
            from src.adapters.converging_mock import ConvergingMockAdapter

            adapters[name] = ConvergingMockAdapter(
                model_name=pconf.model or "converging-mock-v1"
            )
        elif name == "mock_fail":
            from src.adapters.converging_mock import ConvergingMockAdapter

            adapters[name] = ConvergingMockAdapter(
                provider_name="mock_fail",
                model_name=pconf.model or "failing-mock-v1",
                always_fail=True,
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
            spec_content = spec_path.read_text(encoding="utf-8")
        if template_path.exists():
            template_content = template_path.read_text(encoding="utf-8")
        if prompt_path.exists():
            prompt_content = prompt_path.read_text(encoding="utf-8")

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
        text = md_file.read_text(encoding="utf-8")
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

    artifact_content = artifact_path.read_text(encoding="utf-8")

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
            validator_prompt = vp.read_text(encoding="utf-8")
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
            validator_prompt = vp.read_text(encoding="utf-8")
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

    prompt_content = base_prompt_path.read_text(encoding="utf-8")
    spec_content = ""
    if args.base_spec:
        spec_path = Path(args.base_spec).resolve()
        if spec_path.exists():
            spec_content = spec_path.read_text(encoding="utf-8")

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
        raw = json.loads(decision_path.read_text(encoding="utf-8"))
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
                # G-14: report what apply_freeze_decision actually decided, in
                # the canonical FR-018 vocabulary. This used to be the hardcoded
                # literal "frozen" -- lowercase, not the enum, and not derived
                # from the result at all. The console then parsed this field and
                # threw it away, hardcoding its own 'frozen'. Both sides agreed
                # only because both invented the same string; any outcome other
                # than FROZEN would have been reported as FROZEN anyway.
                "status": result.status.value,
                "artifact_id": result.artifact_id,
                "path": result.path,
                "frozen_count": result.frozen_count,
                "decided_by": result.decided_by,
            }
        )
    )
    return 0


def _select_role_adapters(config: HarnessConfig, adapters: dict) -> tuple:
    """Pick the generate and validate adapters (G-8).

    ``roles.generate`` / ``roles.validate`` name providers; either unset falls
    back to the first enabled one, which is the pre-existing behaviour. Setting
    them to different providers is how you stop a model grading its own work.

    Raises ValueError naming the offender if a role points at a provider that
    isn't enabled -- silently falling back to "whatever is first" would defeat
    the entire purpose of asking for a separate validator.
    """
    def _pick(role_name: str, provider: Optional[str]):
        if provider is None:
            return next(iter(adapters.values()))
        if provider not in adapters:
            raise ValueError(
                f"roles.{role_name} names provider {provider!r}, which is not "
                f"enabled. Enabled: {sorted(adapters)}"
            )
        return adapters[provider]

    return (
        _pick("generate", config.roles.generate),
        _pick("validate", config.roles.validate),
    )


def cmd_run_artifact(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Run one artifact's lifecycle and emit a machine-readable result.

    The dark-factory conductor's subprocess seam (mirrors ADR-0003's
    console->harness CLI pattern). Because both repos use ``src`` as their import
    root, an in-process import would collide; the CLI subprocess is the clean
    cross-repo boundary. Emits
    ``{"result": "CONVERGED"|"ESCALATION_NEEDED"|"ALREADY_FROZEN"}`` on stdout,
    exit 0; a structured JSON error + non-zero otherwise.
    """
    from src.driver import HarnessDriver

    adapters = _build_adapters(config)
    if not adapters:
        print(
            json.dumps({"error": "no_providers", "message": "No providers enabled in config"}),
            file=sys.stderr,
        )
        return 1
    try:
        gen_adapter, val_adapter = _select_role_adapters(config, adapters)
    except ValueError as exc:
        print(json.dumps({"error": "bad_roles", "message": str(exc)}), file=sys.stderr)
        return 1
    driver = HarnessDriver(
        Path(args.initiative).resolve(),
        gen_adapter,
        val_adapter,
        aieos_root=Path(args.aieos_root).resolve(),
    )
    try:
        result = driver.run_artifact(args.type)
    except Exception as exc:
        print(json.dumps({"error": "run_failed", "message": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"result": result.value}))
    return 0


def cmd_read_state(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Emit the ER §1b state block as JSON (the conductor's read_layer_state seam)."""
    from src.state import read_er_state_block

    initiative = Path(args.initiative).resolve()
    er_path = initiative / "docs" / "engagement" / "er.md"
    if not er_path.exists():
        print(
            json.dumps({"error": "no_state", "message": f"ER not found: {er_path}"}),
            file=sys.stderr,
        )
        return 1
    s = read_er_state_block(er_path)
    print(
        json.dumps({
            "current_layer": s.current_layer,
            "current_artifact": s.current_artifact,
            "frozen_count": s.frozen_count,
        })
    )
    return 0


def cmd_mark_status(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Write an artifact's andon fault status (HALTED/FAULTED) to its Document
    Control block (ADR-0004). This is the andon status writer, distinct from the
    freeze writer: it REFUSES anything but HALTED/FAULTED, so the single-FROZEN-
    writer invariant (apply_freeze_decision) can never be bypassed through here.
    """
    from src.models import ArtifactStatus
    from src.state import write_artifact_status

    status = args.status.upper()
    if status not in ("HALTED", "FAULTED"):
        print(
            json.dumps({
                "error": "bad_status",
                "message": "mark-status writes only HALTED/FAULTED; FROZEN goes through `freeze`",
            }),
            file=sys.stderr,
        )
        return 2
    try:
        path = write_artifact_status(
            Path(args.initiative).resolve(), args.artifact, ArtifactStatus(status)
        )
    except ValueError as exc:
        print(json.dumps({"error": "not_found", "message": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": status, "artifact": args.artifact, "path": str(path)}))
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
    # run-artifact (dark-factory subprocess seam)
    ra = subparsers.add_parser(
        "run-artifact", help="Run one artifact lifecycle; emit JSON result"
    )
    ra.add_argument("--type", required=True, help="Artifact type (e.g. PRD)")
    ra.add_argument("--initiative", required=True, help="Path to initiative project")
    ra.add_argument("--aieos-root", required=True, help="Path to kit files root")

    # read-state (dark-factory subprocess seam)
    rs = subparsers.add_parser(
        "read-state", help="Emit the ER state block as JSON"
    )
    rs.add_argument("--initiative", required=True, help="Path to initiative project")

    # mark-status (andon fault writer — HALTED/FAULTED only)
    ms = subparsers.add_parser(
        "mark-status", help="Write an artifact andon fault status (HALTED/FAULTED)"
    )
    ms.add_argument("--initiative", required=True)
    ms.add_argument("--artifact", required=True, help="Artifact ID")
    ms.add_argument("--status", required=True, help="HALTED or FAULTED")

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
        "mark-status": cmd_mark_status,
        "run-artifact": cmd_run_artifact,
        "read-state": cmd_read_state,
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
