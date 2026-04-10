# Quickstart: Live Logs History Events

## Focused verification

Run the Phase 3 router test slice:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py
```

Run the Spec Kit scope checks:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

## Expected behavior

1. `/api/task-runs/{id}/observability/events` returns canonical structured history and supports `since`, `limit`, stream filters, and kind filters.
2. Historical retrieval prefers the durable observability journal and degrades through spool or artifact-backed synthesis when necessary.
3. `/api/task-runs/{id}/observability-summary` exposes the latest available session snapshot and truthful live-stream status.
4. `/api/task-runs/{id}/logs/stream` continues to emit canonical `RunObservabilityEvent` payloads compatible with the historical route.
