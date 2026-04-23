# Quickstart: Codex Managed Session Plane Phase 9

## Focused Verification

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py
SPECIFY_FEATURE=135-codex-managed-session-plane-phase9 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
SPECIFY_FEATURE=135-codex-managed-session-plane-phase9 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

## Full Verification

```bash
./tools/test_unit.sh
```

## Example API Call

```bash
curl -s \
  "http://localhost:8000/api/task-runs/<task_run_id>/artifact-sessions/<session_id>"
```

Expected result:

- returns the latest durable `session_epoch`
- includes grouped runtime and continuity/control artifacts
- includes the latest summary/checkpoint/control refs needed for a continuity panel
- works even when the live managed-session container no longer exists
