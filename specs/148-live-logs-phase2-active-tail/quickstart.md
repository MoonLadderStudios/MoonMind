# Quickstart: Live Logs Phase 2 Active Tail

## Focused TDD Run

1. Add failing tests in `tests/unit/api/routers/test_task_runs.py`:
   - active `/logs/merged` uses journal rows before final artifacts
   - active `/logs/merged` falls back to spool when the journal is absent or invalid
   - summary carries record-derived session snapshot and `observabilityEventsRef`
   - stream endpoint status behavior remains unchanged

2. Run the focused router tests:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py
```

3. Implement the router helpers and source preference in `api_service/api/routers/task_runs.py`.

4. Re-run:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py
```

## Manual Smoke Scenario

1. Start an active Codex managed-session run with `liveStreamCapable=true`.
2. Confirm `/api/task-runs/{id}/observability-summary` reports `supportsLiveStreaming=true` and includes `sessionSnapshot`.
3. Refresh the task detail page while the run is active.
4. Confirm Live Logs shows recent merged content before SSE appends new rows.
5. End the run and confirm summary reports `liveStreamStatus=ended` while merged history remains visible.
