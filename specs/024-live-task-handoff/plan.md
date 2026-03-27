# Implementation Plan: Live Task Handoff

**Branch**: `024-live-task-handoff` | **Date**: 2026-02-18 | **Spec**: `specs/024-live-task-handoff/spec.md`
**Input**: Feature specification from `/specs/024-live-task-handoff/spec.md`

## Summary

Implement live task handoff for queue task runs using tmate-backed worker sessions, task-run live-session/control persistence, API endpoints for session/control/message operations, and dashboard controls for enable/view/grant/revoke/pause/resume/takeover flows. The implementation must preserve queue task completion semantics when live-session setup fails.

## Technical Context

**Language/Version**: Python 3.11, JavaScript (dashboard)  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic schemas, existing agent queue service/repository stack, managed agent queue worker (`moonmind/agents/codex_worker`; all managed CLI runtimes, not Codex-only)  
**Storage**: PostgreSQL tables `task_run_live_sessions` + `task_run_control_events` linked to `agent_jobs`  
**Testing**: `./tools/test_unit.sh` with focused API/service/worker/dashboard/config coverage  
**Target Platform**: Linux containers in Docker Compose (`api`, `codex-worker`, `api-db`)  
**Project Type**: Backend API + worker daemon + dashboard frontend assets  
**Performance Goals**: Live-session status and operator controls reflected in queue detail view with existing dashboard polling cadence; worker pause control observable within heartbeat interval  
**Constraints**: Keep RO-first exposure model, preserve queue task execution when live session fails, maintain backward compatibility for existing queue API/event flows  
**Scale/Scope**: Queue task-run live handoff only (non-goal: full collaborative IDE)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` is placeholder-only and does not define ratified enforceable principles.
- Repository runtime guardrails apply: runtime production changes and validation tests are mandatory.

**Gate Status**: PASS WITH NOTE (no enforceable constitution text present).

## Project Structure

### Documentation (this feature)

```text
specs/024-live-task-handoff/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── live-task-handoff.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/task_runs.py
├── api/routers/task_dashboard_view_model.py
├── main.py
├── migrations/versions/202602180001_live_task_handoff.py
└── static/task_dashboard/dashboard.js

moonmind/
├── agents/codex_worker/worker.py
├── config/settings.py
├── schemas/agent_queue_models.py
└── workflows/agent_queue/
   ├── models.py
   ├── repositories.py
   └── service.py

runtime/
├── docker-compose.yaml
├── .env-template
├── api_service/Dockerfile
└── tools/start-worker.sh

tests/
├── unit/api/routers/test_task_runs.py
├── unit/api/routers/test_task_dashboard_view_model.py
├── unit/agents/codex_worker/test_worker.py
├── unit/config/test_settings.py
└── unit/workflows/test_skills_resolver.py
```

**Structure Decision**: Extend existing queue API/service/worker/dashboard modules in place with additive models, endpoints, and controls; avoid introducing new services.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
