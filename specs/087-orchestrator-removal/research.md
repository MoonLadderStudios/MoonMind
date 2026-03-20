# Research: Orchestrator Removal

**Feature**: 087-orchestrator-removal  
**Date**: 2025-03-19

## Decision: Scope of “orchestrator” runtime in task dashboard

**Decision**: Remove orchestrator as a first-class runtime from `api_service/static/task_dashboard/dashboard.js` (routes, submit forms, list/detail fetches, draft keys) in line with API removal.

**Rationale**: Without `/orchestrator/*` endpoints, the UI would be broken if left intact.

**Alternatives considered**: Feature-flag hiding — rejected; plan calls for full removal.

## Decision: `moonmind/workflows/tasks/compatibility.py`

**Decision**: Remove code paths that load `OrchestratorRun` for unified task views; restrict compatibility mapping to remaining run sources (queue, Temporal, etc.).

**Rationale**: Models and tables are deleted; imports must not reference `OrchestratorRun`.

**Alternatives considered**: Stub models — rejected; violates DOC-REQ-006.

## Decision: `docker-compose.job.yaml`

**Decision**: Remove the file entirely (it only existed to build/run `services/orchestrator`).

**Rationale**: With the orchestrator image and service gone, the job overlay is obsolete.

## Decision: Alembic

**Decision**: Add a new revision that drops child tables (artifacts, plan steps, task states, etc.) before `orchestrator_runs`, matching actual `api_service/db/models.py` FK graph.

**Rationale**: Safe ordering prevents migration failures on populated DBs.

## Decision: Spec directories `005` and `050`

**Decision**: Delete `specs/005-orchestrator-architecture` and `specs/050-orchestrator-task-runtime` per source plan.

**Rationale**: Explicit user contract; avoids stale OpenAPI references.
