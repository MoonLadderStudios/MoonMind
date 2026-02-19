# Implementation Plan: Agent Queue MVP (Milestone 1)

**Branch**: `009-agent-queue-mvp` | **Date**: 2026-02-13 | **Spec**: `specs/009-agent-queue-mvp/spec.md`
**Input**: Feature specification from `/specs/009-agent-queue-mvp/spec.md`

## Summary

Implement Milestone 1 from `docs/TaskQueueSystem.md` by adding a first-class `agent_jobs` queue model in Postgres, repository/service queue lifecycle operations, and authenticated REST APIs for enqueue/claim/heartbeat/complete/fail/get/list. Delivery includes unit coverage for state transitions and concurrent claims using SKIP LOCKED semantics.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, SQLAlchemy ORM, Alembic, Pydantic, PostgreSQL, pytest  
**Storage**: PostgreSQL tables managed via Alembic migrations  
**Testing**: Unit tests executed with `./tools/test_unit.sh`  
**Target Platform**: Linux containers and local dev shells running MoonMind API service  
**Project Type**: Backend API + persistence layer changes  
**Performance Goals**: Claim endpoint remains deterministic under concurrent claim attempts and does not double-assign a queued job  
**Constraints**: Runtime code changes are required (no docs-only delivery); preserve existing auth and repository conventions; keep milestone scope limited to queue MVP  
**Scale/Scope**: One new queue table, one migration, one router, one schema module, one queue workflow module, and associated unit tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` currently contains placeholder headings without enforceable MUST/SHOULD rules.
- No explicit constitutional constraints can be evaluated beyond standard project instructions.

**Gate Status**: PASS WITH NOTE. Proceeding under repository guidance and AGENTS instructions.

## Project Structure

### Documentation (this feature)

```text
specs/009-agent-queue-mvp/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── agent-queue.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   └── agent_queue.py                     # New queue REST router
├── main.py                                # Register queue router
└── migrations/versions/
    └── 202602130001_agent_queue_mvp.py    # New queue migration

moonmind/
├── schemas/
│   └── agent_queue_models.py              # Request/response + status models
└── workflows/
    └── agent_queue/
        ├── __init__.py
        ├── models.py                      # ORM model for agent_jobs
        ├── repositories.py                # DB transition methods
        └── service.py                     # Validation + orchestration rules

tests/
└── unit/
    ├── api/routers/test_agent_queue.py
    └── workflows/agent_queue/test_repositories.py
```

**Structure Decision**: Use existing MoonMind backend layering (schemas -> repository/service -> FastAPI router) and colocate the new queue workflow under `moonmind/workflows/agent_queue`.

## Phase 0: Research Plan

1. Confirm preferred place for new ORM model and migration patterns by reviewing existing workflow tables and repositories.
2. Define status transition matrix and ownership rules for claim/heartbeat/complete/fail.
3. Define deterministic claim ordering and SKIP LOCKED query strategy with SQLAlchemy.
4. Define API contracts and error responses aligned with existing routers.

## Phase 1: Design Outputs

- `research.md`: recorded decisions and alternatives for schema, claim transactions, service boundaries, and tests.
- `data-model.md`: queue entity fields and transition rules.
- `contracts/agent-queue.openapi.yaml`: REST contract for Milestone 1 endpoints.
- `contracts/requirements-traceability.md`: DOC-REQ to FR mapping with implementation surface and validation strategy.
- `quickstart.md`: local verification flow (migration + API tests + unit tests).

## Post-Design Constitution Re-check

- No new constitution directives emerged from design artifacts.
- Design keeps runtime implementation and testing in scope as required.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
