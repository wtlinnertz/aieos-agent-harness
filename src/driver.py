"""HarnessDriver -- the small, stable facade over the agent harness (ADR-0002).

The dark factory (``aieos-dark-factory``) is a pure control plane. It imports
*only* this facade, never harness internals. That boundary is the whole
justification for the separate repo; if the conductor reaches past these three
operations into ``convergence``, ``state``, or ``invariants`` directly, the
separation ADR-0002 draws has leaked.

Three operations, held stable:

- ``run_artifact_lifecycle()`` -> ``CONVERGED | ESCALATION_NEEDED``
- ``read_layer_state()`` -> the ER state block
- ``apply_freeze_decision()`` -> the single ``FROZEN`` writer (delegates to
  :func:`src.freeze.apply_freeze_decision`)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.adapters.base import AgentAdapter
from src.convergence import ConvergenceLoop
from src.freeze import apply_freeze_decision
from src.models import (
    AgentRequest,
    ERStateBlock,
    LifecycleEvent,
    FreezeGateDecision,
    FreezeResult,
    LifecycleResult,
)
from src.state import read_er_state_block


class HarnessDriver:
    """Stable orchestration seam between a conductor and the harness engine."""

    def __init__(
        self,
        initiative_path: Path,
        generate_adapter: AgentAdapter,
        validate_adapter: AgentAdapter,
        *,
        max_iterations: int = 3,
        er_path: Optional[Path] = None,
        journal_path: Optional[Path] = None,
        aieos_root: Optional[Path] = None,
    ) -> None:
        self._initiative = Path(initiative_path)
        self._gen = generate_adapter
        self._val = validate_adapter
        self._max_iterations = max_iterations
        # Kit files root (docs/specs|artifacts|prompts|validators) for run_artifact.
        self._aieos_root = Path(aieos_root) if aieos_root is not None else None
        # Default the on-disk substrate to the initiative's engagement record.
        self._er_path = (
            Path(er_path)
            if er_path is not None
            else self._initiative / "docs" / "engagement" / "er.md"
        )
        self._journal_path = (
            Path(journal_path)
            if journal_path is not None
            else self._initiative / "docs" / "engagement" / "journal.md"
        )

    def run_artifact(self, artifact_type: str) -> LifecycleResult:
        """Artifact-type-level lifecycle -- the conductor-facing entry point.

        Resolves the artifact type's kit files, assembles the generate/validate
        requests, and runs the bounded loop. Request assembly stays inside the
        harness so the dark-factory control plane can call the facade with just
        an artifact type and never needs harness internals (AgentRequest,
        LifecycleEvent, kit resolution). Requires ``aieos_root``.
        """
        if self._aieos_root is None:
            raise ValueError(
                "run_artifact requires aieos_root (kit files location) at construction"
            )
        from src.cli import _collect_upstream_artifacts, _resolve_kit_files

        spec, template, prompt = _resolve_kit_files(self._aieos_root, artifact_type)
        if not spec:
            raise ValueError(
                f"No kit spec for artifact type {artifact_type!r} under {self._aieos_root}"
            )
        upstream = _collect_upstream_artifacts(self._initiative)

        validator_prompt = ""
        for kit_dir in sorted(self._aieos_root.iterdir()):
            if not kit_dir.is_dir() or not kit_dir.name.startswith("aieos-"):
                continue
            vp = (
                kit_dir / "docs" / "validators" / f"{artifact_type.lower()}-validator.md"
            )
            if vp.exists():
                validator_prompt = vp.read_text()
                break

        gen_request = AgentRequest(
            artifact_type=artifact_type,
            event=LifecycleEvent.PRE_GENERATION,
            spec_content=spec,
            template_content=template,
            prompt_content=prompt,
            upstream_artifacts=upstream,
            current_artifact=None,
            correction_constraints=[],
            metadata={"initiative": str(self._initiative), "artifact_id": artifact_type},
        )
        val_request = AgentRequest(
            artifact_type=artifact_type,
            event=LifecycleEvent.PRE_VALIDATION,
            spec_content=spec,
            template_content=template,
            prompt_content=validator_prompt or prompt,
            upstream_artifacts=upstream,
            current_artifact=None,
            correction_constraints=[],
            metadata={"initiative": str(self._initiative)},
        )
        return self.run_artifact_lifecycle(gen_request, val_request)

    def run_artifact_lifecycle(
        self,
        gen_request: AgentRequest,
        val_request: AgentRequest,
    ) -> LifecycleResult:
        """Drive one artifact through the bounded generate/validate loop.

        Returns ``CONVERGED`` when validation reaches PASS within the budget,
        ``ESCALATION_NEEDED`` when the budget is exhausted without a PASS. It
        never freezes -- promotion always requires a separate human decision
        through :meth:`apply_freeze_decision`.
        """
        loop = ConvergenceLoop(
            self._gen, self._val, max_iterations=self._max_iterations
        )
        _response, result, _state = loop.run(gen_request, val_request)
        return (
            LifecycleResult.CONVERGED
            if result.status == "PASS"
            else LifecycleResult.ESCALATION_NEEDED
        )

    def read_layer_state(self) -> ERStateBlock:
        """Return the current ER state block (the layer/initiative state)."""
        return read_er_state_block(self._er_path)

    def apply_freeze_decision(
        self, decision: FreezeGateDecision
    ) -> FreezeResult:
        """Promote an artifact to ``FROZEN`` through the single freeze authority.

        Delegates to :func:`src.freeze.apply_freeze_decision`, wired to this
        driver's ER and Journal paths. Raises :class:`src.freeze.FreezeError`
        on any refused freeze, writing nothing.
        """
        return apply_freeze_decision(
            self._initiative,
            decision,
            er_path=self._er_path if self._er_path.exists() else None,
            journal_path=(
                self._journal_path if self._journal_path.exists() else None
            ),
        )
