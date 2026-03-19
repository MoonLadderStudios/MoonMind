# Implementation Plan: Workflow Scheduling

**Branch**: `086-workflow-scheduling` | **Date**: 2026-03-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/086-workflow-scheduling/spec.md`

## Summary

Add inline scheduling parameters to the task create endpoint (`POST /api/executions` and `POST /api/queue/jobs`). Support two modes: `schedule.mode=once` for deferred one-time execution via Temporal `start_delay`, and `schedule.mode=recurring` for cron-based recurring schedules delegating to the existing `RecurringTasksService`. Add a schedule panel to the Mission Control submit form with feature flag gating.

## Technical Context

**Language/Version**: Python 3.12+ (backend), vanilla JavaScript (frontend)
**Primary Dependencies**: FastAPI, Pydantic, temporalio SDK, SQLAlchemy, Alembic
**Storage**: PostgreSQL (existing `TemporalExecutionRecord` and `RecurringTaskDefinition` tables)
**Testing**: pytest with `./tools/test_unit.sh`
**Target Platform**: Linux server (Docker), web browser (dashboard)
**Project Type**: Web application (backend API + dashboard frontend)
**Performance Goals**: No additional latency on create endpoint when `schedule` is absent
**Constraints**: Zero regression on existing create flows, feature-flagged UI
**Scale/Scope**: ~8 files modified, ~2 new files, 1 DB migration

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
| --- | --- | --- |
| Orchestrate, Don't Recreate | ✅ Pass | Uses Temporal's native `start_delay` for deferred execution; `RecurringTasksService` is reused, not duplicated |
| One-Click Agent Deployment | ✅ Pass | No new services or containers; changes are within existing API service |
| Avoid Vendor Lock-In | ✅ Pass | `schedule` abstraction is MoonMind-owned; backend could theoretically swap scheduler |
| Spec-Driven Development | ✅ Pass | Implementation follows DOC-REQ contracts from `WorkflowSchedulingGuide.md` |
| Project Count Limit (≤3) | ✅ Pass | No new projects |

## Project Structure

### Documentation (this feature)

```text
specs/086-workflow-scheduling/
├── plan.md                                # This file
├── spec.md                                # Feature specification
├── research.md                            # Phase 0 research
├── data-model.md                          # Phase 1 data model
├── contracts/
│   ├── requirements-traceability.md       # DOC-REQ traceability
│   └── schedule-api.md                    # API contract for schedule object
├── checklists/
│   └── requirements.md                    # Spec quality checklist
└── tasks.md                               # Implementation tasks (from speckit-tasks)
```

### Source Code (repository root)

```text
# Backend: API service + schemas
moonmind/schemas/temporal_models.py          # Add ScheduleParameters model, extend CreateExecutionRequest
moonmind/schemas/agent_queue_models.py       # Extend CreateJobRequest with schedule field
moonmind/workflows/temporal/client.py        # Add start_delay to start_workflow()

api_service/api/routers/executions.py        # Route schedule.mode to deferred or recurring paths
api_service/db/models.py                     # Add scheduled_for column, SCHEDULED state
api_service/api/routers/task_dashboard_view_model.py  # Add submitScheduleEnabled feature flag

# Frontend: Dashboard
api_service/static/task_dashboard/dashboard.js  # Schedule panel on submit form, scheduled banner

# Database
api_service/migrations/versions/             # New Alembic migration for scheduled_for column

# Tests
tests/unit/api_service/api/routers/          # test_executions_schedule.py
tests/unit/moonmind/workflows/temporal/      # test_client_start_delay.py
```

**Structure Decision**: Web application (backend API + frontend dashboard). All changes in existing project directories — no new top-level directories.

## Complexity Tracking

No constitution violations — table intentionally empty.
