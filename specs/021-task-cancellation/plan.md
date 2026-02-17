# Implementation Plan: Agent Queue Task Cancellation

**Branch**: `021-task-cancellation` | **Date**: 2026-02-17 | **Spec**: `specs/021-task-cancellation/spec.md`
**Input**: Feature specification from `/specs/021-task-cancellation/spec.md`

## Summary

Implement cooperative queue task cancellation across queue repository/service/router, MCP queue tooling, worker heartbeat/execution flow, and dashboard queue detail UI. The design follows `docs/TaskCancellation.md`: queued jobs cancel immediately; running jobs record cancellation request metadata and transition to `cancelled` only via worker acknowledgement.

## Technical Context

**Language/Version**: Python 3.11, JavaScript (existing dashboard)  
**Primary Dependencies**: FastAPI router layer, SQLAlchemy async repository, Pydantic schemas, MCP tool registry, asyncio worker runtime  
**Storage**: Existing `agent_jobs` and `agent_job_events` tables with additive cancellation metadata columns  
**Testing**: `./tools/test_unit.sh` plus focused queue/API/MCP/worker/dashboard unit coverage  
**Target Platform**: Linux containers in docker compose (api + worker)  
**Project Type**: Backend service + worker daemon + dashboard frontend assets  
**Performance Goals**: Cancellation request observability within heartbeat cadence (max 5 seconds heartbeat interval for cancellation detection)  
**Constraints**: Preserve running-job ownership rules; maintain idempotent endpoint semantics; do not requeue cancellation-requested jobs; keep dashboard behavior compatible with existing queue routes  
**Scale/Scope**: Queue cancellation behavior only (not orchestrator run cancellation)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` is still placeholder-only and contains no enforceable principles.
- Repository-level runtime guardrails apply: include production runtime changes and validation tests.

**Gate Status**: PASS WITH NOTE (no ratified constitution constraints yet).

## Project Structure

### Documentation (this feature)

```text
specs/021-task-cancellation/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── queue-cancellation.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/agent_queue.py
├── api/routers/task_dashboard_view_model.py
├── migrations/versions/*.py
└── static/task_dashboard/dashboard.js

moonmind/
├── schemas/agent_queue_models.py
├── mcp/tool_registry.py
├── workflows/agent_queue/models.py
├── workflows/agent_queue/repositories.py
├── workflows/agent_queue/service.py
└── agents/codex_worker/
   ├── worker.py
   └── handlers.py

tests/
├── unit/workflows/agent_queue/*.py
├── unit/api/routers/test_agent_queue.py
├── unit/mcp/test_tool_registry.py
├── unit/agents/codex_worker/test_worker.py
└── unit/api/routers/test_task_dashboard_view_model.py
```

**Structure Decision**: Extend existing queue subsystem in place with additive fields/endpoints and cooperative worker cancellation checks; avoid introducing new services or queues.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
