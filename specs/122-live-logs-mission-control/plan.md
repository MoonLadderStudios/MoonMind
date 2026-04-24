# Implementation Plan: live-logs-mission-control

**Branch**: `122-live-logs-mission-control` | **Date**: 2026-04-01 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/122-live-logs-mission-control/spec.md`

## Summary

Finish the remaining live-logs contract work by persisting a durable workflow-to-managed-run binding, deriving `taskRunId` for task detail from that binding, aligning observability authorization with execution ownership, and updating the React task-detail state model so Mission Control can attach to real managed runs without placeholder dead-ends.

## Technical Context

**Language/Version**: Python 3.12, TypeScript + React 19
**Primary Dependencies**: FastAPI, SQLAlchemy async session helpers, existing managed-runtime launcher/supervisor/store, TanStack Query, Vitest
**Testing**: `./tools/test_unit.sh --python-only --no-xdist ...`, `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, focused integration tests under `tests/integration/temporal/`
**Project Type**: Managed-runtime backend plus Mission Control task-detail frontend
**Constraints**: Preserve artifact-first live-log behavior; avoid hidden auth fallbacks; keep long-running stream coverage at the runtime/observability boundary

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The work keeps observability in MoonMind-owned workflow/runtime layers.
- **II. One-Click Agent Deployment**: PASS. No new infrastructure or operator prerequisites are introduced.
- **VII. Powerful Runtime Configurability**: PASS. The existing `MOONMIND_LOG_STREAMING_ENABLED` flag remains canonical.
- **VIII. Modular and Extensible Architecture**: PASS. Binding and authorization fixes stay in runtime/API boundaries instead of adding UI-side guesses.
- **IX. Resilient by Default**: PASS. Durable workflow binding removes a best-effort-only dependency from the task-detail path.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This spec covers the remaining functional gap called out by `docs/ManagedAgents/LiveLogs.md`.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The UI will stop using the Temporal run id as a pseudo-`taskRunId` fallback.

## Scope

### In Scope

- Persist `workflow_id` on managed run records
- Move task-run binding persistence to after successful record save
- Derive `taskRunId` in execution detail from the managed-run store when memo/search attributes do not already contain it
- Allow task owners to read observability routes for their own runs
- Update task-detail placeholder logic and auth error handling
- Add unit and integration coverage, including simulated long-running streams

### Out of Scope

- New observability transports
- Viewer virtualization or ANSI rendering polish
- New Mission Control control-surface features

## Structure Decision

- Update managed runtime contracts in `moonmind/schemas/agent_runtime_models.py`, `moonmind/workflows/temporal/runtime/launcher.py`, and `moonmind/workflows/temporal/runtime/store.py`.
- Update launch binding behavior in `moonmind/workflows/temporal/activity_runtime.py` and the managed agent workflow handoff in `moonmind/workflows/temporal/workflows/agent_run.py`.
- Update execution detail and observability routers in `api_service/api/routers/executions.py` and `api_service/api/routers/task_runs.py`.
- Update Mission Control task detail behavior in `frontend/src/entrypoints/task-detail.tsx`.
- Add focused backend, frontend, and integration tests; update `docs/ManagedAgents/LiveLogs.md` when implementation status changes.

## Verification Plan

### Automated Tests

1. Run `./tools/test_unit.sh --python-only --no-xdist tests/unit/services/temporal/runtime/test_store.py tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_runs.py`.
2. Run `./tools/test_unit.sh --python-only --no-xdist tests/integration/temporal/test_managed_runtime_live_logs.py`.
3. Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`.

### Manual Validation

1. Open Mission Control task detail for a real managed run and confirm the live-log placeholder transitions to the observability panels once launch completes.
2. Verify a non-admin owning user can load observability summary and logs for their own run.
3. Verify a running task without a binding shows the degraded binding-missing copy instead of the generic placeholder.
