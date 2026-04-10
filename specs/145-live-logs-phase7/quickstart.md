# Quickstart: Live Logs Phase 7 Hardening and Rollback

## Prerequisites

- Repo dependencies installed
- Current branch: `145-live-logs-phase7`

## Verification commands

```bash
npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx
./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py
SPECIFY_FEATURE=145-live-logs-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
SPECIFY_FEATURE=145-live-logs-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

## Runtime checks

1. Set `FEATURE_FLAGS__LIVE_LOGS_STRUCTURED_HISTORY_ENABLED=false`.
2. Open a task detail page and expand `Live Logs`.
3. Confirm the browser loads merged history without calling `/observability/events`.
4. Re-enable the flag and confirm `/observability/events` is used again.
5. Verify summary/history/stream route tests assert the expected metrics behavior.
