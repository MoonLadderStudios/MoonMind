# Quickstart: Temporal Task Editing Entry Points

## Purpose

Validate the Phase 0/1 runtime slice for Temporal task editing entry points. This quickstart verifies contract scaffolding, runtime flag exposure, backend capability gating, and task-detail navigation behavior.

## Prerequisites

- Python and Node tooling available in the current repository workspace.
- Frontend dependencies installed through `./tools/test_unit.sh` or `npm ci --no-fund --no-audit`.
- No external provider credentials are required.

## Targeted Backend Validation

```bash
pytest tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/api/routers/test_executions.py -q
```

Expected result:

- Dashboard runtime config includes `features.temporalDashboard.temporalTaskEditing`.
- Execution detail exposes the task editing read contract fields.
- `canUpdateInputs` and `canRerun` respect workflow type, lifecycle state, and feature flag state.

## Targeted Frontend Validation

Managed agent workspaces may include colons in their absolute path, so invoke the local binary directly if `npm run ui:test` cannot resolve `vitest`.

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx
```

Expected result:

- Canonical route helpers produce `/tasks/new`, edit, and rerun targets.
- Edit is visible only for supported update-capable fixtures with the feature flag on.
- Rerun is visible only for supported rerun-capable fixtures with the feature flag on.
- Feature-disabled and unsupported fixtures omit invalid actions.

## Type Validation

```bash
./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
```

Expected result:

- Frontend route helpers and typed task editing contracts typecheck.

## Full Unit Gate

```bash
./tools/test_unit.sh
```

Expected result:

- Python unit tests and frontend Vitest tests pass through the project-standard runner.

## Manual Smoke Check

1. Enable the `temporalTaskEditing` runtime flag.
2. Open a supported active `MoonMind.Run` detail page with `actions.canUpdateInputs = true`.
3. Confirm Edit is visible and links to `/tasks/new?editExecutionId=<workflowId>`.
4. Open a supported terminal `MoonMind.Run` detail page with `actions.canRerun = true`.
5. Confirm Rerun is visible and links to `/tasks/new?rerunExecutionId=<workflowId>`.
6. Open an unsupported workflow type or disable the feature flag.
7. Confirm Edit and Rerun are omitted and no queue-era route is offered.

## Runtime Scope Confirmation

This feature is complete only when runtime code changes and validation tests are present. Spec-only or docs-only changes do not satisfy the quickstart.
