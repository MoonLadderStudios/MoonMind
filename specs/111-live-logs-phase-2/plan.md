# Implementation Plan: live-logs-phase-2

**Feature Branch**: `111-live-logs-phase-2`
**Created**: 2026-03-28
**Aligned Spec**: `spec.md`

## Proposed Changes

### [agent_runtime_models.py]
#### [MODIFY] `moonmind/schemas/agent_runtime_models.py`
Add `stdout_artifact_ref: str | None`, `stderr_artifact_ref: str | None`, `last_log_offset: int | None`, `last_log_at: datetime | None` to `ManagedRunRecord`.
Deprecate or replace `log_artifact_ref`.

### [supervisor.py & log_streamer.py]
#### [MODIFY] `moonmind/workflows/temporal/runtime/supervisor.py`
Update `log_refs["stdout"]` and `log_refs["stderr"]` assignments to write to the new properties in the `ManagedRunRecord`. Update `.update_status` signatures to support the new `stdout_artifact_ref`/`stderr_artifact_ref` fields.

### [API Routers]
#### [NEW] `moonmind/proxy/routers/runs_observability.py` (or similar)
Add endpoints for:
- `GET /api/task-runs/{id}/observability-summary`
- `GET /api/task-runs/{id}/logs/stdout`
- `GET /api/task-runs/{id}/logs/stderr`
- `GET /api/task-runs/{id}/logs/merged`
- `GET /api/task-runs/{id}/diagnostics`

Or add them to the existing `runs.py` or `activity_router.py`.

#### [MODIFY] `docs/tmp/009-LiveLogsPlan.md`
Mark tasks as completed.
