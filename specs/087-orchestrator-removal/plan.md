# Implementation Plan: Remove mm-orchestrator

**Branch**: `087-orchestrator-removal` | **Date**: 2025-03-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/087-orchestrator-removal/spec.md`

## Summary

Remove the legacy `mm-orchestrator` worker stack: Docker services, FastAPI routes, SQLAlchemy models and Alembic tables, `moonmind.workflows.orchestrator` package, dependent schemas and task-compatibility glue, task dashboard UI paths, tests, CI workflow, and obsolete spec/OpenAPI trees. Ship one Alembic revision that drops orchestrator tables in FK-safe order.

## Technical Context

**Language/Version**: Python 3.11+ (Poetry), JavaScript (task dashboard)  
**Primary Dependencies**: FastAPI, SQLAlchemy, Alembic, Docker Compose  
**Storage**: PostgreSQL (orchestrator tables removed)  
**Testing**: `./tools/test_unit.sh`, targeted pytest under `tests/`  
**Target Platform**: Linux containers (Compose)  
**Project Type**: API service + Python `moonmind` library + static dashboard  
**Performance Goals**: N/A (deletion)  
**Constraints**: Constitution II — default `docker compose` path must remain valid without orchestrator; fail-fast if references remain  
**Scale/Scope**: Full removal including discovered dependents (`workflow_models`, `compatibility.py`, `dashboard.js`); `docker-compose.job.yaml` deleted with the orchestrator stack

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **II. One-Click Agent Deployment**: PASS — removing an optional service simplifies the default stack; Compose must still start core API/workers.
- **I. Orchestrate, Don't Recreate**: PASS — this removes a MoonMind-specific orchestrator *service*, not the platform’s agent-orchestration philosophy; Temporal/managed paths remain.

## Project Structure

### Documentation (this feature)

```text
specs/087-orchestrator-removal/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
docker-compose.yaml
docker-compose.test.yaml
api_service/
  api/routers/orchestrator.py      # remove
  api/main.py                    # unregister router
  db/models.py                   # remove Orchestrator* ORM
  migrations/versions/           # new drop migration
moonmind/
  workflows/orchestrator/        # remove package
  workflows/tasks/compatibility.py  # strip orchestrator branches
  schemas/workflow_models.py     # remove orchestrator Pydantic models
services/orchestrator/           # remove if only for mm-orchestrator image
tests/integration/orchestrator/  # remove
tests/unit/workflows/orchestrator/
tests/contract/test_orchestrator_api.py
.github/workflows/orchestrator-integration-tests.yml
```

**Structure Decision**: MoonMind monorepo layout above; implementation follows dependency order (API/UI → library → DB migration → compose → tests/docs).

## Complexity Tracking

> No constitution violations requiring justification.

## Phases

### Phase 0 — Complete

See `research.md`.

### Phase 1 — Complete

See `data-model.md`, `contracts/requirements-traceability.md`, `quickstart.md`.

### Phase 2

Execution via `tasks.md` (speckit-implement).
