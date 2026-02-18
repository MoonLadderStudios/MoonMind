# Implementation Plan: Queue Live Logs + SSE

**Branch**: `023-queue-live-sse` | **Date**: 2026-02-18 | **Spec**: `specs/023-queue-live-sse/spec.md`  
**Input**: Feature specification from `specs/023-queue-live-sse/spec.md`

## Summary

Add real-time queue output by streaming worker command stdout/stderr into `agent_job_events` as batched/throttled log events, expose an SSE endpoint backed by the same event listing service, and upgrade queue detail UI with a Live Output panel + filters/follow/copy controls while retaining existing polling flows.

## Technical Context

**Language/Version**: Python 3.11 backend, vanilla JS dashboard frontend  
**Primary Dependencies**: FastAPI, Starlette `StreamingResponse`, existing AgentQueueService/Repository stack  
**Storage**: PostgreSQL `agent_job_events` append-only events table  
**Testing**: `./tools/test_unit.sh` (pytest unit suite)  
**Target Platform**: Docker Compose MoonMind services (API + queue workers)  
**Project Type**: Web application (backend API + static dashboard UI)  
**Performance Goals**: Near real-time UI updates without unbounded event-write amplification  
**Constraints**: Keep existing event schema/API compatibility; redact secrets; no destructive migrations  
**Scale/Scope**: Queue task detail realtime visibility for running jobs and operators

## Constitution Check

- Runtime implementation + validation tests included.
- Existing contracts preserved (`GET /events` stays intact); SSE is additive.
- No credential leakage in events/logs (reuse existing redaction).

## Project Structure

### Documentation (this feature)

```text
specs/023-queue-live-sse/
├── spec.md
├── plan.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/api/routers/agent_queue.py
api_service/api/routers/task_dashboard_view_model.py
api_service/static/task_dashboard/dashboard.js
api_service/static/task_dashboard/dashboard.css
moonmind/agents/codex_worker/worker.py
moonmind/agents/codex_worker/handlers.py
tests/unit/api/routers/test_agent_queue.py
tests/unit/api/routers/test_task_dashboard_view_model.py
tests/unit/agents/codex_worker/test_handlers.py
tests/unit/agents/codex_worker/test_worker.py
```

**Structure Decision**: Extend current queue/router/worker/dashboard modules in place; no new service boundary required.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| None | N/A | N/A |
