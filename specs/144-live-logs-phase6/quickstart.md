# Quickstart: Live Logs Phase 6 Compatibility and Cleanup

## Goal

Verify rollout-aware viewer eligibility and mixed-payload compatibility for Live Logs.

## Steps

1. Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`.
2. Run `npm run ui:typecheck`.
3. Run `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`.
4. Run `SPECIFY_FEATURE=144-live-logs-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
5. Run `SPECIFY_FEATURE=144-live-logs-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.

## Manual Spot Checks

1. With rollout `codex_managed`, open a Codex managed run and confirm the timeline viewer appears.
2. With rollout `codex_managed`, open a non-Codex managed run and confirm the legacy line viewer appears.
3. Open a run where structured history is empty but merged logs are available and confirm merged compatibility text is shown.
4. Confirm session header state updates for both camelCase and snake_case live event payloads.
