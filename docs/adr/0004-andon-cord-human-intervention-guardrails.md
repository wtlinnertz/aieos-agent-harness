# ADR-0004: Andon cord — human-intervention guardrails for the dark factory

**Status:** Accepted (2026-07-10)
**Date:** 2026-07-10
**Deciders:** AIEOS framework owner

## Context

ADR-0002 introduces `aieos-dark-factory`, a control-plane loop that walks `kit-manifest.yml` and runs each layer's artifacts to `VALIDATED` through the existing harness lifecycle, halting only at human freeze gates. The freeze gate is a *planned* stop: every artifact reaches it, and it presents good work for approval.

A dark factory also needs an *unplanned* stop. When something goes wrong mid-run — a governance invariant is breached, a provider dies, the budget runs away, or the conductor silently loops — an unattended run has no human watching to catch it. This ADR specifies a virtual **andon cord**: a mechanism that halts the run and summons a human when a fault is detected. The name is the manufacturing term for the cord any worker can pull to stop the line on a defect.

The andon cord is the architectural inverse of the freeze gate. Both hand control to a human, but they are different stops and must not share a code path or a human-facing payload:

| | Freeze gate | Andon cord |
|---|---|---|
| Trigger | Planned, every artifact | Unplanned, on fault |
| Presents | Good artifact + validation report | A fault + diagnostic context |
| Human action | Approve / block the promotion | Diagnose, remediate, then resume |
| Writes | `FROZEN` (via `apply_freeze_decision`) | `HALTED` / `FAULTED` (never `FROZEN`) |

### What the harness already emits

Most of the trip *detectors* exist. This ADR wires them to a halt path; it does not invent new detection.

- `ConvergenceLoop` already detects three distinct failure shapes: max-iterations exhaustion (`convergence.py`, returns escalation-needed state), `_detect_staleness` (same gate failing with the same description), and `_detect_oscillation` (gate A/B alternating).
- `CircuitBreaker` (`routing.py`) exposes `is_open(provider)` after `max_failures` (default 3) within `reset_seconds` (default 60).
- `invariants.py` has seven `check_*` functions, each returning an `InvariantCheck` with a boolean `passed`.
- `LifecycleEvent.ON_FAILURE` is a defined hook.
- `ObservabilityLayer` (`observability.py`) meters `cost_usd` per invocation and aggregates it in `cost_summary`.

What is missing is everything *after* a trip: a fault status distinct from freeze, a channel that reaches an absent human, and a policy for resuming a run that was interrupted mid-convergence.

## Decision

Add an andon cord to the dark factory with the following design. Each numbered item records a decision already taken (2026-07-10); the trailing tag maps it to the elicitation question it answers.

### 1. Tiered triggers *(A1)*

Two severity classes, because the correct response differs:

- **Hard trip → halt and summon.** Any invariant `passed=False`; a budget-ceiling breach; a circuit-breaker open. A hard trip means the run cannot safely continue — governance is compromised, spend is out of control, or infrastructure is down.
- **Soft trip → park and hand off.** Convergence exhaustion (the existing escalation-needed state) and early staleness/oscillation. A soft trip means *this artifact* could not converge; it is the expected escalation, not an alarm.

Staleness and oscillation pull at the detector's existing window — the thresholds encoded in `_detect_staleness` and `_detect_oscillation` are not second-guessed *(A8)*.

### 2. Blast radius: halt the whole initiative *(A2)*

When the cord pulls, the conductor stops walking the DAG and the entire initiative parks. One conductor runs one initiative, and a faulted upstream artifact poisons everything downstream, so continuing sibling work is waste at best and corruption at worst. Per-node halt is not a v1 concern.

### 3. Two new artifact statuses *(A3)*

Extend `ArtifactStatus` beyond `DRAFT | VALIDATED | FREEZE_PENDING | FROZEN`:

- `HALTED` — a clean stop, resumable once the triggering condition is cleared (provider outage, budget raised, transient fault).
- `FAULTED` — a governance breach (invariant violation). Not resumable without human investigation first; a human must inspect and explicitly clear it before any resume.

These are added to the FR-018 canonical Document Control block in `aieos-schema` in the same pass that defines the block, so every driver renders them from day one. This is a change to the FR-018 vocabulary, not a bolt-on.

### 4. One halt path, two triggers, backed by a sentinel *(A4)*

The internal trip (conductor detects a fault) and the external stand-down (a human halts a running conductor) are the same mechanism. Both write and both check a sentinel file, `.aieos/halt`, which the conductor reads before starting each artifact. On an internal trip the conductor writes the sentinel and stops; a human can write it to stand the run down from outside. A file is language-neutral (Python conductor, TypeScript console, prompt-based sherpa can all read it), composes with the FR-019 lock in the same `.aieos/` sidecar, and survives a crash where an OS signal handler would not.

### 5. Summon channel: email in v1 *(A5)*

A pull writes status and log, and actively notifies via the existing Gmail integration. Lights-out means nobody is watching, so a pull that only writes a status cell is a cord no one hears. Email is the only channel wired and authorized today. A PagerDuty or webhook adapter — the more honest fit for "the autonomous line stopped" and the path to the EU-AI-Act "rapid revocation" posture — is a v1.1 upgrade, deferred because the connector needs OAuth not yet completed.

### 6. Resume requires a positive human signal *(A6)*

A cleared andon does not auto-resume. The Toyota line does not restart itself; a human restarts it after fixing the defect. Requiring an explicit resume signal also stops a flapping condition from silently restarting an unattended run.

**A resume/clear is not a freeze.** The human's clearance routes through its own record — a sibling entry in the append-only Decision Register (roadmap FR-007), never through `apply_freeze_decision`. `apply_freeze_decision` remains the single authority that writes `FROZEN`. The andon must never become a backdoor to promotion.

### 7. Silent-failure backstops *(A7)*

The triggers above catch *loud* failures. Two cheap backstops catch the conductor that fails quietly, both riding data `ObservabilityLayer` already collects:

- **Liveness heartbeat** — no artifact status change within N minutes pulls the cord. Catches a conductor wedged or looping without progressing.
- **Cost anomaly** — per-artifact spend exceeding K× the rolling baseline pulls the cord. Catches a generation loop burning tokens.

These are complementary to Judge Calibration Governance (FR-014), which catches the *other* silent failure: a drifting lenient validator that passes garbage. Heartbeat and cost watch the conductor; calibration watches the judge. Both are needed for full silent-failure coverage; only the first two are in this ADR's scope.

### 8. Recovery: rewind to the last frozen boundary *(A9)*

On resume after a halt, the in-flight artifact is discarded and regenerated from clean upstream state at the last frozen boundary. This keeps the existing invariant — only frozen artifact boundaries are safe switch points (ADR-0002, FR-019) — completely intact. No mid-convergence checkpoint format has to be designed, tested, or trusted. The cost is redoing at most one artifact's convergence, bounded at `max_iterations` (default 3).

Recovery eligibility is keyed to **Reversibility Classification** (roadmap idea): an artifact whose generation triggered a `compensable` or `irreversible` action cannot simply be discarded and rewound. For v1, the dark factory only sequences generation and validation of governance artifacts (reversible by construction), so rewind is always safe; the eligibility check is the extension point for when the conductor drives reversibility-classified actions.

### 9. Dark-factory conductor semantics

Two conductor-level decisions taken alongside the andon design:

- **A human `REMEDIATE_AND_RETRY` at a gate resets the convergence budget** *(exchange-3 Q1)*. A human correction is new information the loop has not seen, so the artifact gets a fresh `max_iterations`, rather than consuming one of them.
- **A soft trip hands the human the full convergence trail** *(exchange-3 Q2)*: every iteration's generation plus validator findings plus the staleness/oscillation flag, not just the final failed artifact. The human is diagnosing *why it could not converge*, and the cross-iteration pattern is the diagnosis. This reuses the run-record shape from the Interpretation-Before-Action idea (what was asked, what it did, what it touched, what it was uncertain about) as the fault-context payload.

## Relationship to existing roadmap items

The andon cord is not a new idea. It is the concrete, dark-factory instantiation of controls already logged in [[AIEOS Ideas MOC]]:

- **Agent Control Map** is the parent. Its control rows already include "who can stop it — with the kill switch required at multiple layers (runtime cancel … workflow interrupt)," and its documented enforcement defaults (`max_tool_calls` budget, token-cost circuit breaker, progress-based termination, spend caps with circuit breakers) are verbatim the hard-trip triggers in decision 1 and the heartbeat backstop in decision 7. This ADR is where that idea graduates from the backlog into code for one driver. Its EU AI Act Art. 9/13 anchor ("rapid revocation within seconds") is the latency requirement that makes the email channel (decision 5) a v1 stopgap rather than the end state.
- **Reversibility Classification** governs recovery eligibility (decision 8).
- **Agent Harness Log Forwarder** is the substrate for the backstops (decision 7): the heartbeat is "no state-transition span," and its published alert thresholds (task-success < 80%, tool-error > 5%) are reusable trip conditions. The andon should emit to and read from that run-log schema rather than defining its own.
- **FR-007 (append-only Decision Register)** is where the resume/clear record lives (decision 6).
- **Interpretation-Before-Action** supplies the fault-context payload shape (decision 9).

## Options Considered

### Trigger model

| Option | Assessment |
|--------|------------|
| Minimal — only invariant + budget trips | Ignores detectors already built; lets a wedged conductor run |
| **Tiered (chosen)** | Uses existing detectors; separates "work won't converge" from "governance broke" |
| Aggressive — any FAIL halts | Destroys lights-out; every routine retry becomes a human interrupt |

### Recovery model

| Option | Assessment |
|--------|------------|
| Persist convergence state, resume mid-artifact | Most capable; reopens a policy (mid-convergence is unsafe) deliberately closed in ADR-0002; needs a new checkpoint format |
| **Rewind to last frozen boundary (chosen)** | Preserves the safe-switch-point invariant; cost is ≤ one artifact's convergence |
| Halt initiative, no automated resume | Simplest; discards the conductor's crash-resumption value |

## Placement (decided 2026-07-10)

The andon protocol is a reliability-resilience concern (Layer 6, RRK), but its enforcement lives in `aieos-agent-harness` and `aieos-dark-factory`. **The two are split along the framework's own spec-versus-execution seam:**

- **RRK owns the andon *pattern* spec** as governed content: the trigger taxonomy (hard/soft tiers), the severity model, the safe-park-and-rewind-to-frozen-boundary recovery policy, and the summon-and-resume contract. This makes the andon a reusable AIEOS practice, consistent with the Principles → Patterns → Practices model, so any future driver inherits the pattern rather than reinventing it.
- **The harness and the dark factory own the *enforcement code*** — the trip detectors, the `.aieos/halt` sentinel, `apply_freeze_decision`'s companion invariants, the summon channel, and the conductor's halt/resume loop.

The **dark factory is the first (and, in v1, only) consumer** of the RRK pattern — sherpa and console are human-present, so a human is already their andon. Other drivers adopt the RRK pattern if and when they gain an unattended mode.

This mirrors how AIEOS already separates governance (specs, validators) from execution (the harness), and it is the direct application of "guarantee with hooks, guide with prompts": RRK states the guarantee, the harness enforces it. It also keeps the pattern from over-fitting to the dark factory's implementation, since the spec is authored one level up in RRK.

## Consequences

Positive:

- The dark factory can run unattended between freeze gates with a defined, human-summoning stop for faults — the honest version of "lights-out."
- The freeze invariant is never weakened: the andon writes `HALTED`/`FAULTED`, never `FROZEN`, and resume routes around `apply_freeze_decision`.
- The Agent Control Map graduates from an idea to shipped enforcement for one driver, and the FR-019 lock-takeover path reuses the same `.aieos/halt` sentinel (decision 4) rather than inventing a second stop mechanism.
- The backstops (decision 7) are near-free, riding existing observability data.

Costs and follow-ups:

- Three genuinely new pieces of work: the `HALTED`/`FAULTED` statuses (in the FR-018 schema pass), the summon channel, and the sentinel halt path.
- The email channel is a v1 stopgap; the seconds-latency revocation posture the EU AI Act anchor implies needs the PagerDuty/webhook adapter.
- A false-positive andon degrades lights-out back toward attended operation; the heartbeat interval and cost-anomaly multiplier need tuning against real runs.
- The RRK pattern spec is a new authoring deliverable (governed content), separate from the enforcement code — tracked in Track D of the release plan.

## Related

- ADR-0002 (dark factory as a separate control-plane repo). The driver this ADR guards.
- ADR-0003 (console-to-harness freeze boundary — the TS→Python `apply_freeze_decision` call path). The andon's summon-and-resume UI is rendered by the console, so the same boundary applies.
- Roadmap: Agent Control Map, Reversibility Classification, Agent Harness Log Forwarder, FR-007 (Decision Register), FR-014 (Judge Calibration), FR-018/FR-019.
- `AIEOS v1.3 - Three Drivers Release Plan` (vault) — Track D and the FR-019/FR-020 decisions section.
