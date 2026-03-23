# Implementation Plan: Phase 3 - Remove Queue Backend Code

**Branch**: `100-queue-removal-phase-3` | **Date**: 2026-03-21 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/100-queue-removal-phase-3/spec.md`

## Summary
Remove all legacy `agent_queue` and `orchestrator` Python code, API endpoints, configurations, SQL tables, and tests from the codebase, as Temporal has fully subsumed its role.

## User Review Required
No breaking operational changes. Dropping `agent_queue` related DB schemas will require generating an alembic migration, but the system relies entirely on Temporal so this will be safe.

## Proposed Changes

### API Layer
#### [DELETE] `api_service/api/routers/agent_queue.py`
Delete the entire queue router.

#### [MODIFY] `api_service/api/router_setup.py`
Remove the import and router include for `agent_queue.py`.

### Database Layer
#### [MODIFY] `api_service/db/models.py`
Delete `AgentJob`, `AgentJobEvent`, `AgentJobArtifact`, `AgentJobSkill` models and any `Enum` types only used by the queue (like `AgentJobStatus`, `AgentJobType`).

#### [NEW] `api_service/migrations/versions/<hash>_queue_removal.py`
Run `alembic revision --autogenerate -m "Remove legacy queue backend tables"` to generate the migration.

### Application Logic (Backend Modules)
#### [DELETE] `moonmind/workflows/agent_queue/` (Directory)
Delete the entire `agent_queue` package recursively.

#### [DELETE] `moonmind/workflows/orchestrator/` (Directory)
Delete the entire `orchestrator` package recursively.

#### [MODIFY] `moonmind/workflows/__init__.py`
Remove all `AgentJob`, `AgentQueue`, or `orchestrator` exports.

### Configuration
#### [MODIFY] `moonmind/config/settings.py` (or view models)
Remove references to `MOONMIND_QUEUE`, `defaultQueue`, and `queueEnv` where they are declared and used.

### Tests
#### [DELETE] `tests/unit/orchestrator_removal/` (Directory)
Drop this legacy directory entirely.

#### [MODIFY] `tests/` (Various)
Scrub `tests/unit/api/routers/test_agent_queue.py` and any tests mocking queue services in integration modules.

## Verification Plan
### Automated Tests
Execute `./tools/test_unit.sh` locally to ensure the test suite is green after purging the modules, ensuring no dangling imports surface.
Run `docker compose` to assert that the alembic migrations apply successfully over the existing database snapshot.
