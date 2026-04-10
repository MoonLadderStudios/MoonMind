# Implementation Plan: codex-managed-session-plane-phase9

**Branch**: `135-codex-managed-session-plane-phase9` | **Date**: 2026-04-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/135-codex-managed-session-plane-phase9/spec.md`

## Summary

Implement the minimal Phase 9 session continuity projection API for task-scoped Codex managed sessions. This slice adds a task-run API endpoint that reads the durable managed-session record, resolves the persisted artifact refs into artifact metadata, and returns a grouped server-side read model that Mission Control can consume without inferring continuity from live container state.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: FastAPI task-run router, SQLAlchemy-backed `TemporalArtifactService`, JSON-backed `ManagedSessionStore`, existing artifact metadata schemas
**Storage**: Temporal artifact metadata tables plus file-backed managed-session records under `MOONMIND_AGENT_RUNTIME_STORE`
**Testing**: focused pytest router coverage plus final verification via `./tools/test_unit.sh`
**Target Platform**: Linux server containers in the existing MoonMind Docker deployment
**Project Type**: Backend API and projection read-model slice
**Performance Goals**: One request should build the projection from durable refs only, with no live session-controller or container dependency
**Constraints**: preserve task-run ownership semantics; reuse persisted artifact metadata rather than container-local state; keep the endpoint small enough that later UI work can iterate without changing orchestration contracts
**Scale/Scope**: one task-scoped session projection per request, using the latest durable session epoch only for the MVP

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The endpoint reads durable orchestration outputs and does not move control or execution into the API layer.
- **II. One-Click Agent Deployment**: PASS. No new operator dependency or deployment step is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The projection is an artifact/read-model API over generic artifact metadata and session refs, not a Codex-specific execution shortcut.
- **IV. Own Your Data**: PASS. Session continuity becomes easier to inspect after container teardown because the API reads persisted artifacts and session metadata.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The work adds a typed response contract and API tests rather than UI-side grouping heuristics.
- **VII. Powerful Runtime Configurability**: PASS. Existing runtime-store and artifact-store env configuration remains authoritative.
- **VIII. Modular and Extensible Architecture**: PASS. The change stays inside API/router and schema boundaries and does not alter workflow orchestration.
- **IX. Resilient by Default**: PASS. The endpoint is explicitly durable-state-first and does not fail merely because the session container is gone.
- **XI. Spec-Driven Development**: PASS. Phase 9 is implemented from fresh spec/plan/tasks artifacts.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Migration sequencing stays in the spec artifacts; canonical docs already define the desired projection behavior.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The feature extends the live task-run API directly with no compatibility shim layer.

## Research

### Decision: Build the MVP projection from the durable managed-session record plus artifact metadata lookups

- **Rationale**: Phase 8 already persists the latest continuity refs and runtime artifact refs on `CodexManagedSessionRecord`, which is enough to serve a first useful projection without querying live session infrastructure.
- **Alternatives considered**:
  - Query live session-controller/container state: rejected because it violates the artifact-first continuity rule.
  - Add a new persisted projection table: rejected because Phase 9 only needs a minimal read model and the current durable refs already exist.

### Decision: Reuse task-run ownership checks before reading artifacts through a service principal

- **Rationale**: The task-run router already owns operator-facing access control. Once access is granted, the API can resolve artifact metadata through a service principal without exposing raw artifact permissions complexity to the client.
- **Alternatives considered**:
  - Require end-user artifact ownership for each resolved artifact: rejected because managed-session artifacts are often system-produced and the route already enforces task-level access.

### Decision: Limit the MVP to the latest durable epoch and grouped latest refs

- **Rationale**: The Phase 9 plan only requires a minimal continuity projection. Returning the latest epoch plus grouped latest artifacts satisfies the exit criteria and keeps the implementation small.
- **Alternatives considered**:
  - Full historical timeline across all epochs and step executions: rejected as premature for the Phase 9 MVP.

## Project Structure

### Documentation (this feature)

```text
specs/135-codex-managed-session-plane-phase9/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-run-artifact-session-projection.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── api/
    └── routers/
        └── task_runs.py

moonmind/
└── schemas/
    └── temporal_artifact_models.py

tests/
└── unit/
    └── api/
        └── routers/
            └── test_task_runs.py
```

**Structure Decision**: Keep the MVP inside the existing task-run API surface. Add typed response models under `moonmind/schemas/temporal_artifact_models.py`, implement the projection assembly in `api_service/api/routers/task_runs.py`, and verify behavior in `tests/unit/api/routers/test_task_runs.py`.

## Data Model

- **ArtifactSessionProjectionModel**
  - `task_run_id`
  - `session_id`
  - `session_epoch`
  - `grouped_artifacts`
  - `latest_summary_ref`
  - `latest_checkpoint_ref`
  - `latest_control_event_ref`
  - optional `latest_reset_boundary_ref`
- **ArtifactSessionGroupModel**
  - `group_key`
  - `title`
  - `artifacts[]`
- **Projection Source Inputs**
  - durable `CodexManagedSessionRecord`
  - artifact metadata for `stdout_artifact_ref`, `stderr_artifact_ref`, `diagnostics_ref`
  - artifact metadata for `latest_summary_ref`, `latest_checkpoint_ref`, `latest_control_event_ref`, `latest_reset_boundary_ref`

## Contracts

- Add `specs/135-codex-managed-session-plane-phase9/contracts/task-run-artifact-session-projection.md` capturing the endpoint path, status/error rules, and response shape for the MVP read model.

## Implementation Plan

1. Add failing router tests for successful projection reads, grouped artifact output, durable-only behavior without a live container, owner checks, and missing-session errors.
2. Extend `moonmind/schemas/temporal_artifact_models.py` with typed response models for artifact session groups and the projection envelope.
3. Extend `api_service/api/routers/task_runs.py` with:
   - managed-session store root helpers,
   - task-run ownership validation for projection reads,
   - projection assembly from the durable session record and artifact metadata lookups,
   - stable `404` error mapping for missing or mismatched task/session pairs.
4. Keep grouping server-defined and minimal by returning runtime and continuity/control buckets plus the latest ref fields needed by later UI work.
5. Run focused router tests, run runtime scope validation, rerun the full unit suite, and mark completed tasks in `tasks.md`.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py`
2. `SPECIFY_FEATURE=135-codex-managed-session-plane-phase9 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. `SPECIFY_FEATURE=135-codex-managed-session-plane-phase9 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
4. `./tools/test_unit.sh`

### Manual Validation

1. Request `GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}` for a persisted Codex managed session and confirm the response can drive a continuity panel without any live session dependency.
2. Confirm the response groups runtime artifacts separately from continuity/control artifacts and reports the current session epoch.
3. Confirm a non-owner receives `403` and a missing or mismatched session/task pair receives `404` with `session_projection_not_found`.
