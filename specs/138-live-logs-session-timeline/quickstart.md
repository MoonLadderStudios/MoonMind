# Quickstart: Live Logs Session Timeline

1. Run the focused Phase 0 and Phase 1 verification:

```bash
./tools/test_unit.sh tests/unit/services/temporal/runtime/test_log_streamer.py tests/unit/services/temporal/runtime/test_store.py tests/unit/services/temporal/runtime/test_supervisor_live_output.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/api/routers/test_task_runs.py
```

2. Validate Spec Kit runtime scope:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

3. Run the full unit suite before finalizing:

```bash
./tools/test_unit.sh
```

4. Manual smoke check:

```text
Inspect the task dashboard boot payload and confirm it includes liveLogsSessionTimelineEnabled and liveLogsSessionTimelineRollout.
Execute or simulate one managed run with mixed stdout/stderr/system/session rows and confirm the managed-run record points to observability.events.jsonl.
Load /api/task-runs/{taskRunId}/observability-summary and /api/task-runs/{taskRunId}/observability/events for the completed run and confirm they surface the durable session snapshot and structured timeline history.
```
