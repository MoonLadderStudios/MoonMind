# Implementation Plan: Agent Queue Hardening and Quality (Milestone 5)

**Branch**: `013-queue-hardening-quality` | **Date**: 2026-02-14 | **Spec**: `specs/013-queue-hardening-quality/spec.md`
**Input**: Feature specification from `/specs/013-queue-hardening-quality/spec.md`

## Summary

Implement Milestone 5 hardening for the queue runtime by adding worker credential enforcement (token/OIDC-aware worker identity), per-worker repository/job/capability policy checks during claim, retry backoff with dead-letter terminal state, and append-only job event APIs that support incremental polling for streaming-ish logs.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, SQLAlchemy ORM/async session, Pydantic v2 schemas, existing agent queue service/repository modules  
**Storage**: PostgreSQL queue tables (plus SQLite in unit tests), filesystem artifact root for uploaded files  
**Testing**: pytest unit tests via `./tools/test_unit.sh`  
**Target Platform**: MoonMind API + worker runtime (Linux containers/local shell)  
**Project Type**: Backend API/service/repository hardening + worker client updates  
**Performance Goals**: Keep claim endpoint single-query selection semantics with deterministic ordering and bounded retry backoff computation  
**Constraints**: Preserve Milestone 1-4 API compatibility while adding new optional fields/endpoints and security checks  
**Scale/Scope**: Queue lifecycle/state model updates, auth/policy enforcement, event storage APIs, worker daemon integration, and focused unit test coverage

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` remains placeholder-only with no concrete MUST/SHOULD policy language.
- No additional constitution blockers were identified beyond repository instructions and AGENTS constraints.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/013-queue-hardening-quality/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── queue-hardening-api.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/agent_queue.py
├── db/models.py
└── migrations/versions/202602140001_agent_queue_hardening.py

moonmind/
├── agents/codex_worker/worker.py
├── schemas/agent_queue_models.py
└── workflows/agent_queue/
    ├── models.py
    ├── repositories.py
    └── service.py

tests/
└── unit/
    ├── agents/codex_worker/test_worker.py
    ├── api/routers/test_agent_queue.py
    └── workflows/agent_queue/
        ├── test_repositories.py
        └── test_service_hardening.py
```

**Structure Decision**: Extend existing queue runtime modules in-place and keep all hardening behavior centralized in `AgentQueueService`/`AgentQueueRepository`, with thin FastAPI mapping and worker-client adoption.

## Phase 0: Research Plan

1. Select worker auth enforcement strategy that supports dedicated worker tokens while allowing OIDC/JWT principals.
2. Define deterministic policy merge rules for claim filtering (requested `allowedTypes` + token allowlists + capability matching).
3. Define retry backoff algorithm and dead-letter transition semantics with minimal schema changes.
4. Define event API shape for append + incremental polling and map worker progress updates to event entries.

## Phase 1: Design Outputs

- `research.md`: records selected strategy and alternatives for auth, claim filtering, events, retries.
- `data-model.md`: documents worker token, retry scheduling, and event entities/state transitions.
- `contracts/queue-hardening-api.md`: request/response shapes for token-aware worker operations and event polling.
- `contracts/requirements-traceability.md`: DOC-REQ to FR, implementation surfaces, and validation strategy mapping.
- `quickstart.md`: verification flow for token creation/usage, restricted claims, retries, and event polling.

## Post-Design Constitution Re-check

- Runtime code changes and validation tasks are explicitly in scope.
- No constitution violations were identified due placeholder constitution content.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
