# Requirements Traceability: Task Dependencies Phase 1

**Branch**: `101-task-dependencies-phase1`
**Date**: 2026-03-22

| DOC-REQ | FR(s) | Implementation Surface | Validation Strategy |
|---------|-------|----------------------|---------------------|
| DOC-REQ-001 | FR (implicit) | `run.py` lifecycle ordering (Phase 2 wires it; Phase 1 makes it possible) | Phase 2 unit tests validate lifecycle ordering |
| DOC-REQ-002 | FR-001 | `api_service/db/models.py` — `MoonMindWorkflowState` enum | Unit test: `MoonMindWorkflowState.WAITING_ON_DEPENDENCIES.value == "waiting_on_dependencies"` |
| DOC-REQ-003 | FR-002 | `api_service/migrations/versions/<new>.py` | `alembic upgrade head` succeeds; inserting `state = 'waiting_on_dependencies'` works |
| DOC-REQ-004 | FR-003 | `moonmind/workflows/temporal/workflows/run.py` | Unit test: `STATE_WAITING_ON_DEPENDENCIES == "waiting_on_dependencies"` |
| DOC-REQ-005 | FR-004 | `api_service/core/sync.py` | Unit test: projection sync accepts `mm_state = "waiting_on_dependencies"` without warning |
| DOC-REQ-006 | FR-005, FR-006 | `api_service/api/routers/executions.py`, `moonmind/workflows/tasks/compatibility.py` | Unit test: status maps return `"waiting"` for the new state |
| DOC-REQ-007 | FR-007 | All touchpoints | Verified by FR-001 test (enum string value check) |
