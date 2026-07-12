# ADR-0002: Autonomous pipeline conductor as a separate control-plane repo (aieos-dark-factory)

**Status:** Accepted (2026-07-10)
**Date:** 2026-07-06
**Deciders:** AIEOS framework owner

## Context

AIEOS today is driven by two human-in-the-loop surfaces: `aieos-console` (a browser wizard) and `aieos-sherpa` (an LLM-agnostic prompt guide). Both walk the same pipeline one artifact at a time, with a human present at every step. There is no way to run the pipeline unattended.

The goal of this decision is a third driver: run generation and validation lights-out across the layer model, with a human present only at freeze gates. "Dark factory" is the manufacturing term for a plant that runs without people on the floor. The name is not new to this repo. `AgentSpecies.DARK_FACTORY` is already the default species in `src/config.py` and `src/lifecycle.py`, and `src/observability.py` already meters cost and latency by species. The harness was built assuming lights-out operation as its default mode. What it never had is the driver that runs the pipeline that way.

Most of the machinery already exists. `ConvergenceLoop` runs a single artifact through generate, validate, and correct, bounded at three iterations before escalation. `WDDOrchestrator` sequences work items within Layer 4 with dependency and file-overlap checks. Routing, provider adapters, circuit breakers, and per-invocation observability are all in place. `kit-manifest.yml` in `aieos-governance-foundation` is the machine-readable DAG of all fifteen kits, their dependency edges, and their trigger conditions.

What is missing is a cross-layer conductor: a component that walks `kit-manifest.yml`, runs each layer's artifacts to `VALIDATED` through the existing lifecycle, and then halts at the freeze gate. The CLI exposes only per-artifact commands (`generate`, `validate`, `lifecycle`), and `cmd_lifecycle` stops with the message "harness does not auto-freeze." Nothing drives the pipeline layer to layer.

Two findings from tracing the harness shape this decision.

**The freeze primitives exist but are unwired.** `ArtifactStatus.FREEZE_PENDING` and the full `DecisionOutcome` enum are defined in `src/models.py` and referenced only by tests. `check_human_freeze_decision(auto_freeze_attempted)` in `src/invariants.py` has zero production callers and three test assertions. `state.py` has a reader for artifact status (`read_frozen_artifacts`) but no writer, and `write_er_state_block` and `append_journal_entry` have no callers in `src/`. The pattern is consistent: the vocabulary for a human-gated autonomous run was anticipated and left in place, and the driver that would use it was never built.

**The two existing drivers have already diverged on the freeze representation.** Verified this session (see Relationship to console and sherpa, below): `aieos-console` records freeze in `.aieos/state.json` and as a horizontal registry row appended to `docs/engagement/er.md`, while the harness reads freeze from a vertical `| Status |` cell inside each `docs/sdlc/*.md` Document Control block, which the console never writes. A console-frozen artifact is invisible to `read_frozen_artifacts`. This is the concrete form of the risk that multiple freeze implementations drift apart.

## Decision

Build the conductor as a separate repository, `aieos-dark-factory`, driven by its value as a standalone, demonstrable capability ("AIEOS runs its own governance pipeline").

Draw the repository boundary at **governance versus orchestration**, not at "conductor code versus harness code":

1. `aieos-agent-harness` keeps all governance and execution concerns: generation, validation, convergence, `invariants.py`, the freeze writer, and the state primitives. It publishes one small, stable facade, `HarnessDriver`, with three operations: `run_artifact_lifecycle() -> CONVERGED | ESCALATION_NEEDED`, `read_layer_state()`, and `apply_freeze_decision(FreezeGateDecision)`.
2. `aieos-dark-factory` is pure control plane: the DAG walk over `kit-manifest.yml`, the conductor state machine, the freeze-gate broker to a human, the budget ceiling and kill-switch, the Decision Register, and crash resumption. It imports only the facade.

`apply_freeze_decision` is the single authority that writes `FROZEN`. It verifies that the decision's content hash matches the artifact on disk and that a `decided_by` identity is present, then writes the Document Control `Status` cell, fires `POST_FREEZE`, appends the Journal, and increments the frozen count. The conductor has no code path that writes `FROZEN`. It can drive an artifact to `FREEZE_PENDING` and no further.

The freeze invariant change is additive. Add a companion check, `check_authorized_freeze(decision)`, alongside the existing `check_human_freeze_decision`. The existing rule stays true: an auto-freeze with no human decision still fails. A conductor freeze passes because it carries a valid decision record and reports `auto_freeze_attempted=False`.

## Options Considered

### Option A: Conductor as a subpackage inside the harness

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low. One repo, one CI job, refactor internals freely |
| Cost | Low. No new repo, release, or versioning overhead |
| Narrative | Weak. A subpackage does not read as a standalone capability |
| API stability required | None. Conductor uses harness internals directly |

Engineering-optimal while the design is still moving. Defers the repo tax until a second consumer or a stable interface exists.

### Option B: Separate repo `aieos-dark-factory` (chosen)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium. New repo, CI, release, a published facade |
| Cost | Higher up front, justified by the narrative goal |
| Narrative | Strong. A named repo that runs the pipeline is a demonstrable artifact |
| API stability required | One small facade, held stable; internals stay free to churn |

Chosen because the driver for this work is narrative, not engineering economics. The risk of a separate repo (a distributed monolith where the conductor reaches into harness internals across a boundary) is contained by the facade and by keeping the freeze invariant inside the harness.

### Option C: Fold the conductor into aieos-console

Rejected. It inverts the dependency (the console is a consumer of governance, not the owner of it) and straddles a TypeScript and Python boundary for logic that belongs next to the Python lifecycle.

## Trade-off Analysis

The decisive factor is the driver. On engineering economics alone, Option A wins: the conductor reuses `ConvergenceLoop`, `LifecycleBinder`, `state.py`, and `ExecutionLedger`, all internal modules with no stability contract, and a separate repo would turn each into a published interface overnight. The narrative goal changes the calculus. A repo named `aieos-dark-factory` that runs the fifteen-layer pipeline is a stronger demonstration than a subpackage, and the org slot is free.

The two costs of a separate repo are both contained by the boundary. The published-API cost shrinks to one small facade rather than the whole surface of the harness internals. The invariant-straddle cost disappears because the freeze invariant and the only `FROZEN`-writer stay inside the harness; the conductor never edits governance across the boundary. The boundary maps onto the separation-of-concerns invariant: the dark factory sequences, the harness governs.

## Step 0: Blast-radius verification (done, 2026-07-06)

Before any harness change lands, confirmed that no AIEOS repo imports the modules this work touches. A grep across the whole `aieos/` org checkout found:

- No repo imports the harness's `invariants` or `state` modules, and nothing outside the harness references their functions. Matches were documentation prose and two false positives on the phrase "import statement."
- The only cross-repo coupling is `aieos-pipeline-runner`, which interoperates with the harness at runtime through the capability registry interface (its own source comments note it mirrors the harness's artifact-store shape). It does not import `invariants` or `state`, so additive changes do not affect it.

Every harness-side change in this decision is a new function (`write_artifact_status`, `apply_freeze_decision`, `HarnessDriver`, `check_authorized_freeze`) with no existing callers. The one existing function in scope, `check_human_freeze_decision`, gains a companion rather than a signature change. Worst-case surface is roughly three test lines, no pipeline code. The change is additive on the code and reinforcing on the governance model.

## Relationship to console and sherpa

All three drivers sit over one engine: the same specs, templates, prompts, and validators, the same freeze-before-promote principle, and the same on-disk substrate (the Engagement Record state block, the artifact Document Control status, the Sherpa Journal). They differ on autonomy and interface.

| | Sherpa | Console | Dark Factory |
|---|---|---|---|
| Form | Prompt (Markdown) | Next.js app (TypeScript) | Control-plane loop (Python) |
| Driver | Any LLM, human-led chat | Human clicking a wizard | Autonomous code walking the DAG |
| Freeze | Human decides in chat | Human clicks Freeze | Human submits a decision record |
| Sequencing source | navigation-map | kit YAML flow defs | `kit-manifest.yml` |
| Autonomy | Human every step | Human every click | Autonomous except freeze gates |

**Parity finding (verified).** The console and harness do not share a freeze representation today. The console writes `status: 'frozen'` (lowercase) to `.aieos/state.json` and appends `| artifactId | type | frozen | notes |` to `docs/engagement/er.md`. The harness reads a vertical `| Status | FROZEN |` cell inside each `docs/sdlc/*.md` Document Control block, which the console never writes. The status vocabularies also differ: the console uses `frozen`, `validated-pass`, `validated-fail`, `draft`, `not-started`; the harness uses `FROZEN`, `VALIDATED`, `FREEZE_PENDING`, `DRAFT`. An initiative frozen in the console cannot be handed to the harness or the dark factory without a format bridge, because the harness will not see the console's freezes.

**Direction: one writer, several front-ends.** The dark factory must not add a fourth representation. It reads and writes the harness's Document Control status through `state.py` and the new `write_artifact_status`. Longer term, `apply_freeze_decision` becomes the single freeze authority, and the console (and eventually sherpa) call it rather than each writing status in its own shape. Consolidating the console's TypeScript freeze onto the Python writer is cross-language and non-trivial, so the near-term target is a shared contract (one status format, one dependency-map source, all pointed at `kit-manifest.yml`), with "one writer" as the architectural direction rather than a v1 requirement.

**Canonical representation (decided 2026-07-06, tracked as FR-018).** The single representation is the artifact's Document Control block, defined once in `aieos-schema` and enforced by a conformance validator. Freeze status is a governed fact about an artifact, so it belongs inside the governed, self-describing artifact rather than an ungoverned sidecar; a `.aieos/state.json` as system-of-record would be incoherent with the rest of the framework. The console's JSON store demotes to a derived cache (fast UI lookup, delivering FR-008) rebuilt by scanning the blocks. Freeze status and validation outcome become two orthogonal fields: the harness lifecycle (`DRAFT`, `VALIDATED`, `FREEZE_PENDING`, `FROZEN`) for freeze, and a separate `last_validation: PASS | FAIL` for the console's finer distinction. The harness change is small; the console never shipped (noted 2026-07-06), so it is built to the canonical block directly rather than migrated, and there is no legacy format to reconcile. A redesigned console could go further and skip its own JSON store, reading the canonical state directly like sherpa and the harness. That rebuild is tracked as FR-020.

**Cooperation.** The shared Engagement Record state block and Sherpa Journal are already designed for cross-session and cross-AI handoff (see the sherpa `cross-ai-handoff.md`). Once the representations are reconciled, a human can let the dark factory run several layers unattended, park at a freeze gate, and pick the initiative up in the console to review and approve or in sherpa conversationally. The `FreezeGateRequest` payload (artifact, validation report, decision) is what the console's freeze UI already renders, so the console can serve as the human approval surface for the dark factory's gates. When the conductor hits `ESCALATION_NEEDED` (convergence exhausted), the console and sherpa are the natural human escalation surfaces.

## Consequences

Positive:

- The harness-side changes land first, additively, on a verified-safe base (Step 0). Nothing that exists breaks.
- The dark factory enforces the human-freeze invariant in code for the first time, instead of relying on a human not writing `FROZEN` by hand.
- Building the freeze gate realizes the append-only Decision Register (roadmap FR-007) as a byproduct, hash-chained through each artifact's upstream frozen references.
- The extraction seam (the facade) is designed from day one, so the boundary stays clean.

Costs and follow-ups:

- A forty-second repo adds CI, release, and versioning overhead, taken on deliberately for the narrative goal.
- The console-to-harness freeze format divergence is now an explicit, documented gap, tracked as FR-018 (shared format) and FR-019 (cross-driver write-integrity lock). Since the console never shipped, it is redesigned to the canonical format rather than migrated, which removes the migration risk entirely.
- The dark factory runs unattended, so concurrent writes to the same initiative files (dark factory plus a human in the console) are a new failure mode that the human-driven tools never had. Cross-driver write safety is tracked as FR-019 (a shared ownership lock that all three drivers honor, plus a frozen-boundary switch-point policy), and the budget ceiling and kill-switch (roadmap: Agent Control Map) are dependencies for the unattended variant, not the attended one.

  **Amendment (2026-07-10):** FR-019 is a *rewrite* of the console's `.aieos/lock`, not an extension of it. The existing primitive (`filesystem-service.ts::acquireLock`) records `hostname` in the lock file but its liveness check (`isPidAlive` → `process.kill(pid, 0)`) never reads it, so a lock written on one host is checked against whatever process happens to hold that PID number on another — a false-alive that wedges the initiative, or a false-stale that silently steals a lock another host still owns. It also has no lease or heartbeat, so a crashed unattended run leaves a lock that never expires. FR-019 therefore must specify a hostname-aware, lease-based format implemented in both Python (harness) and TypeScript (console), with sherpa honoring it advisory-only. The stale-lock takeover reuses ADR-0004's `.aieos/halt` sentinel so a possibly-live prior owner stands down before a human resumes. See the v1.3 release plan, Phase 3.
- `ROLLBACK` from `DecisionOutcome` is intentionally out of scope for the first gate; unfreezing a promoted artifact contradicts freeze immutability and needs its own design.

## Related

- ADR-0001 (CI/CD substrate co-located in the agent harness). The prior placement decision this one mirrors in reasoning.
- `kit-manifest.yml` in `aieos-governance-foundation`. The DAG the conductor walks.
- Roadmap FR-007 (append-only Decision Register), Agent Control Map, Reversibility Classification.
