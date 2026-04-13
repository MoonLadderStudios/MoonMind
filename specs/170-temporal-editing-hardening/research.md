# Research: Temporal Editing Hardening

## Decision 1: Keep Telemetry Best-Effort and Bounded

**Decision**: Client and server telemetry for Temporal task editing should be best-effort, bounded, and non-blocking.

**Rationale**: Operators need rollout visibility, but telemetry must not create a new failure mode for editing or rerun. Existing MoonMind patterns already treat metrics/logging as best-effort. The feature also handles task instructions and artifact-backed inputs, so telemetry must avoid raw payload content and only include bounded event names, modes, update names, states, outcomes, and normalized failure reasons.

**Alternatives considered**:

- Persisting every event in a new audit table: rejected because Phase 5 needs production hardening without new storage or migrations.
- Sending full request/response payloads to telemetry: rejected because it risks leaking instructions, artifact content, or secrets.
- Blocking submit when telemetry fails: rejected because observability outages must not affect operator workflow correctness.

## Decision 2: Use Existing Update Boundary for Server Submit Telemetry

**Decision**: Server telemetry for `UpdateInputs` and `RequestRerun` should live at the execution update route boundary.

**Rationale**: `/api/executions/{workflowId}/update` is the canonical submit boundary for both edit and rerun. It sees update name, current execution state, validation failures, accepted/rejected responses, and backend application outcomes. Emitting here avoids duplicating logic in the Temporal service while still observing operator-facing submit attempts and results.

**Alternatives considered**:

- Emitting only inside workflow code: rejected because terminal rerun requests may be handled without a live workflow update and because frontend submit failures should still be visible at the control plane boundary.
- Emitting only in frontend code: rejected because backend validation and lifecycle outcomes would be invisible to server-side rollout health.
- Adding a separate telemetry ingestion endpoint first: rejected for this phase because existing metrics/logging surfaces are enough for rollout hardening.

## Decision 3: Keep Route and Mode Semantics Centralized in the Existing Frontend Helper

**Decision**: Route parsing, edit/rerun href generation, mode precedence, and payload construction should remain centralized in the Temporal task editing frontend helper.

**Rationale**: Phase 5 needs regression safety around rerun-over-edit precedence and no queue fallback. A single helper gives tests a stable surface for canonical routes and payload shape while keeping the shared `/tasks/new` page from duplicating query handling.

**Alternatives considered**:

- Inline query parsing in each entrypoint: rejected because it increases drift risk between detail navigation and shared submit handling.
- Separate edit and rerun pages: rejected because the canonical design keeps create, edit, and rerun on one shared submit page.

## Decision 4: Treat Queue-Era Cleanup as Primary Runtime Cleanup

**Decision**: Remove queue-era references from current primary runtime UI, helper, submit, redirect, and operator-facing copy surfaces; leave clearly historical specs or migration artifacts only when they are not active guidance.

**Rationale**: The feature request targets production runtime readiness, not historical record rewriting. Removing active queue-era language prevents operator confusion, while preserving archived specs avoids creating misleading history churn.

**Alternatives considered**:

- Rewrite every historical spec containing queue-era wording: rejected because those specs describe past work and are not primary runtime surfaces.
- Keep queue-era wording in current UI as compatibility language: rejected because the product is pre-release and the constitution favors clean removal over compatibility shims.

## Decision 5: Validate Rollout Readiness with Existing Runtime Flags and Tests

**Decision**: Use the existing `temporalTaskEditing` flag and dashboard runtime config for rollout readiness. Add tests and quickstart checks rather than adding new rollout persistence.

**Rationale**: The current runtime already exposes dashboard feature flags. Phase 5 needs confidence that the flag can be enabled in local/staging and expanded after health signals are acceptable. New persistent rollout state would add complexity without improving the immediate readiness gate.

**Alternatives considered**:

- Add a cohort management table: rejected as out of scope for this hardening phase.
- Enable all operators immediately without staged validation: rejected because the feature explicitly requires dogfood and staged rollout gates.
