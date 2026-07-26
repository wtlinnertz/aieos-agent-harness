"""Convergence loop for generate-validate cycles in the AIEOS Agent Harness."""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from src.adapters.base import AgentAdapter
from src.models import (
    AgentRequest,
    AgentResponse,
    ConvergenceState,
    LifecycleEvent,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class TruncatedGenerationError(RuntimeError):
    """Generation stopped at the output ceiling, so the artifact is incomplete (G-7).

    Raised instead of validating-and-retrying, because a truncated artifact is
    structurally incomplete by construction: it can never satisfy the spec's
    hard gates no matter how many times it is regenerated at the same ceiling.
    The old behaviour paid for the full convergence budget (~6 provider calls)
    to relearn that, then reported a generic ESCALATION_NEEDED that looked
    identical to a genuine quality failure.

    This is a configuration fault with an obvious operator action -- raise
    max_tokens -- so it stops the run loudly on the FIRST call and names the
    ceiling it hit.
    """

    def __init__(self, artifact_type: str, iteration: int, tokens_out: int) -> None:
        self.artifact_type = artifact_type
        self.iteration = iteration
        self.tokens_out = tokens_out
        super().__init__(
            f"{artifact_type} generation truncated at the provider's output "
            f"ceiling on iteration {iteration} ({tokens_out} output tokens). "
            "The artifact is incomplete and cannot pass validation; retrying at "
            "the same ceiling would burn the convergence budget for nothing. "
            "Raise max_tokens for this provider in harness.yaml."
        )


def parse_validation_result(response: AgentResponse) -> ValidationResult:
    """Extract JSON from response content and parse into ValidationResult.

    Look for JSON block in the response (between ```json and ``` or raw JSON).
    Raise ValueError if not valid validation format.
    """
    content = response.content.strip()

    # Try fenced JSON block first
    fenced = re.search(r"```json\s*\n(.*?)\n\s*```", content, re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    else:
        # Try raw JSON (starts with {)
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            raw = json_match.group(0)
        else:
            raise ValueError(
                f"No JSON found in validation response: {content[:200]}"
            )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in validation response: {exc}") from exc

    required = {"status", "summary", "hard_gates", "blocking_issues"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Validation JSON missing required fields: {missing}")

    return ValidationResult(
        status=data["status"],
        summary=data["summary"],
        hard_gates=data.get("hard_gates", {}),
        blocking_issues=data.get("blocking_issues", []),
        warnings=data.get("warnings", []),
        completeness_score=int(data.get("completeness_score", 0)),
    )


class ConvergenceLoop:
    """Bounded generate-validate loop with staleness and oscillation detection.

    Supports optional lens reviewers: after validation PASS, run lens
    reviews. If any lens returns FAIL, feed findings back as correction
    constraints and re-generate. The lens review loop shares the same
    max_iterations budget as the validator convergence loop.
    """

    def __init__(
        self,
        generate_adapter: AgentAdapter,
        validate_adapter: AgentAdapter,
        max_iterations: int = 3,
        lens_adapters: dict[str, AgentAdapter] | None = None,
    ) -> None:
        self._gen = generate_adapter
        self._val = validate_adapter
        self._max = max_iterations
        self._lenses = lens_adapters or {}

    def run(
        self,
        gen_request: AgentRequest,
        val_request: AgentRequest,
    ) -> tuple[AgentResponse, ValidationResult, ConvergenceState]:
        """Run convergence loop.

        1. Generate artifact (invoke generate_adapter with gen_request)
        2. Validate (invoke validate_adapter with val_request, setting
           current_artifact to generated content)
        3. Parse validation result
        4. If PASS: return (response, result, state)
        5. If FAIL and iteration < max:
           a. Check for staleness (same gate failing with same description)
           b. Check for oscillation (gate A/B alternating)
           c. Build correction request with blocking_issues
           d. Re-generate with correction request
           e. Go to step 2
        6. If FAIL and iteration >= max: return with state indicating
           escalation needed

        CRITICAL: generate and validate are ALWAYS separate invoke() calls.
        The validate_adapter may be the same adapter instance as
        generate_adapter, but it receives a fresh request with no shared
        session state.
        """
        state = ConvergenceState(
            artifact_id=gen_request.metadata.get("artifact_id", "unknown"),
            artifact_type=gen_request.artifact_type,
            max_iterations=self._max,
        )

        current_gen_request = gen_request

        for iteration in range(self._max):
            state.current_iteration = iteration + 1

            # Step 1: Generate
            gen_response = self._gen.invoke(current_gen_request)

            # G-7: stop the moment the provider says it ran out of room. Do not
            # spend a validation call confirming that an artifact cut off
            # mid-sentence fails its gates, and do not regenerate it identically
            # for the rest of the budget.
            if gen_response.truncated:
                raise TruncatedGenerationError(
                    val_request.artifact_type,
                    state.current_iteration,
                    gen_response.tokens_out,
                )

            # Step 2: Validate (fresh request with generated content)
            validation_request = AgentRequest(
                artifact_type=val_request.artifact_type,
                event=val_request.event,
                spec_content=val_request.spec_content,
                template_content=val_request.template_content,
                prompt_content=val_request.prompt_content,
                upstream_artifacts=val_request.upstream_artifacts,
                current_artifact=gen_response.content,
                correction_constraints=val_request.correction_constraints,
                metadata=val_request.metadata,
                declared_inputs=val_request.declared_inputs,
            )
            val_response = self._val.invoke(validation_request)

            # Step 3: Parse validation result
            result = parse_validation_result(val_response)

            # Record in ledger
            ledger_entry = {
                "iteration": state.current_iteration,
                "status": result.status,
                "hard_gates": dict(result.hard_gates),
                "blocking_issues": list(result.blocking_issues),
                "completeness_score": result.completeness_score,
            }
            state.ledger.append(ledger_entry)

            # Step 4: If PASS, check lens reviews (if any)
            if result.status == "PASS":
                if self._lenses:
                    lens_issues = self._run_lens_reviews(
                        gen_response, gen_request, state
                    )
                    if lens_issues:
                        # Lens failures — feed back as correction constraints
                        logger.info(
                            "Lens review found %d issues for %s — re-generating",
                            len(lens_issues),
                            state.artifact_id,
                        )
                        if iteration < self._max - 1:
                            current_gen_request = self._build_correction_request(
                                gen_request, lens_issues
                            )
                            continue  # Re-enter loop
                        else:
                            # At max iterations — return with lens failures noted
                            result = ValidationResult(
                                status="FAIL",
                                summary=f"Lens review failures after {state.current_iteration} iterations",
                                hard_gates=result.hard_gates,
                                blocking_issues=lens_issues,
                                warnings=result.warnings,
                                completeness_score=result.completeness_score,
                            )
                            return gen_response, result, state
                return gen_response, result, state

            # Step 5: If FAIL and not at max, attempt correction
            if iteration < self._max - 1:
                # Check staleness and oscillation
                if self._detect_staleness(state):
                    logger.warning(
                        "Staleness detected at iteration %d for %s",
                        state.current_iteration,
                        state.artifact_id,
                    )
                if self._detect_oscillation(state):
                    logger.warning(
                        "Oscillation detected at iteration %d for %s",
                        state.current_iteration,
                        state.artifact_id,
                    )

                # Build correction request
                current_gen_request = self._build_correction_request(
                    gen_request, result.blocking_issues
                )

        # Step 6: Max iterations reached, escalation needed
        return gen_response, result, state

    def _run_lens_reviews(
        self,
        gen_response: AgentResponse,
        gen_request: AgentRequest,
        state: ConvergenceState,
    ) -> list[dict]:
        """Run all lens reviewers on the generated artifact.

        Each lens runs in a separate invoke() call with independent context
        (no lens sees another lens's output — Pattern 1 invariant).

        Returns a list of blocking issues from all failing lenses (empty if all pass).
        """
        all_issues: list[dict] = []

        for lens_name, lens_adapter in self._lenses.items():
            lens_request = AgentRequest(
                artifact_type=gen_request.artifact_type,
                event=LifecycleEvent.POST_VALIDATION,
                spec_content=gen_request.spec_content,
                template_content="",
                prompt_content=f"Review this artifact from a {lens_name} perspective.",
                upstream_artifacts=gen_request.upstream_artifacts,
                current_artifact=gen_response.content,
                correction_constraints=[],
                metadata={
                    **gen_request.metadata,
                    "lens": lens_name,
                },
                declared_inputs=gen_request.declared_inputs,
            )

            try:
                lens_response = lens_adapter.invoke(lens_request)
                lens_result = parse_validation_result(lens_response)

                state.ledger.append({
                    "iteration": state.current_iteration,
                    "lens": lens_name,
                    "status": lens_result.status,
                    "blocking_issues": list(lens_result.blocking_issues),
                })

                if lens_result.status == "FAIL":
                    for issue in lens_result.blocking_issues:
                        all_issues.append({
                            "gate": f"lens:{lens_name}:{issue.get('gate', 'unknown')}",
                            "description": issue.get("description", ""),
                            "location": issue.get("location", ""),
                        })
            except Exception as exc:
                logger.warning("Lens %s failed with error: %s", lens_name, exc)
                # Lens errors don't block — log and continue

        return all_issues

    def _detect_staleness(self, state: ConvergenceState) -> bool:
        """Same gate failing with same description in last 2 ledger entries."""
        if len(state.ledger) < 2:
            return False

        prev = state.ledger[-2]
        curr = state.ledger[-1]

        # Find gates that failed in both
        prev_failed = {
            k: v for k, v in prev.get("hard_gates", {}).items() if v == "FAIL"
        }
        curr_failed = {
            k: v for k, v in curr.get("hard_gates", {}).items() if v == "FAIL"
        }

        common_failed = set(prev_failed.keys()) & set(curr_failed.keys())
        if not common_failed:
            return False

        # Check if blocking issues have the same description for common gates
        prev_issues = {
            issue.get("gate"): issue.get("description")
            for issue in prev.get("blocking_issues", [])
        }
        curr_issues = {
            issue.get("gate"): issue.get("description")
            for issue in curr.get("blocking_issues", [])
        }

        for gate in common_failed:
            if gate in prev_issues and gate in curr_issues:
                if prev_issues[gate] == curr_issues[gate]:
                    return True

        return False

    def _detect_oscillation(self, state: ConvergenceState) -> bool:
        """Gate A fails in iteration N, passes in N+1 but gate B fails,
        then gate A fails again in N+2.
        """
        if len(state.ledger) < 3:
            return False

        entries = state.ledger[-3:]
        failed_sets = []
        for entry in entries:
            failed = {
                k for k, v in entry.get("hard_gates", {}).items() if v == "FAIL"
            }
            failed_sets.append(failed)

        # Oscillation: a gate that failed in N, passed in N+1, failed in N+2
        for gate in failed_sets[0]:
            if gate not in failed_sets[1] and gate in failed_sets[2]:
                return True

        return False

    def _build_correction_request(
        self,
        original: AgentRequest,
        blocking_issues: list[dict],
    ) -> AgentRequest:
        """Clone original request, add blocking issues as correction_constraints."""
        constraints = list(original.correction_constraints)
        for issue in blocking_issues:
            desc = issue.get("description", "")
            gate = issue.get("gate", "")
            location = issue.get("location", "")
            constraint = f"[{gate}] {desc}"
            if location:
                constraint += f" (at: {location})"
            constraints.append(constraint)

        return AgentRequest(
            artifact_type=original.artifact_type,
            event=original.event,
            spec_content=original.spec_content,
            template_content=original.template_content,
            prompt_content=original.prompt_content,
            upstream_artifacts=original.upstream_artifacts,
            current_artifact=original.current_artifact,
            correction_constraints=constraints,
            metadata=original.metadata,
            declared_inputs=original.declared_inputs,
        )
