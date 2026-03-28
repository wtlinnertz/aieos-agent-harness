"""Auto-research engine for the AIEOS Agent Harness.

Automated metric optimization: experiments with variations of prompts,
constraints, or context and selects the configuration that best meets
a target metric. The fourth agent species — optimization against a
rate or metric rather than producing working software.
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.adapters.base import AgentAdapter
from src.models import AgentRequest, AgentResponse, LifecycleEvent

logger = logging.getLogger(__name__)


@dataclass
class Experiment:
    """Record of a single optimization experiment."""
    id: str                         # EXP-001, EXP-002, ...
    variation: str                  # The varied content (e.g., modified prompt)
    variation_description: str      # Human-readable what changed
    metric_name: str
    metric_value: float
    cost_usd: float
    latency_ms: float
    timestamp: str
    status: str                     # "success", "failure", "error"


@dataclass
class ResearchResult:
    """Final result of an auto-research optimization run."""
    target_metric: str
    target_value: str               # e.g., ">= 85"
    target_met: bool
    experiments_run: int
    best_experiment: Optional[Experiment]
    best_value: float
    experiment_history: list[Experiment] = field(default_factory=list)
    total_cost_usd: float = 0.0


class AutoResearchEngine:
    """Optimization loop that experiments with variations and selects winners.

    Uses the harness adapter protocol for execution and measurement.
    Supports multiple variation strategies: prompt_phrasing, constraint_ordering,
    context_pruning, model_comparison.
    """

    def __init__(
        self,
        adapter: AgentAdapter,
        metric_name: str,
        target: str,                            # e.g., ">= 85", "<= 0.05"
        max_experiments: int = 20,
        variation_adapter: AgentAdapter | None = None,
        batch_size: int = 5,                    # Variations per round
    ):
        self._adapter = adapter
        self._metric_name = metric_name
        self._target = target
        self._max_experiments = max_experiments
        self._var_adapter = variation_adapter or adapter
        self._batch_size = batch_size
        self._operator, self._threshold = self._parse_target(target)

    def optimize(
        self,
        base_request: AgentRequest,
        variation_strategy: str = "prompt_phrasing",
    ) -> ResearchResult:
        """Run the optimization loop."""
        history: list[Experiment] = []
        best: Optional[Experiment] = None
        best_value = float("-inf") if self._operator in (">=", ">") else float("inf")
        exp_counter = 0

        while exp_counter < self._max_experiments:
            # Generate variations
            variations = self._generate_variations(
                base_request, variation_strategy,
                prior_results=history if history else None,
                count=min(self._batch_size, self._max_experiments - exp_counter),
            )

            if not variations:
                break

            # Execute each variation
            for var_request, var_desc in variations:
                exp_counter += 1
                exp_id = f"EXP-{exp_counter:03d}"

                try:
                    start = datetime.now(timezone.utc)
                    response = self._adapter.invoke(var_request)
                    metric_val = self._measure_metric(response, self._metric_name)

                    exp = Experiment(
                        id=exp_id,
                        variation=var_request.prompt_content[:500],
                        variation_description=var_desc,
                        metric_name=self._metric_name,
                        metric_value=metric_val,
                        cost_usd=response.cost_usd,
                        latency_ms=response.latency_ms,
                        timestamp=start.isoformat(),
                        status="success",
                    )
                except Exception as e:
                    exp = Experiment(
                        id=exp_id,
                        variation=var_request.prompt_content[:500],
                        variation_description=var_desc,
                        metric_name=self._metric_name,
                        metric_value=0.0,
                        cost_usd=0.0,
                        latency_ms=0.0,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        status=f"error: {e}",
                    )

                history.append(exp)

                # Track best
                if exp.status == "success":
                    if self._is_better(exp.metric_value, best_value):
                        best = exp
                        best_value = exp.metric_value

                    # Check if target met
                    if self._target_met(exp.metric_value):
                        return ResearchResult(
                            target_metric=self._metric_name,
                            target_value=self._target,
                            target_met=True,
                            experiments_run=exp_counter,
                            best_experiment=best,
                            best_value=best_value,
                            experiment_history=history,
                            total_cost_usd=sum(e.cost_usd for e in history),
                        )

        # Budget exhausted
        return ResearchResult(
            target_metric=self._metric_name,
            target_value=self._target,
            target_met=False,
            experiments_run=exp_counter,
            best_experiment=best,
            best_value=best_value,
            experiment_history=history,
            total_cost_usd=sum(e.cost_usd for e in history),
        )

    def _generate_variations(
        self,
        base_request: AgentRequest,
        strategy: str,
        prior_results: list[Experiment] | None = None,
        count: int = 5,
    ) -> list[tuple[AgentRequest, str]]:
        """Generate N variations of the base request.

        Returns list of (modified_request, description) tuples.
        """
        if strategy == "prompt_phrasing":
            return self._vary_prompt_phrasing(base_request, prior_results, count)
        elif strategy == "constraint_ordering":
            return self._vary_constraint_ordering(base_request, count)
        elif strategy == "context_pruning":
            return self._vary_context_pruning(base_request)
        elif strategy == "model_comparison":
            # For model_comparison, return the same request N times
            # (caller routes to different adapters)
            return [(base_request, "baseline (same request)")] * count
        else:
            raise ValueError(f"Unknown variation strategy: {strategy}")

    def _vary_prompt_phrasing(
        self,
        base: AgentRequest,
        prior: list[Experiment] | None,
        count: int,
    ) -> list[tuple[AgentRequest, str]]:
        """Generate prompt phrasing variations using the variation adapter."""
        # Build a meta-prompt asking the AI to generate variations
        prior_context = ""
        if prior:
            best_prior = sorted(
                [e for e in prior if e.status == "success"],
                key=lambda e: e.metric_value,
                reverse=(self._operator in (">=", ">")),
            )[:3]
            if best_prior:
                prior_lines = [
                    f"  - Score {e.metric_value}: {e.variation_description}"
                    for e in best_prior
                ]
                prior_context = (
                    "\n\nPrior best results:\n" + "\n".join(prior_lines) +
                    "\n\nGenerate variations that build on the strongest patterns above."
                )

        meta_prompt = (
            f"Generate {count} alternative phrasings of the following prompt. "
            f"Each should have the same intent but different structure, emphasis, "
            f"or constraint wording. Optimizing for: {self._metric_name} {self._target}.\n\n"
            f"Original prompt:\n---\n{base.prompt_content[:2000]}\n---\n"
            f"{prior_context}\n\n"
            f"Output exactly {count} variations, each on its own line prefixed with "
            f"'VARIATION N: ' followed by the full alternative prompt text."
        )

        meta_request = AgentRequest(
            artifact_type="meta-variation",
            event=LifecycleEvent.PRE_GENERATION,
            spec_content="",
            template_content="",
            prompt_content=meta_prompt,
            upstream_artifacts={},
            current_artifact=None,
            correction_constraints=[],
            metadata={"purpose": "generate_variations"},
        )

        try:
            meta_response = self._var_adapter.invoke(meta_request)
            # Parse variations from response
            variations = []
            for line in meta_response.content.split("\n"):
                m = re.match(r"VARIATION\s+\d+:\s*(.*)", line, re.IGNORECASE)
                if m:
                    var_text = m.group(1).strip()
                    if var_text:
                        var_request = AgentRequest(
                            artifact_type=base.artifact_type,
                            event=base.event,
                            spec_content=base.spec_content,
                            template_content=base.template_content,
                            prompt_content=var_text,
                            upstream_artifacts=base.upstream_artifacts,
                            current_artifact=base.current_artifact,
                            correction_constraints=base.correction_constraints,
                            metadata={**base.metadata, "variation": "prompt_phrasing"},
                        )
                        variations.append((var_request, f"prompt variation: {var_text[:80]}"))

            return variations[:count]
        except Exception as e:
            logger.warning("Failed to generate prompt variations: %s", e)
            return []

    def _vary_constraint_ordering(
        self, base: AgentRequest, count: int,
    ) -> list[tuple[AgentRequest, str]]:
        """Generate variations by reordering correction_constraints."""
        import random
        constraints = list(base.correction_constraints)
        if len(constraints) < 2:
            return [(base, "baseline (no reordering possible)")]

        variations = []
        seen = set()
        for _ in range(count * 3):  # Over-generate to find unique orderings
            shuffled = list(constraints)
            random.shuffle(shuffled)
            key = tuple(shuffled)
            if key not in seen:
                seen.add(key)
                var_request = AgentRequest(
                    artifact_type=base.artifact_type,
                    event=base.event,
                    spec_content=base.spec_content,
                    template_content=base.template_content,
                    prompt_content=base.prompt_content,
                    upstream_artifacts=base.upstream_artifacts,
                    current_artifact=base.current_artifact,
                    correction_constraints=shuffled,
                    metadata={**base.metadata, "variation": "constraint_ordering"},
                )
                desc = f"constraints reordered: {', '.join(c[:20] for c in shuffled[:3])}"
                variations.append((var_request, desc))
                if len(variations) >= count:
                    break

        return variations

    def _vary_context_pruning(
        self, base: AgentRequest,
    ) -> list[tuple[AgentRequest, str]]:
        """Generate variations by removing one upstream artifact at a time."""
        variations = []
        for key in base.upstream_artifacts:
            pruned = {k: v for k, v in base.upstream_artifacts.items() if k != key}
            var_request = AgentRequest(
                artifact_type=base.artifact_type,
                event=base.event,
                spec_content=base.spec_content,
                template_content=base.template_content,
                prompt_content=base.prompt_content,
                upstream_artifacts=pruned,
                current_artifact=base.current_artifact,
                correction_constraints=base.correction_constraints,
                metadata={**base.metadata, "variation": "context_pruning", "pruned": key},
            )
            variations.append((var_request, f"pruned upstream: {key}"))

        # Also include baseline (no pruning)
        variations.insert(0, (base, "baseline (no pruning)"))
        return variations

    @staticmethod
    def _measure_metric(response: AgentResponse, metric_name: str) -> float:
        """Extract metric value from response."""
        if metric_name == "cost_usd":
            return response.cost_usd
        elif metric_name == "latency_ms":
            return response.latency_ms
        elif metric_name == "token_efficiency":
            return response.tokens_out / max(response.tokens_in, 1)
        elif metric_name == "completeness_score":
            # Try to parse from validation JSON in response content
            try:
                from src.convergence import parse_validation_result
                result = parse_validation_result(response)
                return float(result.completeness_score)
            except Exception:
                # Try direct JSON parse
                try:
                    data = json.loads(response.content)
                    return float(data.get("completeness_score", 0))
                except Exception:
                    return 0.0
        elif metric_name == "first_pass_rate":
            try:
                from src.convergence import parse_validation_result
                result = parse_validation_result(response)
                return 1.0 if result.status == "PASS" else 0.0
            except Exception:
                return 0.0
        else:
            raise ValueError(f"Unknown metric: {metric_name}")

    @staticmethod
    def _parse_target(target: str) -> tuple[str, float]:
        """Parse '>= 85' into ('>=', 85.0)."""
        m = re.match(r"(>=?|<=?|==)\s*([\d.]+)", target.strip())
        if not m:
            raise ValueError(f"Cannot parse target: '{target}'. Expected format: '>= 85'")
        return m.group(1), float(m.group(2))

    def _target_met(self, value: float) -> bool:
        """Check if metric value meets target."""
        op, threshold = self._operator, self._threshold
        if op == ">=":
            return value >= threshold
        elif op == ">":
            return value > threshold
        elif op == "<=":
            return value <= threshold
        elif op == "<":
            return value < threshold
        elif op == "==":
            return abs(value - threshold) < 0.001
        return False

    def _is_better(self, new_value: float, current_best: float) -> bool:
        """Check if new value is better than current best (direction-aware)."""
        if self._operator in (">=", ">"):
            return new_value > current_best
        else:
            return new_value < current_best
