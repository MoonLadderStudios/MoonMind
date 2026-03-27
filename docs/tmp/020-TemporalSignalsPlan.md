# Temporal Signals Plan

Status: **Draft**
Owners: MoonMind Engineering
Last Updated: 2026-03-27
Canonical Doc: [`docs/Temporal/TemporalSignalsSystem.md`](../Temporal/TemporalSignalsSystem.md)

> [!NOTE]
> This document tracks phased implementation work for the Temporal Signals System.
> It is intentionally temporary and should be removed or archived once the desired-state design is implemented and the canonical doc remains accurate without migration notes.

---

## 1. Objective

Implement the Temporal Signals System described in [`docs/Temporal/TemporalSignalsSystem.md`](../Temporal/TemporalSignalsSystem.md) so that MoonMind has:

- a clear separation between asynchronous Signals and acknowledged Updates,
- consistent signal contracts across run, agent-run, auth-profile-manager, and OAuth workflows,
- scheduling-aware signal behavior for deferred execution,
- callback ingestion and workflow-to-workflow coordination that are replay-safe and duplicate-safe,
- signal-driven state changes that stay aligned with search attributes, memo, and execution projections.

---

## 2. Current State

Based on the current repo state:

- `MoonMind.Run` already exposes `Pause`, `Resume`, `Approve`, and `Cancel` as workflow updates, and exposes `ExternalEvent` plus `reschedule` as signals.
- `moonmind/schemas/temporal_models.py` currently exposes only `ExternalEvent` through the generic signal API, while `reschedule` is handled through a separate execution path.
- `TemporalClientAdapter` still supports generic `signal_workflow(...)`, `send_reschedule_signal(...)`, and batch lowercase `pause` / `resume` signaling for running workflows.
- `TemporalExecutionService.signal_execution(...)` is aligned only to `ExternalEvent`, while `update_execution(...)` carries the acknowledged execution controls.
- `MoonMind.AgentRun`, `MoonMind.AuthProfileManager`, and `MoonMind.OAuthSession` all use internal workflow Signals, but most payloads are still raw dicts rather than explicit shared models.
- `MoonMind.Run.child_state_changed` currently uses positional signal arguments instead of a single structured payload.
- `MoonMind.AuthProfileManager` maintains mutable queue and lease state, including an async `release_slot(...)` handler that can interleave with other signal handlers.
- Existing docs such as `TemporalSignalsResearch.md`, `WorkflowTypeCatalogAndLifecycle.md`, `TemporalScheduling.md`, `WorkerPauseSystem.md`, and `IntegrationsMonitoringDesign.md` now partially overlap but do not yet describe one consolidated desired-state signal system.

---

## 3. Delivery Principles

Implementation should preserve the following:

- Updates remain the authoritative request/response path for acknowledged execution mutations.
- Signals remain compact and must not carry raw secrets or large opaque payloads.
- Signal contracts must be safe for in-flight workflows or changed with an explicit cutover plan.
- Workflow-boundary tests are mandatory for signal-path changes.
- Public API signal names and internal workflow coordination names must not drift into ambiguous mixed use.

---

## 4. Phase Overview

### Phase 0. Contract Freeze and Doc Alignment

Outcome:

- One agreed signal taxonomy across docs, schemas, and workflow code.

Detailed tasks:

1. [DONE] Compare `TemporalSignalsSystem.md` against `WorkflowTypeCatalogAndLifecycle.md`, `TemporalScheduling.md`, `IntegrationsMonitoringDesign.md`, `WorkerPauseSystem.md`, and `ManagedAndExternalAgentExecutionModel.md` and identify any remaining conflicts in signal-vs-update ownership.
2. [DONE] Decide which names are public execution-control contracts versus internal workflow-only coordination contracts, and remove ambiguous references that imply the same action can be driven interchangeably through both paths.
3. [DONE] Document the public rule that `Pause`, `Resume`, `Approve`, and `Cancel` are update-style controls, while `ExternalEvent` and `reschedule` remain signal-style controls.
4. [DONE] Document the internal rule that `request_slot`, `release_slot`, `report_cooldown`, `sync_profiles`, `slot_assigned`, `completion_signal`, `child_state_changed`, `profile_assigned`, `finalize`, and `cancel` are workflow-local coordination contracts.
5. [DONE] Replace stale research assumptions in any remaining docs that still claim `MoonMind.Run` lacks update or signal handlers.
6. [DONE] Record explicit compatibility notes for any signal shape changes that could affect in-flight workflow histories.

Exit criteria:

- Canonical docs agree on the signal taxonomy.
- No current design doc claims a contradictory control path for the same behavior.

### Phase 1. Shared Signal Contract Primitives

Outcome:

- Signal payloads use explicit contract objects rather than ad hoc dict conventions.

Detailed tasks:

7. Introduce a shared Temporal signal contracts module for compact payload models used across workflows.
8. Define public callback-oriented payload models for `ExternalEvent` and scheduling-oriented payload models for `reschedule`.
9. Define internal coordination payload models for auth-profile manager requests/releases/cooldowns/profile syncs and slot assignments.
10. Define parent/child coordination payload models for `child_state_changed`, `profile_assigned`, and `completion_signal`.
11. Define compact OAuth session control payloads or explicit empty-payload models so the session contract is still typed even when minimal.
12. Establish field-level rules for dedupe keys, artifact references, timestamps, and correlation identity so future signal additions follow one shape discipline.
13. Keep any payload additions backward-safe for already-running workflows by using optional/defaulted fields or explicit cutover boundaries where needed.

Validation:

- Unit tests for payload parsing and validation.
- Boundary tests proving current worker-bound invocation shapes still decode correctly.

### Phase 2. `MoonMind.Run` Control Surface Reconciliation

Outcome:

- `MoonMind.Run` has a clean desired-state split between Signals and Updates.

Detailed tasks:

14. Audit every API route, service method, adapter helper, and dashboard action that can control `MoonMind.Run`, then map each path to either Update, Signal, or cancellation semantics.
15. Keep `ExternalEvent` on the signal path and ensure its workflow-side handler, service validation, and execution projection behavior all use the same compact contract.
16. Keep `reschedule` as the dedicated scheduled-wait signal and ensure it is not exposed through the generic signal endpoint unless that is explicitly intended and documented.
17. Remove or refactor any remaining lowercase batch `pause` / `resume` signal usage that conflicts with the desired-state update-based pause/resume contract.
18. Ensure the worker-pause/quiesce path either targets an explicit workflow pause signal contract or is rewritten to use the supported control surface instead of relying on mismatched lowercase names.
19. Verify that `Pause`, `Resume`, `Approve`, and `Cancel` all stay on the update/cancel path end to end, including dashboard capabilities and service routing.
20. Ensure `MoonMind.Run` updates search attributes and memo consistently after signal-driven transitions, especially for `awaiting_external`, `scheduled`, and `executing` state changes.
21. Confirm that deferred scheduling behavior matches `TemporalScheduling.md`: `start_delay` for immutable delayed starts, in-workflow wait plus `reschedule` for mutable ones.

Validation:

- Workflow-boundary tests for `ExternalEvent`.
- Workflow-boundary tests for `reschedule`.
- API/service tests proving control actions route to the correct Temporal primitive.

### Phase 3. Parent/Child and Managed Runtime Signal Hardening

Outcome:

- `MoonMind.Run`, `MoonMind.AgentRun`, and `MoonMind.AuthProfileManager` coordinate through structured, duplicate-safe signals.

Detailed tasks:

22. Replace positional `child_state_changed` arguments with one structured payload and update every sender, receiver, and test in the same change.
23. Move `profile_assigned` and `completion_signal` onto explicit shared payload shapes so they can evolve without fragile dict guessing.
24. Add explicit duplicate-handling rules in `MoonMind.AgentRun` for repeated `slot_assigned` or repeated completion notifications.
25. Add duplicate-handling rules in `MoonMind.AuthProfileManager` so repeated `request_slot` signals from the same requester do not multiply queue entries.
26. Make `release_slot` fully safe to repeat and ensure stale defensive releases do not corrupt lease ownership.
27. Guard async queue/lease mutations in `MoonMind.AuthProfileManager` so awaited handlers cannot interleave into invalid shared state.
28. Preserve enough manager state across Continue-As-New and restart recovery to keep slot coordination correct after long runtimes or crashes.
29. Ensure defensive parent-side slot release stays aligned with the desired-state contract and does not depend on stale child identity assumptions.

Validation:

- Boundary tests for `MoonMind.AgentRun` <-> `MoonMind.AuthProfileManager`.
- Race-style tests for duplicate slot requests/releases/cooldowns.
- Restart or Continue-As-New tests for persisted lease state.

### Phase 4. External Callback and Integration Event Reliability

Outcome:

- External async provider events are correlated, deduped, and replay-safe.

Detailed tasks:

30. Standardize the callback ingestion path so external systems always enter Temporal through authenticated MoonMind APIs and compact `ExternalEvent` signaling.
31. Ensure correlation storage remains the lookup source for inbound callbacks rather than visibility scans or run-id-only assumptions.
32. Store large raw callback payloads as artifacts and pass only compact metadata plus artifact references through the signal contract.
33. Move duplicate-provider-event handling to the authoritative workflow-side semantics, with service-side checks only as defense in depth.
34. Ensure late non-terminal callback events cannot reopen a terminal external integration state.
35. Ensure terminal callback handling and polling fallback handling share one state machine so races do not double-complete or double-fail a workflow.
36. Preserve callback correlation and bounded dedupe state across Continue-As-New.

Validation:

- Workflow tests for duplicate callbacks.
- Hybrid callback-versus-polling race tests.
- Continue-As-New compatibility tests for integration wait paths.

### Phase 5. OAuth and Session Workflow Consistency

Outcome:

- `MoonMind.OAuthSession` uses the same signal discipline as the rest of the system.

Detailed tasks:

37. Convert OAuth session signals to the shared signal-contract style even if they remain minimal.
38. Ensure `finalize` and `cancel` handlers remain lightweight flag setters that only unblock the main workflow wait path.
39. Verify that session status transitions, audit behavior, and timeout handling remain consistent when signals arrive late, duplicate, or immediately before timeout.
40. Ensure any future operator metadata added to OAuth signals stays compact and durable-history-safe.

Validation:

- Workflow tests for finalize-before-timeout, cancel-before-timeout, and duplicate finalize/cancel behavior.

### Phase 6. Public API, Adapter, and Dashboard Cleanup

Outcome:

- The public control plane matches the desired-state signal system without legacy ambiguity.

Detailed tasks:

41. Reconcile `moonmind/schemas/temporal_models.py`, `TemporalExecutionService`, and `TemporalClientAdapter` so each public signal or update path exists exactly once and is named consistently.
42. Remove or redesign generic signaling helpers that enable unsupported control actions to bypass the desired-state API contract.
43. Align Mission Control action buttons and capability flags with the desired-state split: updates for acknowledged controls, signals for async external event ingress only.
44. Update any routing-policy or dashboard docs that still describe outdated generic signal usage.
45. Ensure projection refresh behavior after signals and updates continues to honor the source-of-truth rules in `SourceOfTruthAndProjectionModel.md`.

Validation:

- API tests covering supported and unsupported update/signal names.
- UI or deterministic manual verification for execution detail actions and external event handling.

### Phase 7. Replay Safety, Rollout, and Removal of Stale Paths

Outcome:

- The new signal system is safe for long-running workflows and the old ambiguous paths are removed cleanly.

Detailed tasks:

46. Review every signal or update shape change for in-flight compatibility risk and add replay-safe guards or cutover notes where required.
47. Add regression tests for old persisted payload or invocation shapes that may still exist in open workflows.
48. Remove stale helper paths, outdated docs, and unused signal names once the desired-state contracts are live.
49. Audit batch pause, reschedule, and callback flows in staging/local Temporal to ensure no hidden mismatched names remain.
50. Archive or delete temporary implementation notes once the canonical docs and runtime behavior fully match.

Validation:

- Replay-style or compatibility tests for affected workflows.
- End-to-end validation in the Temporal-backed local stack.

---

## 5. Cross-Cutting Work Items

These concerns span all phases and should be tracked continuously:

1. **Secret hygiene**
   - confirm that no signal payload, memo field, artifact preview, or diagnostic path leaks credentials.

2. **Observability**
   - ensure signal-driven state changes are visible through `mm_state`, related search attributes, memo summaries, and execution projections.

3. **Contract ownership**
   - keep one clear owner per signal family so future features do not reintroduce generic catch-all control names.

4. **In-flight safety**
   - any change to Temporal-facing payload shape, handler signature, or signal/update ownership must be reviewed for replay and live-run safety.

5. **Doc synchronization**
   - keep Temporal architecture docs aligned as implementation decisions solidify.

---

## 6. Initial Task Breakdown

The most practical first slice is:

1. Freeze the signal taxonomy across docs and schemas.
2. Introduce shared payload contracts for internal and external signal families.
3. Reconcile `MoonMind.Run` so pause/resume/approve remain update-based and `ExternalEvent` / `reschedule` remain signal-based.
4. Remove or redesign mismatched lowercase batch pause/resume signal behavior.
5. Convert parent/child coordination signals to structured payloads.
6. Add dedupe rules and concurrency protection to `MoonMind.AuthProfileManager`.
7. Harden callback ingestion and `ExternalEvent` race handling.
8. Add workflow-boundary regression tests for every changed signal family.

---

## 7. Risks and Decisions to Resolve

Open decisions:

- Should system-wide quiesce use a dedicated workflow pause signal contract, or should it remain purely an update/cancel/drain operation with no batch workflow signal path?
- Should `reschedule` stay outside the generic signal endpoint permanently, or become an explicitly listed public signal contract?
- How much provider-event dedupe state should be retained in workflow memory before forcing Continue-As-New?
- Which existing in-flight payload shapes must be preserved exactly versus versioned through a cutover?

Primary risks:

- mismatched control names causing operator actions to silently do nothing,
- changing signal handler shapes without protecting in-flight workflows,
- duplicate external callbacks or slot requests causing invalid durable state,
- state projections claiming a signal “applied” when the workflow contract actually rejected it,
- leaving stale generic signaling helpers that reintroduce ambiguity after cleanup.

---

## 8. Completion Criteria

This plan is complete when all of the following are true:

- the runtime behavior matches `docs/Temporal/TemporalSignalsSystem.md`,
- public execution controls use Updates or cancellation where the desired-state contract says they should,
- public async external ingress uses the documented signal path,
- internal workflow coordination signals use explicit structured payloads,
- duplicate delivery and replay safety are verified for all critical signal families,
- stale mixed-mode signal paths and stale docs are removed,
- this temporary plan can be archived or deleted without losing the durable system design.
