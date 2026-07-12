# ADR-0003: Console-to-harness freeze boundary — CLI subprocess

**Status:** Accepted (2026-07-10)
**Date:** 2026-07-10
**Deciders:** AIEOS framework owner

## Context

ADR-0002 established `apply_freeze_decision` in `aieos-agent-harness` as the single authority that writes `FROZEN`, and FR-020 rebuilds `aieos-console` as a thin front-end that calls that authority rather than writing freeze status in its own shape. This closes the freeze-format divergence (FR-018): the console stops owning `.aieos/state.json` as a source of truth and instead reads and writes the canonical Document Control block, freezing through the harness.

That creates a language boundary. `apply_freeze_decision` is Python; the console is TypeScript (Next.js 15). ADR-0002 deliberately deferred this: "Consolidating the console's TypeScript freeze onto the Python writer is cross-language and non-trivial, so the near-term target is a shared contract... with 'one writer' as the architectural direction rather than a v1 requirement." FR-020 promotes it to a v1 requirement — a console that *calls* `apply_freeze_decision` must cross TS → Python now, not later. This ADR decides how.

Left implicit, the boundary gets decided by whoever writes the console's freeze button first, which is exactly how a second freeze writer sneaks back in and reopens the divergence FR-018 exists to kill.

### The two seams that exist today

The change has one insertion point on each side, both already isolated:

- **Console:** `POST /api/flow/[kitId]/step/[stepId]/freeze/route.ts` delegates to `orchestration.freezeArtifact(projectDir, kitId, stepId, artifactId)`. That one service method is the entire freeze path; redirecting it redirects the console.
- **Harness:** `src/cli.py` uses an argparse subcommand dispatch (`generate`, `validate`, `lifecycle`, `health`, `costs`, `research`). Adding a `freeze` subcommand is additive and matches the existing shape. `apply_freeze_decision` (ADR-0002, not yet built) is the function it wraps.

## Decision

**The console shells out to a harness CLI subcommand to freeze.** It does not write `FROZEN` itself, run a long-lived service, or reimplement the writer.

### Harness side

Add a `freeze` subcommand to `src/cli.py` that wraps `apply_freeze_decision`:

```
harness freeze \
  --initiative <path> \
  --artifact <artifact-id> \
  --decision <path-to-decision-json> \
  --decided-by <identity>
```

The decision JSON is the serialized `FreezeGateDecision`: the artifact reference, the human's outcome (`APPROVE` / `APPROVE_WITH_CONDITIONS` / `BLOCK` / …), and the content hash the artifact must match. The command:

- exits `0` and writes the canonical Document Control `Status` cell, fires `POST_FREEZE`, appends the Journal, and increments the frozen count — all inside `apply_freeze_decision`, which stays the single writer;
- exits non-zero with a structured error on any invariant failure (`check_authorized_freeze`), a content-hash mismatch, or a missing `decided_by`, and writes nothing.

The exit code and a machine-readable payload on stdout are the contract the console consumes. The console never parses harness internals.

### Console side

`OrchestrationService.freezeArtifact` stops writing `.aieos/state.json` and the horizontal ER row. It:

1. assembles the `FreezeGateDecision` from the human's action in the freeze UI,
2. invokes `harness freeze` as a subprocess,
3. on exit `0`, re-reads the canonical block (the harness just wrote it) to refresh the view; on non-zero, surfaces the structured error to the user and changes nothing.

The freeze UI already renders the `FreezeGateRequest` payload (ADR-0002), so the human-facing surface does not change — only where the write lands.

### Same boundary for the andon resume (ADR-0004)

The andon cord's human resume/clear is a distinct action from freeze and gets its own subcommand (for example `harness resume` / `harness clear-fault`), routed the same way. A resume is not a freeze and must never travel through `harness freeze`; keeping them separate commands enforces that at the boundary.

## Options Considered

| Option | Surface | Assessment |
|--------|---------|------------|
| **CLI subprocess (chosen)** | One argparse subcommand + one subprocess call | No daemon, port, or auth. Freeze is rare, human-initiated, and latency-tolerant, so subprocess start-up cost is irrelevant. Keeps `apply_freeze_decision` the single writer. |
| Local HTTP endpoint | A harness HTTP service the console calls | Introduces a service to start, a port to bind, and a lifecycle and auth story to manage for an action that happens a few times an hour. Cost with no matching benefit at this cadence. |
| Reimplement the writer in TypeScript | A second freeze writer | Rejected outright. Recreates the exact two-writers condition FR-018 exists to remove, now across a language boundary that guarantees they drift. |

The decisive factor is freeze's cadence and criticality: it is a rare, deliberate, human action where correctness dominates and latency is irrelevant. That profile is the textbook case for a subprocess over a service.

## Trade-off analysis

The one real cost is deployment coupling. Shelling out means the console's runtime must be able to invoke the harness — the Python environment has to be reachable from the Next.js process. For the console's actual deployment model this is mild: the README describes it as "a locally deployed web app," and the dark factory, harness, and console already expect to sit in one workspace over shared initiative files. Co-locating the harness CLI (same host, or bundled into the console image) is consistent with how the drivers already run. The alternative service approach would trade this co-location cost for a worse one — a long-lived process to supervise.

Two boundary hazards to handle in implementation, not in this decision:

- **Argument injection.** The console passes user-influenced values (`artifact-id`, `decided-by`) into a subprocess. Invoke with an explicit argument array, never a shell string, so nothing is interpreted by a shell.
- **Decision integrity.** The content hash in the `FreezeGateDecision` is what lets `apply_freeze_decision` reject a freeze against an artifact that changed under it. The console must hash the artifact it showed the human, not re-read at call time, or the check is defeated.

## Consequences

Positive:

- `apply_freeze_decision` stays the single `FROZEN` writer across all three drivers; the console becomes a caller, not a writer. FR-018's divergence class cannot reopen through the console.
- The seam is one subcommand and one service method — small, testable, and reversible.
- The andon resume/clear reuses the identical boundary, so there is one cross-language pattern, not two.
- No new long-lived surface, port, or auth to secure.

Costs and follow-ups:

- The console runtime must have the harness CLI available; the console Docker image or host provisioning grows a Python dependency. Documented as a deployment note, not an architecture problem.
- Subprocess error handling and the structured stdout/exit-code contract must be specified precisely so the console can distinguish "invariant blocked the freeze" from "harness failed to run."
- First cross-language integration test belongs in the Phase 5 three-way switch proof (release plan): a console-initiated freeze must be visible to `read_frozen_artifacts` and to the dark factory.

## Related

- ADR-0002 (dark factory; `apply_freeze_decision` as the single `FROZEN` writer). This ADR is how a non-Python driver reaches that writer.
- ADR-0004 (andon cord). The resume/clear boundary is this same subprocess pattern, on separate subcommands.
- Roadmap FR-020 (console as thin front-end — promotes this boundary to v1) and FR-018 (canonical freeze representation the console reads/writes).
- `AIEOS v1.3 - Three Drivers Release Plan` (vault) — Track C and the decisions log (Q7).
