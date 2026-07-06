# ADR-0001: CI/CD capability substrate co-located in the agent harness

**Status:** Accepted
**Date:** 2026-07-04
**Deciders:** AIEOS framework owner

## Context

`aieos-agent-harness` began as a single-purpose component: a multi-agent orchestration engine that bridges AIEOS governance artifacts (Markdown specs, templates, prompts, validators) and AI providers. Its core (`src/` root) is the *governance orchestration* concern — lifecycle binding, routing strategies, provider adapters, state management, convergence, and structural-invariant enforcement.

During milestone **M2 (spec-driven CI/CD)**, a second body of code was added under `src/cicd/`: the *runtime capability substrate* for the CI/CD path. It contains:

- **Capability registry** — artifact-store-backed, with a read-through in-memory index
- **Attestation verification** — refuses to register an adapter without a valid conformance attestation for the current (or within-grace) contract version
- **Tool-using agent interface** — `DeterministicAgent` and `LLMAgent` variants
- **Structured event emission** — `run.start` / `task.start` / `task.evidence` / `task.result` / `run.end` to stdout
- **Contract tests** for all of the above, plus the frozen `conformance-attestation.schema.json`

To a new contributor scanning the tree, `src/cicd/` reads as scope creep: the repo's name and README describe an *orchestration engine*, yet a substantial CI/CD substrate lives inside it. This ADR records why that placement was chosen and the boundary that keeps it defensible.

Forces at play:

- **Conceptual kinship.** The substrate is the *runtime* side of the same idea the harness embodies — executing governed work through pluggable agents. The registry's `DeterministicAgent`/`LLMAgent` interface is a close cousin of the harness's provider-adapter abstraction.
- **Dependency direction.** `aieos-pipeline-runner` (a separate repo) is the orchestrator that runs CI/CD specs; it *registers adapters against* this substrate. The substrate is a dependency of the runner, not the other way around.
- **Maturity.** M2 was the substrate's first cut. Its public surface (registry API, attestation rules, event schema) is still settling.
- **Cost of premature extraction.** A separate repo means its own CI, versioning, release cadence, and a published package boundary — overhead that only pays off once there is a second consumer or an independent release need.

Observed code reality (verified): `src/cicd/` imports **only its own submodules** (`.registry`, `.models`, `.attestation`, `.artifact_store`, `.agents`) — never the harness root — and the harness root **never imports** `src/cicd`. The two concerns already sit behind a clean, one-directional (in fact, zero-directional) code boundary; they merely share a repository.

## Decision

Keep the M2 CI/CD capability substrate **co-located in `aieos-agent-harness` under `src/cicd/`**, rather than extracting it into its own repository — for now. Treat `src/cicd/` as an internal package with a hard rule that it must not depend on the harness core and the harness core must not depend on it, preserving the option to extract later at low cost.

## Options Considered

### Option A: Co-locate `src/cicd/` in the agent harness (chosen)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — one repo, one CI job, one test suite |
| Cost | Low — no new repo/release/versioning overhead |
| Scalability | Medium — fine until the substrate has an external consumer or independent release cadence |
| Team familiarity | High — contributors already work in the harness repo |

**Pros:**

- No premature repo/packaging overhead while the substrate's surface is still settling.
- Shared tooling, CI, and test conventions; one place to run everything.
- Conceptual cohesion — runtime execution of governed capabilities lives next to the orchestration engine.

**Cons:**

- Reads as scope creep; the repo name undersells what it contains (this ADR is the mitigation).
- A consumer that wants only the substrate must depend on the whole harness repo.
- Independent versioning of the substrate is not possible without a split.

### Option B: Extract to a dedicated repo (e.g. `aieos-runtime-substrate`, or fold into `aieos-pipeline-runner`)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium–High — new repo, CI, release process, published boundary |
| Cost | Higher up front; pays off only with a second consumer |
| Scalability | High — independent versioning and a clean published package |
| Team familiarity | Medium — one more repo to track |

**Pros:**

- Crisp boundary; the substrate's contract becomes explicit and independently versioned.
- `aieos-pipeline-runner` (and future consumers) can depend on just the substrate.

**Cons:**

- Overhead (CI, versioning, release) for a surface that is not yet stable.
- Folding into `pipeline-runner` inverts the dependency — the runner depends on the substrate, so the substrate should not live inside it.

## Trade-off Analysis

The decisive factors are **maturity** and **consumer count**. Today the substrate has exactly one effective consumer (the pipeline-runner, via adapter registration) and an unsettled public surface. Extraction buys independent versioning and a clean package boundary, but those benefits are only realized when a *second* consumer exists or the substrate needs to release on its own cadence — neither of which is true yet. Co-location costs almost nothing given the code is already decoupled; the only real cost is contributor confusion, which documentation (this ADR) addresses directly. Extraction remains cheap *later* precisely because the import boundary is already clean, so deferring the split loses little.

## Consequences

- **Easier now:** single repo to build, test, and reason about; the substrate evolves alongside the harness without cross-repo coordination.
- **Harder now:** the substrate cannot be versioned or released independently; an external consumer must pull the whole harness.
- **To revisit:** extract `src/cicd/` into its own repo when any of these becomes true — (a) a second consumer needs the substrate without the harness, (b) the substrate needs an independent release cadence, or (c) its public surface (registry API, attestation rules, event schema) has stabilized to a v1 worth publishing.

## Action Items

1. [ ] Add an import-boundary check (lint or a unit test) asserting `src/cicd/` does not import the harness core, and the harness core does not import `src/cicd/` — so the extraction option stays cheap.
2. [ ] Link this ADR from the repo `README.md` and `CLAUDE.md` so the `src/cicd/` placement is discoverable rather than surprising.
3. [ ] Re-evaluate the split at the next milestone review, or when a second substrate consumer appears.
