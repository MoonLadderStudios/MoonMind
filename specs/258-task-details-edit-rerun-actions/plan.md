# Implementation Plan: Task Details Edit and Rerun Actions

**Branch**: `add-the-edit-and-rerun-buttons-as-descri-5068be8c`
**Date**: 2026-04-25
**Spec**: `specs/258-task-details-edit-rerun-actions/spec.md`
**Input**: Single-story feature specification from `specs/258-task-details-edit-rerun-actions/spec.md`

## Summary

Add an explicit `canEditForRerun` action capability for terminal task executions and update the React Task Details action bar to render **Edit task** and **Rerun** from the backend capability object. Repo gap analysis found rerun support already existed, but edit-for-rerun visibility was missing from the API contract and UI action bar. The plan requires API unit coverage, React route/rendering coverage, generated OpenAPI type updates, and final unit/type verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| SCN-001 | implemented_verified | `tests/unit/api/routers/test_executions.py`, `frontend/src/entrypoints/task-detail.test.tsx` | no new implementation | final verify |
| SCN-002 | implemented_verified | existing running edit test updated in `frontend/src/entrypoints/task-detail.test.tsx`; API assertion in `tests/unit/api/routers/test_executions.py` | no new implementation | final verify |
| SCN-003 | implemented_verified | missing snapshot assertions in `tests/unit/api/routers/test_executions.py` | no new implementation | final verify |
| SCN-004 | implemented_verified | unsupported workflow assertions in `tests/unit/api/routers/test_executions.py` | no new implementation | final verify |
| REQ-001 | implemented_verified | `moonmind/schemas/temporal_models.py`, `frontend/src/generated/openapi.ts` | no new implementation | API unit + OpenAPI generation |
| REQ-002 | implemented_verified | `api_service/api/routers/executions.py`, `tests/unit/api/routers/test_executions.py` | no new implementation | API unit |
| REQ-003 | implemented_verified | `tests/unit/api/routers/test_executions.py` | no new implementation | API unit |
| REQ-004 | implemented_verified | `frontend/src/entrypoints/task-detail.tsx`, `frontend/src/entrypoints/task-detail.test.tsx` | no new implementation | frontend unit |
| REQ-005 | implemented_verified | `frontend/src/lib/temporalTaskEditing.ts`, `frontend/src/entrypoints/task-detail.test.tsx`, `frontend/src/entrypoints/task-create.test.tsx` | no new implementation | frontend unit |
| REQ-006 | implemented_verified | `frontend/src/entrypoints/task-detail.tsx`, `frontend/src/entrypoints/task-detail.test.tsx` | no new implementation | frontend unit |
| REQ-007 | implemented_verified | failed-task additive action test in `frontend/src/entrypoints/task-detail.test.tsx` | no new implementation | frontend unit |

## Technical Context

- Language/version: Python 3.12; TypeScript/React.
- Primary dependencies: Pydantic v2, FastAPI execution router, React, TanStack Query, Zod, Vitest.
- Storage: no new persistent storage; existing Temporal execution records and task input snapshot refs only.
- Unit testing tool: `./tools/test_unit.sh` for Python unit tests; Vitest for frontend unit tests through the same runner or direct local binary.
- Integration testing tool: `./tools/test_integration.sh` for hermetic integration_ci coverage when execution-router integration behavior changes; not required for this UI/API capability-only story because no compose-backed seam changed.
- Target platform: Mission Control dashboard and executions API.
- Project type: FastAPI backend plus React frontend.
- Performance goals: no additional network round trips; action visibility remains derived from the existing detail payload.
- Constraints: preserve backend capabilities as source of truth; keep task editing feature flag and original snapshot gates; do not mutate terminal failed executions in place.
- Scale/scope: one task detail action contract and corresponding route behavior.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS - keeps behavior in dashboard/API orchestration.
- II One-Click Agent Deployment: PASS - no deployment prerequisite changes.
- III Avoid Vendor Lock-In: PASS - no provider-specific coupling.
- IV Own Your Data: PASS - uses existing task input snapshot refs.
- V Skills Are First-Class: PASS - no skill runtime changes.
- VI Replaceable Scaffolding: PASS - bounded UI/API contract change with tests.
- VII Runtime Configurability: PASS - respects existing task editing feature flag.
- VIII Modular Architecture: PASS - capability calculation remains at API boundary.
- IX Resilient by Default: PASS - terminal actions do not mutate failed executions in place.
- X Continuous Improvement: PASS - verification evidence is recorded.
- XI Spec-Driven Development: PASS - spec, plan, and tasks are present.
- XII Canonical Documentation: PASS - canonical docs remain desired-state only.
- XIII Pre-Release Compatibility: PASS - internal contract updated directly without aliases.

## Design

- Extend `ExecutionActionCapabilityModel` with `can_edit_for_rerun`.
- Add `can_edit_for_rerun` to terminal rerunnable states in `_build_action_capabilities`.
- Apply the same workflow type, feature flag, and original snapshot gates to `can_edit_for_rerun` as `can_rerun`.
- Update the Task Details action schema and rendering logic to use `canEditForRerun`.
- Add an edit-for-rerun href helper and route resolution support for `?rerunExecutionId=:id&mode=edit`.

## Test Strategy

### Unit

- API: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`
- Frontend: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- Type contract: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- OpenAPI type generation: `npm run api:types`

### Integration

- No new hermetic integration test is planned because the change does not alter Temporal workflow execution, persisted payloads, or compose-backed services.
- If the execution detail endpoint contract is later consumed through a browser E2E fixture, add an integration_ci or E2E assertion that a failed execution fixture renders **Edit task** and **Rerun** together.

## Project Structure

```text
api_service/api/routers/executions.py
moonmind/schemas/temporal_models.py
frontend/src/lib/temporalTaskEditing.ts
frontend/src/entrypoints/task-detail.tsx
frontend/src/entrypoints/task-create.tsx
frontend/src/generated/openapi.ts
tests/unit/api/routers/test_executions.py
frontend/src/entrypoints/task-detail.test.tsx
frontend/src/entrypoints/task-create.test.tsx
```

## Complexity Tracking

No constitution violations or additional complexity exceptions.
