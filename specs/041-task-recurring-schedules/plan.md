# Implementation Plan: Task Recurring Schedules System

**Branch**: `041-task-recurring-schedules` | **Date**: 2026-02-24 | **Spec**: `specs/041-task-recurring-schedules/spec.md`  
**Input**: Feature specification from `/specs/041-task-recurring-schedules/spec.md`

## Summary

Deliver the recurring scheduling system described in `docs/TaskRecurringSchedulesSystem.md` as production runtime behavior, not docs-only output. The implementation extends existing MoonMind queue and manifest pathways with DB-backed recurring definitions, run-history persistence, scheduler daemon dispatch loops, dashboard management surfaces, and automated coverage for cron/policy/idempotency/API/dashboard contracts.

## Technical Context

**Language/Version**: Python 3.11 for backend runtime, plus vanilla JavaScript in `api_service/static/task_dashboard/dashboard.js`  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Alembic migrations, Pydantic v2 models, existing Agent Queue service/repository, manifest service integration  
**Storage**: PostgreSQL as source of truth (`recurring_task_definitions`, `recurring_task_runs`), SQLite in unit/integration-style tests  
**Testing**: `./tools/test_unit.sh` for unit/contract suites; `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` for orchestrator integration validation  
**Target Platform**: Docker Compose deployment (`api`, `scheduler`, queue workers) and Tasks Dashboard routes under `/tasks/schedules*`  
**Project Type**: Web API + dashboard frontend + background scheduler daemon  
**Performance Goals**: Meet SC-003 dispatch latency target (due occurrences dispatched within one poll interval plus jitter under normal load) while preserving one logical dispatch artifact per occurrence  
**Constraints**: Minute-level cron only, timezone/DST correctness, no raw secret material in schedule definitions, HA-safe idempotent dispatch, effectively-once enqueue semantics (not exactly-once worker execution), deferred optional manifest YAML schedule import for v1, runtime-mode delivery with production code and tests  
**Scale/Scope**: Schedule CRUD + run history + scheduler ticks for `queue_task`, `queue_task_template`, `manifest_run`, and `housekeeping` targets with policy controls (overlap/catchup/misfire/jitter/backfill)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` is still an unresolved template (`[PRINCIPLE_*]` placeholders), so no enforceable constitutional constraints can be derived from that file.
- Repository-level quality gates still apply:
  - Runtime mode alignment: this feature includes production runtime code surfaces and avoids docs-only substitution.
  - Validation gate: required automated coverage runs through `./tools/test_unit.sh` (per repository test policy).
  - Compatibility guardrails: no hidden compatibility transforms for runtime-selection/billing semantics.

**Gate Status**: PASS WITH NOTE (constitution template unresolved; project-specific gates applied explicitly).

## Project Structure

### Documentation (this feature)

```text
specs/041-task-recurring-schedules/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── recurring-tasks.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md                # Created later by /speckit.tasks
```

### Source Code (repository root)

```text
api_service/
├── api/routers/recurring_tasks.py
├── api/routers/task_dashboard.py
├── api/routers/task_dashboard_view_model.py
├── db/models.py
├── migrations/versions/202602240001_recurring_task_schedules.py
├── services/recurring_tasks_service.py
└── static/task_dashboard/dashboard.js

moonmind/
├── workflows/recurring_tasks/cron.py
├── workflows/recurring_tasks/scheduler.py
└── workflows/agent_queue/job_types.py

docker-compose.yaml
.env-template

tests/
├── unit/api/routers/test_recurring_tasks.py
├── unit/api/routers/test_task_dashboard.py
├── unit/api/routers/test_task_dashboard_view_model.py
├── unit/services/test_recurring_tasks_service.py
└── unit/workflows/recurring_tasks/test_cron.py
```

**Structure Decision**: Extend existing recurring scheduling modules and dashboard surfaces already present in the repository, then harden remaining FR coverage gaps (security validation, contract completeness, concurrency/idempotency validation breadth, and UI/API behavior parity).

## Phase 0 – Research Summary

See `specs/041-task-recurring-schedules/research.md` for resolved decisions. Key outcomes:

1. Keep DB-backed two-stage scheduler architecture (`schedule -> runs`, `runs -> jobs`) with short transactions and `SKIP LOCKED` behavior.
2. Standardize idempotency around run identity (`definition_id + scheduled_for` and `run_id` recurrence metadata) with reconciliation before re-enqueue.
3. Preserve minute-level cron scope and timezone correctness using current parser + `zoneinfo` with DST coverage.
4. Align explicitly to **runtime orchestration mode** for this feature: production code + validation tests are mandatory deliverables.
5. Keep runtime semantics explicit: effectively-once enqueue per occurrence is required, while manifest YAML schedule import remains deferred optional scope for v1.

## Phase 1 – Design Outputs

- **Data Model**: `data-model.md` documents recurring definition/run entities, target/policy payloads, state transitions, and invariants.
- **Contracts**:
  - `contracts/recurring-tasks.openapi.yaml` defines API expectations for schedule CRUD/run-history surfaces.
  - `contracts/requirements-traceability.md` maps all `DOC-REQ-001..024` entries to FRs, implementation surfaces, and validation strategy.
- **Quickstart**: `quickstart.md` captures local runtime verification and test commands.

## Implementation Strategy

### Workstream 1: Persistence and domain invariants

- Ensure recurring definition/run schema and ORM models enforce required indexes and uniqueness constraints (`definition_id + scheduled_for`).
- Validate schedule/target/policy payloads with strict, user-facing errors.
- Enforce security guardrails for secret-like payload content in persisted schedule definitions.

### Workstream 2: Scheduler dispatch reliability

- Maintain two-stage daemon behavior with lock-safe due selection and pending-run dispatch loops.
- Enforce policy semantics: overlap skip/allow, catchup none/last/all, misfire grace, jitter, and global backfill cap.
- Reconcile uncertain dispatch outcomes via recurrence metadata before issuing new enqueue calls.

### Workstream 3: Target adapters and provenance

- Dispatch `queue_task` and `queue_task_template` targets through Agent Queue task jobs.
- Dispatch `manifest_run` targets through `ManifestsService.submit_manifest_run`.
- Dispatch `housekeeping` targets as queue-backed jobs with recurrence metadata.
- Attach provenance metadata (`definitionId`, `runId`, `scheduledFor`) to all dispatch payloads.

### Workstream 4: API and dashboard behavior

- Finalize REST surface (`list/create/get/update/run-now/list-runs`) with personal/global authorization constraints.
- Keep dashboard schedules list/detail/create routes and view-model source wiring in sync with API contracts.
- Expose run-history linkage to queue job detail when `queue_job_id` exists.

### Workstream 5: Runtime wiring and operations

- Keep `moonmind-scheduler` CLI and Docker Compose service configuration aligned with `MOONMIND_SCHEDULER_*` settings.
- Validate operational defaults (poll interval, batch size, max backfill) and one-shot tick workflow for diagnostics.

### Workstream 6: Validation coverage

- Expand automated tests for cron/timezone edge cases, policy semantics, and idempotent dispatch under retries/concurrency.
- Cover API contract behavior and dashboard route/source integration for schedules.
- Run unit suites through `./tools/test_unit.sh`; run orchestrator integration suites with `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` when validating integrated workflow paths.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| _None_ | — | — |
