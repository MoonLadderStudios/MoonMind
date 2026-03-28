# Tasks: live-logs-phase-2

**Feature Branch**: `111-live-logs-phase-2`
**Created**: 2026-03-28
**Aligned Plan**: `plan.md`

- `[ ]` Update `moonmind/schemas/agent_runtime_models.py` to add stdout/stderr artifact refs and last_log offsets.
- `[ ]` Update `moonmind/workflows/temporal/runtime/supervisor.py` and `store.py` to consume the new schema refs.
- `[ ]` Update `moonmind/proxy/routers/...` to expose endpoints `GET /api/task-runs/{id}/observability-summary`, `logs/stdout`, `logs/stderr`, etc.
- `[ ]` Write API unit tests.
- `[ ]` Mark completion in `docs/tmp/009-LiveLogsPlan.md`.
