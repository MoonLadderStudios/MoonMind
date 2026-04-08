# Implementation Plan: live-logs-session-plane-producer

**Branch**: `140-live-logs-session-plane-producer` | **Date**: 2026-04-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/140-live-logs-session-plane-producer/spec.md`

## Summary

Implement the Phase 2 session-plane producer slice by extending the existing managed-session controller, supervisor, and Codex session adapter so the session plane emits normalized observability rows for resume, steering, interruption, clear/reset, termination, and continuity publication. The shared `RunObservabilityEvent` contract from Phase 1 remains unchanged; this slice focuses on producing the missing events and making event publication best-effort so runtime control and artifact persistence remain reliable.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: `DockerCodexManagedSessionController`, `ManagedSessionSupervisor`, `CodexSessionAdapter`, `ManagedSessionStore`, `ManagedRunStore`, `RuntimeLogStreamer`  
**Storage**: file-backed managed-session store, file-backed managed-run store, artifact-backed `observability.events.jsonl` journal  
**Testing**: pytest unit tests plus focused final verification via `./tools/test_unit.sh`  
**Target Platform**: Docker/Compose-hosted MoonMind managed-runtime workers  
**Project Type**: backend managed-session runtime and workflow adapter  
**Performance Goals**: keep session-event publication cheap and non-blocking relative to control actions; preserve shared run-global sequence semantics  
**Constraints**: keep the Phase 1 event contract, keep artifact-first semantics, do not leak provider-native payloads, do not make observability publication authoritative for control success  
**Scale/Scope**: Phase 2 only; no historical events endpoint changes and no frontend timeline rendering work

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The session plane emits MoonMind-normalized observability rows rather than raw provider events.
- **II. One-Click Agent Deployment**: PASS. No new infrastructure or external service is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The rows stay in the shared `RunObservabilityEvent` contract with optional session metadata only.
- **IV. Own Your Data**: PASS. Timeline facts remain artifact-backed and MoonMind-owned.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill-runtime storage or resolution changes are introduced.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. This slice extends one existing event contract rather than adding a parallel session-event model.
- **VII. Powerful Runtime Configurability**: PASS. The existing feature-flag boundary remains intact; no new rollout switches are needed.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay inside the controller/supervisor/adapter boundaries that already own session control and publication.
- **IX. Resilient by Default**: PASS. Event publication becomes explicitly best-effort so failures do not break control flows or artifact persistence.
- **XI. Spec-Driven Development**: PASS. Phase 2 is isolated into a new spec/plan/tasks slice instead of mutating the completed Phase 0/1 feature.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Only `specs/140-*` captures this implementation slice; canonical docs stay declarative.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. This slice reuses the Phase 1 event kinds already in the schema and removes generic termination-only assumptions where superseded by explicit session lifecycle rows.

## Research

- `ManagedSessionSupervisor.emit_session_event()` already writes `RunObservabilityEvent` rows with session snapshot fields, so the missing work is production at the controller/supervisor boundary rather than contract design.
- `DockerCodexManagedSessionController` already emits `session_started`, `turn_started`, `turn_completed`, `turn_interrupted`, and clear/reset rows in part, but it does not yet emit resume, steer, or explicit terminate rows, and event publication is still able to break control flows.
- `CodexSessionAdapter._ensure_remote_session()` already distinguishes reuse of existing runtime handles from fresh launch, which makes it the correct place to signal `resume_session` versus `start_session` through the workflow control boundary.
- `ManagedSessionSupervisor._publish_record()` already writes durable summary/checkpoint artifacts and observability history, making it the correct place to emit `summary_published` and `checkpoint_published` timeline rows.
- `RunObservabilityEvent` already supports `session_resumed`, `session_terminated`, `summary_published`, and `checkpoint_published`, so Phase 2 should not introduce new top-level schema kinds unless a real gap appears.
- There is no implemented managed-session approval control surface in the current code paths, so this slice covers the stable actions and lifecycle transitions the runtime currently exposes today and leaves approval-specific production for the later approval-capable slice.

## Project Structure

- Add the Phase 2 spec artifact set under `specs/140-live-logs-session-plane-producer/`.
- Update `moonmind/workflows/temporal/runtime/managed_session_controller.py` to emit the missing normalized session rows and make controller-side event emission best-effort.
- Update `moonmind/workflows/temporal/runtime/managed_session_supervisor.py` to emit summary/checkpoint publication rows and make supervisor-side event emission best-effort.
- Update `moonmind/workflows/adapters/codex_session_adapter.py` to signal `start_session` and `resume_session` through the workflow control boundary.
- Extend unit coverage in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`, `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py`, and `tests/unit/workflows/adapters/test_codex_session_adapter.py`.

## Data Model

- See [data-model.md](./data-model.md) for the Phase 2 session event mapping and publication-row semantics.

## Contracts

- [contracts/session-plane-observability-events.md](./contracts/session-plane-observability-events.md)

## Implementation Plan

1. Add failing tests for the missing Phase 2 production behavior:
   - resumed sessions emit `session_resumed`,
   - steer/terminate paths emit normalized session rows,
   - supervisor publication emits `summary_published` and `checkpoint_published`,
   - publication failures do not break successful control/artifact paths,
   - adapter reuse/launch signaling differentiates `resume_session` from `start_session`.
2. Extend the controller’s best-effort session-event helper and wire the missing resume, steer, and terminate rows through it.
3. Extend the supervisor publication path so summary/checkpoint artifact publication writes corresponding observability rows with refs in metadata.
4. Update the Codex session adapter to mirror `start_session` and `resume_session` through the existing workflow/session control signaling path when session handles are launched or reused.
5. Run focused tests and runtime scope validation, then mark completed tasks in `tasks.md`.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/workflows/adapters/test_codex_session_adapter.py`
2. `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

### Manual Validation

1. Reuse an existing managed-session snapshot and confirm the adapter signals `resume_session` instead of relaunching a container.
2. Clear a session and confirm the observability journal still contains the clear row plus the explicit `session_reset_boundary` row.
3. Publish session artifacts and confirm the durable observability history includes `summary_published` and `checkpoint_published` rows with the current session snapshot metadata.
