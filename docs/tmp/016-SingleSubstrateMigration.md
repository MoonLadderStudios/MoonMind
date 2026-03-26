# Single Execution Substrate Migration Plan

> **Goal:** Finish the move to Temporal-backed execution as the **only** real substrate. Remove legacy queue and system paths so MoonMind has one task model, one execution model, one observability model, and one action model.
>
> **Last updated:** 2026-03-26

---

## Relationship to other Temporal implementation notes (009–018)

These `docs/tmp/` plans overlap in topic but differ in scope. **This document (016)** is about **retiring non-Temporal execution substrates** (legacy queue + system orchestration) in code and UI. The others assume **Temporal is already the control plane** and deepen correctness, contracts, or ergonomics:

| Doc | Focus | How it relates to 016 |
|-----|--------|------------------------|
| [009-DefaultNamespace.md](009-DefaultNamespace.md) | Local default `TEMPORAL_NAMESPACE=default`, bootstrap, artifact key alignment | Operational defaults for the same Temporal stack; doc updates should stay consistent with Phase 5 here and with `docs/Temporal/*.md`. |
| [010-CancellationAnalysis.md](010-CancellationAnalysis.md) | Cancel vs terminate, `ActivityCancellationType`, heartbeats | **Single substrate** means operator cancel is `WorkflowHandle.cancel()` / execution APIs only—no parallel queue cancel path. |
| [011-TemporalWorkflowExecutionImprovements.md](011-TemporalWorkflowExecutionImprovements.md) | Fleet model, gaps in `MoonMind.Run`, schedules, versioning | Analytical baseline; several gaps are addressed in 014–015. Treat as background, not a second source of execution truth. |
| [012-TemporalWorkflowMessagePassingImprovements.md](012-TemporalWorkflowMessagePassingImprovements.md) | Signals, Queries, Updates patterns | Message-passing improvements apply **on top of** Temporal-only execution. |
| [013-TemporalWorkflowMessagePassingInventory.md](013-TemporalWorkflowMessagePassingInventory.md) | Inventory of workflows, handlers, queues | Inventory should describe **Temporal** workflows and `mm.*` task queues only (no legacy queue source). |
| [014-TemporalSchedulingImprovements.md](014-TemporalSchedulingImprovements.md) | `start_delay`, Temporal Schedules, recurring tasks | Recurring dispatch is **Temporal Schedule–backed** (see that doc); aligns with Phase 1.5 / 1.6 here. |
| [014-TemporalMessageContracts.md](014-TemporalMessageContracts.md) | `MoonMind.Run` Update/Signal/Query contracts | Control-plane commands on the orchestrator are **Updates** (with validators), not a separate “Mission Control queue.” |
| [015-TemporalIdempotency.md](015-TemporalIdempotency.md) | `update_id`, idempotency keys, workflow-boundary testing | Applies to APIs and workers once execution is Temporal-only. |
| [015-TemporalAgentAlignmentPlan.md](015-TemporalAgentAlignmentPlan.md) | Alignment roadmap (queries, schedules, observability) | Sibling roadmap; prefer **014-TemporalMessageContracts** for current **signal vs update** facts if docs disagree. |
| [017-TemporalTypeSafety.md](017-TemporalTypeSafety.md), [018-TemporalTypeAnnotations.md](018-TemporalTypeAnnotations.md) | Typed activity payloads, binary/json policy | Hardening **Temporal activity boundaries**; unrelated to restoring queue/system sources. |

---

## Why This Matters

The docs and codebase still treat Mission Control as transitional — merging queue, system, and Temporal sources with mixed-source views described as "a convenience rather than a true source of truth." That was useful during migration, but it now:

- **Weakens the "one control plane" story** — operators see multiple sources instead of a unified model.
- **Adds permanent UI and API complexity** — dashboard view model, routing, status maps, pagination, and compatibility adapters all branch on `source`.
- **Blocks doc simplification** — several architecture docs still describe mixed-source rules that only existed for migration.

The target state is clean: **Temporal owns execution truth. Period.**

---

## Current State Audit

### What's Already Done ✅

- Temporal is the execution engine for new task submissions.
- `MoonMind.Run` and `MoonMind.AgentRun` workflows handle managed + external agent execution.
- `/api/executions` (and related dashboard templates) provide CRUD + cancel/signal/update against Temporal.
- `TemporalExecutionRecord` projection row with sync metadata.
- Legacy `moonmind/workflows/system/` and `moonmind/workflows/agent_queue/` removed; queue backend tables dropped (e.g. migration `b92f4891f27c`); system DB tables dropped (e.g. migration `c1d2e3f4a5b6`).
- External Runs tab removed; system submissions rejected from the dashboard.
- **Phases 1–4 (below)** are complete: queue path gone, dashboard execution views Temporal-first, compatibility/router layer collapsed into executions-oriented APIs.

### Residual naming and doc debt (not separate substrates) ⚠️

Code is **Temporal-primary**; remaining items are mostly **vocabulary**, **query parameters**, or **canonical docs** still written for the migration era:

| Area | Notes |
|------|--------|
| **View model** | `build_runtime_config()` exposes `sources.temporal`, `sources.proposals`, `sources.schedules` — no legacy queue execution block. The nested object key **`system`** holds **submit-form defaults** (repository, runtime, attachments policy); it is not the removed "system execution source." |
| **`normalize_status(source, …)`** | Still takes a `source` discriminator so **proposals** vs **Temporal** rows map into shared dashboard chips — not multi-substrate execution. |
| **URLs** | List/detail links may still carry `?source=temporal` for routing clarity; that is a **label**, not a second backend. |
| **Tests / fixtures** | e.g. `createQueueRow` as a legacy alias for Temporal row factories; `queueName: "-"` placeholders on Temporal-shaped rows — cosmetic cleanup optional. |
| **Settings** | `MOONMIND_QUEUE` (or related env) may still name the **Codex worker task queue**, not the removed agent-queue HTTP API. |
| **Canonical Temporal docs** | `SourceOfTruthAndProjectionModel.md`, `TaskExecutionCompatibilityModel.md`, `VisibilityAndUiQueryModel.md`, etc. may still describe `queue` / `system` / mixed-source pagination — **Phase 5** below. |

---

## Migration Phases

**Progress:** Phases **1**, **2**, **3**, and **4** are **complete**. Phase **5** is **not started**.

### Phase 1 — Queue Submission Path → Temporal *(prerequisite)* — **COMPLETE**

**Objective:** Ensure every action that previously routed through `/api/queue/jobs` could be performed through the Temporal execution path.

**Status:** Finished. Gate met: no user-facing action requires the removed queue path.

- [x] **1.1** Audit queue-only features: attachments, live sessions, operator messages, task control, events/SSE, skills list
- [x] **1.2** Map each queue feature to its Temporal equivalent or mark as deferred
- [x] **1.3** Ensure Temporal submit supports submit form fields: runtime, model, effort, repository, publish mode, attachments
- [x] **1.4** Redirect manifest submission to `MoonMind.ManifestIngest` Temporal workflow
- [x] **1.5** Recurring tasks: **Temporal Schedules** are the primary dispatch path (reconciled in API/service layer; see [014-TemporalSchedulingImprovements.md](014-TemporalSchedulingImprovements.md))
- [x] **1.6** Step templates verified against the Temporal execution path

---

### Phase 2 — Collapse Dashboard to Single Source — **COMPLETE**

**Objective:** Remove `queue` and `system` as dashboard **execution** sources. Task list/detail for runs goes through Temporal APIs and projections.

**Status:** Finished.

- [x] **2.1–2.10** View model, dashboard JS, status maps, fixtures, and tests updated for Temporal-first execution (proposals remain a separate non-Temporal **proposal** surface where product requires it)

---

### Phase 3 — Remove Queue Backend Code — **COMPLETE**

**Objective:** Delete the legacy queue execution substrate and scrub queue-shaped operator surfaces.

**Status:** Finished. `agent_queue` router and module tree removed; migrations dropped queue/system tables as applicable; worker exports are Temporal factories only.

- [x] **3.1–3.5** Agent queue router, module, DB models/migrations, empty system workflow dir
- [x] **3.6** `tests/unit/orchestrator_removal/` removed or folded when redundant
- [x] **3.7–3.8** Queue-only tests and package exports cleaned up

**Gate (met):** No `agent_queue` or system workflow modules; queue tables absent from schema; operator surface is not queue-backed.

---

### Phase 4 — Eliminate Compatibility Layer Complexity — **COMPLETE**

**Objective:** Simplify compatibility adapters now that only one execution source exists.

**Status:** Finished. Multi-source task compatibility routing removed; executions-oriented APIs are the bridge to Temporal.

- [x] **4.1–4.7** Simplify services, remove source resolution endpoints and `temporalCompatibility`-style blocks from the view model where redundant

**Gate (met):** No multi-source routing for **execution**; API surface reflects Temporal as the run substrate.

---

### Phase 5 — Update Documentation

**Objective:** Remove transitional language from architecture docs so they match the **Temporal-only execution** reality already implemented in code.

- [ ] **5.1** Update `SourceOfTruthAndProjectionModel.md`: remove "migration stance", "staging", "mixed-source" sections; steady-state contract only
- [ ] **5.2** Update `TaskExecutionCompatibilityModel.md`: remove `queue`/`system` source definitions and multi-source pagination; Temporal-only (or archive if redundant)
- [ ] **5.3** Update `VisibilityAndUiQueryModel.md`: remove mixed-source pages; retire multi-source pagination section
- [x] **5.4** Delete `docs/tmp/OrchestratorRemovalPlan.md` — superseded *(done)*
- [x] **5.5** Update `docs/MoonMindArchitecture.md` if any queue/system execution references remain
- [ ] **5.6** Update remaining Temporal architecture docs (`TemporalArchitecture.md`, `ActivityCatalogAndWorkerTopology.md`, `WorkflowTypeCatalogAndLifecycle.md`, `RoutingPolicy.md`) to remove transitional queue/system language, purge obsolete feature flags, and use steady-state Temporal vocabulary *(may overlap dedicated trackers under `docs/tmp/remaining-work/`)* 
- [ ] **5.7** Update Roadmap: close H.1 (system removal) and mark substrate migration items done
- [ ] **5.8** Delete or archive **this** document once Phase 5 is complete and readers are pointed at canonical `docs/Temporal/` sources

**Cross-links:** When editing canonical docs, align **namespace** story with [009-DefaultNamespace.md](009-DefaultNamespace.md), **scheduling** with [014-TemporalSchedulingImprovements.md](014-TemporalSchedulingImprovements.md), and **control-plane messages** with [014-TemporalMessageContracts.md](014-TemporalMessageContracts.md).

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Existing queue-backed tasks in production DB | Addressed by migrations / cutover; any remaining risk is **historical data** in backups — document retention policy separately. |
| Feature gap in Temporal path | Phase 1 gate met: feature audit completed before queue removal. |
| Queue SSE events not replicated | Superseded: execution events/SSE follow the **Temporal execution** path. |
| Attachment upload path | Verified as part of Temporal submit + artifact flows during Phase 1–2. |
| Recurring tasks still app-cron driven | **Mitigated:** Temporal Schedules are primary (see [014-TemporalSchedulingImprovements.md](014-TemporalSchedulingImprovements.md)). |

---

## Relationship to Roadmap

This plan **subsumes and extends** Housekeeping item **H.1** (Complete system removal). It goes further by also removing the queue substrate and collapsing the execution compatibility layer.

Successful completion delivers:

- One task model (Temporal workflow execution)
- One execution model (Temporal activities + workers)
- One observability model (Temporal Visibility + projection cache)
- One action model (Temporal start/update/signal/cancel via executions APIs)
- Simpler API surface
- Simpler dashboard code
- Simpler docs (after Phase 5)
