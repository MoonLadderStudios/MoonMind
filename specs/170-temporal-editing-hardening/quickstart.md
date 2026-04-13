# Quickstart: Temporal Editing Hardening

## Purpose

Validate the Phase 5 hardening implementation for Temporal task editing in runtime mode.

## Prerequisites

- Python and Node dependencies available in the repository workspace.
- No external provider credentials are required for the required unit validation path.

## Targeted Backend Validation

```bash
pytest tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_dashboard_view_model.py -q
```

Expected coverage:

- `UpdateInputs` and `RequestRerun` submit attempts/results emit bounded server telemetry.
- Backend validation failures emit bounded failure telemetry.
- Dashboard runtime config exposes `temporalTaskEditing`.
- Capability flags remain explicit for supported and unsupported execution states.

## Targeted Frontend Validation

Prepare JavaScript dependencies if needed:

```bash
npm ci --no-fund --no-audit
```

Run focused task editing coverage:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts \
  frontend/src/entrypoints/task-detail.test.tsx \
  frontend/src/entrypoints/task-create.test.tsx
```

Expected coverage:

- Detail-page Edit and Rerun clicks emit bounded client telemetry.
- Draft reconstruction success and failure are observable.
- Active edit submits `UpdateInputs` and returns to a Temporal detail context.
- Terminal rerun submits `RequestRerun` and returns to a Temporal detail context.
- Unsupported workflow, missing capability, missing/malformed artifact, stale state, validation error, artifact failure, and rerun-over-edit precedence are covered.
- Queue-era primary routes or params are not generated.

## Frontend Type and Lint Checks

```bash
./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
./node_modules/.bin/eslint -c frontend/eslint.config.mjs \
  frontend/src/lib/temporalTaskEditing.ts \
  frontend/src/entrypoints/task-detail.tsx \
  frontend/src/entrypoints/task-create.tsx \
  frontend/src/entrypoints/task-detail.test.tsx \
  frontend/src/entrypoints/task-create.test.tsx
```

## Full Required Unit Validation

```bash
./tools/test_unit.sh
```

Expected result:

- Required Python unit suite passes.
- Required frontend unit suite passes.
- Existing warnings may remain if unrelated to this feature, but failures must be fixed before implementation completion.

## Manual Runtime Smoke Check

1. Enable Temporal task editing in local runtime configuration using the existing `TEMPORAL_TASK_EDITING_ENABLED` flag.
2. Open a supported active `MoonMind.Run` execution detail page.
3. Confirm **Edit** appears only when update capability is available.
4. Open edit mode and confirm `/tasks/new?editExecutionId=<workflowId>` loads a reconstructed draft.
5. Submit a supported edit and confirm the operator returns to `/tasks/<workflowId>?source=temporal`.
6. Open a supported terminal `MoonMind.Run` execution detail page.
7. Confirm **Rerun** appears only when rerun capability is available.
8. Open rerun mode and confirm `/tasks/new?rerunExecutionId=<workflowId>` loads a reconstructed draft.
9. Submit a supported rerun and confirm the operator returns to a Temporal detail context.
10. Confirm primary UI copy does not mention queue resubmit, `editJobId`, or `/tasks/queue/new`.

## Rollout Readiness Check

- Local development flag path verified.
- Staging flag path verified.
- Internal dogfood cohort identified.
- Limited production cohort expansion gated on low edit/rerun failure rate, no queue fallback usage, and acceptable support feedback.
- All-operator expansion approved only after dogfood and limited cohort results are acceptable.
