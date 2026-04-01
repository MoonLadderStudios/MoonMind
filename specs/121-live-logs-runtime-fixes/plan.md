# Implementation Plan: live-logs-runtime-fixes

**Branch**: `120-live-logs-phase-4-affordances` | **Date**: 2026-03-31 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/121-live-logs-runtime-fixes/spec.md`

## Summary

Repair the backend/runtime contracts that the Phase 4 UI depends on by persisting real live-stream capability metadata, switching live-log sequencing to one run-global namespace, synthesizing merged log tails in emit order, and aligning docs plus runtime flag naming with the actual implementation.

## Technical Context

**Language/Version**: Python 3.12, TypeScript + React 19
**Primary Dependencies**: FastAPI, Pydantic, existing MoonMind managed-runtime observability stack, Vite/Vitest
**Testing**: `./tools/test_unit.sh --python-only --no-xdist ...` for backend coverage, `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` for the task detail entrypoint
**Project Type**: Managed runtime backend plus Mission Control view-model config and docs
**Constraints**: Preserve artifact-first behavior; do not add compatibility aliases beyond what is required for in-flight observability records; keep the transport cross-process

## Constitution Check

*GATE: Must pass before implementation and after design updates.*

- **I. Orchestrate, Don't Recreate**: PASS. The fixes keep observability MoonMind-owned and runtime-neutral.
- **II. One-Click Agent Deployment**: PASS. The work stays inside existing services and scripts.
- **VII. Powerful Runtime Configurability**: PASS. The observability panel remains runtime-configurable through a canonical feature flag.
- **VIII. Modular and Extensible Architecture**: PASS. Transport and merged-tail fixes stay inside runtime/API boundaries rather than leaking into the UI.
- **IX. Resilient by Default**: PASS. Global sequencing and truthful capability metadata make reconnect and fallback behavior deterministic.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This follow-on spec covers the backend fixes the frontend-only Phase 4 spec did not include.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The rollout flag naming is aligned to one canonical term rather than preserving parallel names in the docs.

## Scope

### In Scope

- Managed-run live-stream capability persistence
- Global sequence assignment for live-log chunks
- Chronological merged-tail synthesis from separate artifacts
- Dashboard feature-flag naming alignment
- Canonical and tmp live-logs doc corrections
- Boundary tests for API/router and runtime behavior

### Out of Scope

- Replacing the spool transport
- Adding new viewer libraries
- Phase 5 intervention work

## Structure Decision

- Update managed runtime launch/streaming code under `moonmind/workflows/temporal/runtime/`.
- Update merged-log API behavior in `api_service/api/routers/task_runs.py`.
- Update dashboard runtime config in `api_service/api/routers/task_dashboard_view_model.py` and the task detail entrypoint.
- Add/extend unit coverage in `tests/unit/api/routers/`, `tests/unit/observability/`, and runtime-focused tests where needed.
- Update `docs/ManagedAgents/LiveLogs.md` and `docs/tmp/009-LiveLogsPlan.md` to match the corrected implementation state and contracts.

## Verification Plan

### Automated Tests

1. Run `./tools/test_unit.sh --python-only --no-xdist tests/unit/api/routers/test_task_runs.py tests/unit/observability/test_transport.py`.
2. Run focused Python tests for any runtime sequencing coverage added under `tests/unit/workflows/temporal/runtime/` or equivalent.
3. Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`.

### Manual Validation

1. Inspect a fresh managed-run record and confirm `liveStreamCapable` is populated for active runs.
2. Confirm the dashboard config surfaces `logStreamingEnabled`.
3. Confirm the updated docs no longer overstate Phase 3 completion and describe the merged-tail/reconnect contracts accurately.
