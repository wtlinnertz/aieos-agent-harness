"""Focused check tier for validator gates (FR-014 build spec sections 4h and 4i).

R1, non-goal coverage. sad-spec.md, Scope Discipline: "Non-goals from PRD
must be explicitly restated." The gold PASS case paraphrases every PRD
exclusion, so text matching cannot decide it (decision 2026-11-15, 4h Q1).
R1 asks the judge one narrow question per PRD exclusion: quote the SAD
Explicit Non-Goals line that restates it, or answer NONE. Code combines the
answers; any NONE fails ``scope_discipline``.

Planning and scoring are pure functions; callers make the provider calls,
so calibration can count them and refuse on a provider error (DEF-003)
exactly as it does for the main judge call. Findings reuse the referential
tier's combining rule: a FAIL overrides a judge PASS, never the reverse.

Registered for SAD only, like the referential tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.models import AgentRequest, AgentResponse, LifecycleEvent
from src.referential_checks import ReferentialFinding

TIER = "focused"
CHECK_ID = "R1-non-goal"
_GATE = "scope_discipline"
_LOCATION = "Section 2 / Explicit Non-Goals"

_PRD_HEADING = re.compile(r"^(#{1,6})\s*(?:\d+(?:\.\d+)*\.?\s*)?non[- ]goals?\b", re.IGNORECASE)
_SAD_HEADING = re.compile(
    r"^(#{1,6})\s*(?:\d+(?:\.\d+)*\.?\s*)?explicit non[- ]goals?\b", re.IGNORECASE
)
_ANY_HEADING = re.compile(r"^(#{1,6})\s")
# Top-level bullets only; nested sub-bullets explain a parent, they are not
# exclusions of their own.
_BULLET = re.compile(r"^ {0,1}[-*+]\s+(.*\S)")

_SYSTEM = (
    "You check one rule of a Software Architecture Document (SAD) against its "
    "PRD. Answer with a single line and nothing else."
)
_QUESTION = """\
Rule: every PRD non-goal must be explicitly restated in the SAD's Explicit Non-Goals.

PRD exclusion ({prd_id}): {exclusion}

The Current Artifact below is the SAD's Explicit Non-Goals list. If one line \
restates this exclusion, reply with that line copied exactly. A paraphrase \
counts only if it excludes the same thing; a narrower or merely related line \
does not. If no line restates it, reply with exactly NONE."""


def _section_bullets(text: str, heading: re.Pattern[str]) -> list[str] | None:
    """Bullets under the first heading matching ``heading``; None if absent."""
    level: int | None = None
    bullets: list[str] = []
    for line in text.splitlines():
        found = _ANY_HEADING.match(line)
        if found:
            if level is not None and len(found.group(1)) <= level:
                break
            match = heading.match(line.strip())
            if match and level is None:
                level = len(match.group(1))
            continue
        if level is not None:
            bullet = _BULLET.match(line)
            if bullet:
                bullets.append(bullet.group(1))
    return bullets if level is not None else None


def prd_non_goals(prd: str) -> list[str] | None:
    """Exclusions under the PRD's Non-Goals heading; None if it has none."""
    return _section_bullets(prd, _PRD_HEADING)


def sad_non_goals(sad: str) -> list[str]:
    """Lines under the SAD's Explicit Non-Goals heading; empty if absent."""
    return _section_bullets(sad, _SAD_HEADING) or []


@dataclass(frozen=True)
class NonGoalQuery:
    prd_id: str
    exclusion: str
    request: AgentRequest


@dataclass(frozen=True)
class NonGoalPlan:
    sad_lines: tuple[str, ...]
    queries: tuple[NonGoalQuery, ...]
    # Decided without a provider call (the SAD has no Explicit Non-Goals).
    findings: tuple[ReferentialFinding, ...]
    # Why R1 did not run, recorded with the verdict; None when it ran.
    skipped: str | None


def _finding(prd_id: str, exclusion: str, why: str) -> ReferentialFinding:
    return ReferentialFinding(
        gate=_GATE,
        check=CHECK_ID,
        description=f"PRD exclusion '{exclusion}' ({prd_id}) {why}",
        location=_LOCATION,
        tier=TIER,
    )


def plan_non_goal_checks(
    artifact_type: str,
    artifact: str,
    upstream_artifacts: dict[str, str],
    metadata: dict[str, str],
) -> NonGoalPlan:
    """Build one temperature-0 judge request per PRD exclusion."""
    if artifact_type.upper() != "SAD":
        return NonGoalPlan((), (), (), f"R1 not registered for {artifact_type}")
    exclusions: list[tuple[str, str]] = []
    prd_ids = sorted(k for k in upstream_artifacts if k.upper().startswith("PRD-"))
    for prd_id in prd_ids:
        for exclusion in prd_non_goals(upstream_artifacts[prd_id]) or []:
            exclusions.append((prd_id, exclusion))
    if not prd_ids:
        return NonGoalPlan((), (), (), "no PRD among upstream artifacts")
    if not exclusions:
        return NonGoalPlan((), (), (), "PRD has no non-goals section")

    sad_lines = tuple(sad_non_goals(artifact))
    if not sad_lines:
        findings = tuple(
            _finding(p, e, "is not restated: the SAD has no Explicit Non-Goals list.")
            for p, e in exclusions
        )
        return NonGoalPlan((), (), findings, None)

    listing = "\n".join(f"- {line}" for line in sad_lines)
    queries = tuple(
        NonGoalQuery(
            prd_id=prd_id,
            exclusion=exclusion,
            request=AgentRequest(
                artifact_type=artifact_type,
                event=LifecycleEvent.PRE_VALIDATION,
                spec_content=_SYSTEM,
                template_content="",
                prompt_content=_QUESTION.format(prd_id=prd_id, exclusion=exclusion),
                upstream_artifacts={},
                current_artifact=listing,
                correction_constraints=[],
                metadata={**metadata, "check": CHECK_ID, "prd_id": prd_id},
                temperature=0.0,
            ),
        )
        for prd_id, exclusion in exclusions
    )
    return NonGoalPlan(sad_lines, queries, (), None)


def _normalize(text: str) -> str:
    text = re.sub(r"^\s*[-*+]\s+", "", text.strip().strip("\"'`"))
    text = re.sub(r"[*_`]", "", text).lower()
    return re.sub(r"\s+", " ", text).strip(" .\"'")


def _quotes_a_line(answer: str, sad_lines: tuple[str, ...]) -> bool:
    said = _normalize(answer)
    if not said:
        return False
    return any(
        line and (line in said or said in line) for line in map(_normalize, sad_lines)
    )


def non_goal_findings(plan: NonGoalPlan, answers: list[str]) -> list[ReferentialFinding]:
    """Score the judge's answers, one per query, in plan order.

    An answer that is neither NONE nor a quote of a real SAD line fails too:
    an invented quote must not pass the gate.
    """
    if len(answers) != len(plan.queries):
        raise ValueError(f"expected {len(plan.queries)} R1 answers, got {len(answers)}")
    findings = list(plan.findings)
    for query, answer in zip(plan.queries, answers):
        if re.match(r"^\W*none\b", answer.strip(), re.IGNORECASE):
            findings.append(
                _finding(query.prd_id, query.exclusion, "is not restated in SAD Explicit Non-Goals.")
            )
        elif not _quotes_a_line(answer, plan.sad_lines):
            findings.append(
                _finding(
                    query.prd_id,
                    query.exclusion,
                    f"could not be confirmed: the judge did not quote a SAD "
                    f"Explicit Non-Goals line (answered '{answer.strip()[:80]}').",
                )
            )
    return findings


def run_non_goal_checks(
    plan: NonGoalPlan, adapter
) -> tuple[list[ReferentialFinding], list[AgentResponse]]:
    """Make the plan's calls on ``adapter`` and score them. Errors propagate."""
    responses = [adapter.invoke(q.request) for q in plan.queries]
    return non_goal_findings(plan, [r.content for r in responses]), responses
