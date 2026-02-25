# Tasks: Task Recurring Schedules System

**Input**: Design documents from `/specs/041-task-recurring-schedules/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required. This feature explicitly requires automated validation coverage (unit, integration-style service behavior, API contracts, dashboard routes/view-model).

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- [ ]: Required checkbox prefix
- `T###`: Sequential task ID
- `[P]`: Task can run in parallel (different files, no dependency on incomplete tasks)
- `[US#]`: Required for user-story phase tasks only
- Each task description includes one or more concrete file paths

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm implementation surfaces, validation entrypoints, and execution guardrails before runtime edits.

- [X] T001 [P] Reconfirm recurring schedule runtime scope, required validation commands, and artifact expectations in `specs/041-task-recurring-schedules/plan.md` and `specs/041-task-recurring-schedules/quickstart.md`.
- [X] T002 [P] Refresh schedule API contract scaffolding for list/create/get/patch/run-now/history endpoints in `specs/041-task-recurring-schedules/contracts/recurring-tasks.openapi.yaml`.
- [X] T003 [P] Add execution checklist checkpoints for runtime files, tests, and scheduler smoke verification in `specs/041-task-recurring-schedules/checklists/requirements.md`.
- [X] T004 [P] Confirm scheduler operational assumptions and environment prerequisites in `specs/041-task-recurring-schedules/research.md` and `specs/041-task-recurring-schedules/quickstart.md`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Complete persistence, domain invariants, scheduler runtime wiring, and requirement coverage prerequisites before user-story implementation.

**⚠️ CRITICAL**: No user story work starts until this phase is complete.

- [X] T005 Create recurring definition/run schema migration with uniqueness and due-scan indexes in `api_service/migrations/versions/202602240001_recurring_task_schedules.py` (DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012).
- [X] T006 Implement ORM entities and state columns/index metadata for recurring definitions and runs in `api_service/db/models.py` (DOC-REQ-001, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012).
- [X] T007 [P] Implement scheduler configuration settings and bounds in `moonmind/config/settings.py` and `.env-template` (DOC-REQ-004, DOC-REQ-016, DOC-REQ-017).
- [X] T008 [P] Implement recurring cron utilities with strict minute-level parsing and timezone handling in `moonmind/workflows/recurring_tasks/cron.py` and `moonmind/workflows/recurring_tasks/__init__.py` (DOC-REQ-006, DOC-REQ-007).
- [X] T009 Implement scheduler loop scaffolding and compose wiring in `moonmind/workflows/recurring_tasks/scheduler.py` and `docker-compose.yaml` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-017).
- [X] T010 Add implementation coverage mapping for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-015`, `DOC-REQ-016`, `DOC-REQ-017`, `DOC-REQ-018`, `DOC-REQ-019`, `DOC-REQ-020`, `DOC-REQ-021`, `DOC-REQ-022`, `DOC-REQ-023`, and `DOC-REQ-024` in `specs/041-task-recurring-schedules/contracts/requirements-traceability.md`.
- [X] T011 Add validation coverage mapping for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-009`, `DOC-REQ-010`, `DOC-REQ-011`, `DOC-REQ-012`, `DOC-REQ-013`, `DOC-REQ-014`, `DOC-REQ-015`, `DOC-REQ-016`, `DOC-REQ-017`, `DOC-REQ-018`, `DOC-REQ-019`, `DOC-REQ-020`, `DOC-REQ-021`, `DOC-REQ-022`, `DOC-REQ-023`, and `DOC-REQ-024` in `specs/041-task-recurring-schedules/contracts/requirements-traceability.md` and `specs/041-task-recurring-schedules/quickstart.md`.

**Checkpoint**: Foundation complete. User stories can now proceed in priority order.

---

## Phase 3: User Story 1 - Manage Recurring Schedules (Priority: P1) 🎯 MVP

**Goal**: Deliver schedule CRUD, enable/disable, run-now, and dashboard management surfaces.

**Independent Test**: Create schedules for supported targets, toggle enabled state, execute run-now, and verify schedule detail/list/history endpoints and dashboard pages reflect correct state and next-run values.

### Tests for User Story 1

- [X] T012 [P] [US1] Add API router tests for list/create/get/patch/run-now/history behavior and scope authorization in `tests/unit/api/routers/test_recurring_tasks.py` (DOC-REQ-003, DOC-REQ-020, DOC-REQ-022, DOC-REQ-023).
- [X] T013 [P] [US1] Add dashboard route/source tests for `/tasks/schedules`, `/tasks/schedules/new`, and schedule detail loading in `tests/unit/api/routers/test_task_dashboard.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-021, DOC-REQ-023).
- [X] T014 [P] [US1] Add service validation tests for secret-material rejection and personal/global ownership restrictions in `tests/unit/services/test_recurring_tasks_service.py` (DOC-REQ-002, DOC-REQ-020, DOC-REQ-022, DOC-REQ-023).

### Implementation for User Story 1

- [X] T015 [US1] Implement recurring schedule API handlers (list/create/get/patch/run-now/list-runs) in `api_service/api/routers/recurring_tasks.py` (DOC-REQ-003, DOC-REQ-020).
- [X] T016 [US1] Register recurring tasks router and dependency wiring in `api_service/main.py` and `api_service/api/routers/__init__.py` (DOC-REQ-001, DOC-REQ-020).
- [X] T017 [US1] Implement recurring definition CRUD, enable/disable updates, run-now creation, and scope checks in `api_service/services/recurring_tasks_service.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-011, DOC-REQ-020, DOC-REQ-022).
- [X] T018 [P] [US1] Implement dashboard schedules routes and template composition in `api_service/api/routers/task_dashboard.py` and `api_service/templates/task_dashboard.html` (DOC-REQ-021).
- [X] T019 [US1] Implement recurring schedule list/detail/create data-source adapters in `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-003, DOC-REQ-021).
- [X] T020 [US1] Implement schedules list/detail/create UI, enable toggle, and run-now actions in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-003, DOC-REQ-021).
- [X] T021 [US1] Normalize target/policy request validation and response serialization contracts in `api_service/services/recurring_tasks_service.py` and `specs/041-task-recurring-schedules/contracts/recurring-tasks.openapi.yaml` (DOC-REQ-009, DOC-REQ-013, DOC-REQ-016, DOC-REQ-020).
- [X] T022 [US1] Ensure API list/detail payloads expose next-run, last-run status, and target summary fields in `api_service/api/routers/recurring_tasks.py` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-021).

**Checkpoint**: US1 is complete and independently testable as MVP.

---

## Phase 4: User Story 2 - Reliable Due-Run Dispatch (Priority: P2)

**Goal**: Deliver HA-safe, idempotent schedule execution loops with policy enforcement and target dispatch adapters.

**Independent Test**: Run concurrent scheduler ticks against shared due schedules and confirm one run row per occurrence and one logical downstream dispatch artifact with retries/reconciliation handling failures.

### Tests for User Story 2

- [X] T023 [P] [US2] Add cron/timezone DST and minute-scope validation coverage in `tests/unit/workflows/recurring_tasks/test_cron.py` (DOC-REQ-006, DOC-REQ-007, DOC-REQ-023).
- [X] T024 [P] [US2] Add due-scan and dispatch idempotency tests for concurrent scheduler workers, including unique-occurrence enforcement and index-backed due-query behavior checks, in `tests/unit/services/test_recurring_tasks_service.py` (DOC-REQ-005, DOC-REQ-010, DOC-REQ-012, DOC-REQ-017, DOC-REQ-023).
- [X] T025 [P] [US2] Add policy and retry/reconciliation tests for overlap, catchup, misfire, jitter, bounded backoff, and effectively-once enqueue semantics under retry paths in `tests/unit/services/test_recurring_tasks_service.py` (DOC-REQ-008, DOC-REQ-016, DOC-REQ-018, DOC-REQ-023).
- [X] T026 [P] [US2] Add target adapter tests for queue task/template, manifest run, and housekeeping dispatch paths in `tests/unit/services/test_recurring_tasks_service.py` (DOC-REQ-002, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015, DOC-REQ-023).

### Implementation for User Story 2

- [X] T027 [US2] Implement stage-A due scan/run generation with lock-safe selection and next-run advancement in `api_service/services/recurring_tasks_service.py` (DOC-REQ-004, DOC-REQ-016, DOC-REQ-017).
- [X] T028 [US2] Implement stage-B pending-run dispatch with idempotent reconciliation and retry/backoff transitions in `api_service/services/recurring_tasks_service.py` (DOC-REQ-005, DOC-REQ-008, DOC-REQ-018).
- [X] T029 [P] [US2] Implement queue task and queue template expansion dispatch with recurrence metadata in `api_service/services/recurring_tasks_service.py` (DOC-REQ-002, DOC-REQ-013, DOC-REQ-019).
- [X] T030 [P] [US2] Implement manifest-run dispatch integration in `api_service/services/recurring_tasks_service.py` and `api_service/services/manifests_service.py` (DOC-REQ-002, DOC-REQ-014).
- [X] T031 [P] [US2] Implement queue-backed housekeeping dispatch support in `api_service/services/recurring_tasks_service.py` and `moonmind/workflows/agent_queue/job_types.py` (DOC-REQ-002, DOC-REQ-015).
- [X] T032 [US2] Finalize run outcome transitions and uniqueness-safe upsert behavior in `api_service/services/recurring_tasks_service.py`, `api_service/db/models.py`, and `api_service/migrations/versions/202602240001_recurring_task_schedules.py` (DOC-REQ-005, DOC-REQ-011, DOC-REQ-012).
- [X] T033 [US2] Finalize scheduler runtime loop operation and one-shot execution path in `moonmind/workflows/recurring_tasks/scheduler.py`, `docker-compose.yaml`, and `moonmind/config/settings.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004, DOC-REQ-017).

**Checkpoint**: US2 is complete with reliable due-run dispatch and policy/idempotency guarantees.

---

## Phase 5: User Story 3 - Observe Schedule History and Provenance (Priority: P3)

**Goal**: Expose run history, outcome states, and queue provenance in API and dashboard detail experiences.

**Independent Test**: Execute scheduled and manual runs, then verify API and dashboard history show trigger/outcome/queue linkage and recurrence provenance.

### Tests for User Story 3

- [X] T034 [P] [US3] Add run-history API tests validating trigger/outcome/message/queue linkage fields in `tests/unit/api/routers/test_recurring_tasks.py` (DOC-REQ-011, DOC-REQ-019, DOC-REQ-020, DOC-REQ-023).
- [X] T035 [P] [US3] Add dashboard history/provenance tests for queue-link rendering and source hydration in `tests/unit/api/routers/test_task_dashboard.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-019, DOC-REQ-021, DOC-REQ-023).

### Implementation for User Story 3

- [X] T036 [US3] Implement recurring run-history serialization and pagination behavior in `api_service/services/recurring_tasks_service.py` and `api_service/api/routers/recurring_tasks.py` (DOC-REQ-003, DOC-REQ-011, DOC-REQ-020).
- [X] T037 [US3] Attach and persist recurrence provenance metadata for downstream queue jobs in `api_service/services/recurring_tasks_service.py` and `moonmind/workflows/agent_queue/job_types.py` (DOC-REQ-019).
- [X] T038 [US3] Expose schedule history and queue-job deep-link view data in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/api/routers/task_dashboard.py` (DOC-REQ-019, DOC-REQ-021).
- [X] T039 [US3] Render history timeline, outcome badges, and queue navigation controls in `api_service/static/task_dashboard/dashboard.js` and `api_service/templates/task_dashboard.html` (DOC-REQ-021).
- [X] T040 [US3] Align recurring history/detail contract examples with implemented provenance fields in `specs/041-task-recurring-schedules/contracts/recurring-tasks.openapi.yaml` (DOC-REQ-019, DOC-REQ-020).

**Checkpoint**: US3 is complete with auditable schedule history and provenance visibility.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final hardening, runtime validation, and full requirement traceability closure.

- [X] T041 [P] Maintain explicit effective-once semantics and deferred optional manifest-YAML import scope notes in `specs/041-task-recurring-schedules/plan.md` and `specs/041-task-recurring-schedules/spec.md` (DOC-REQ-008, DOC-REQ-024).
- [X] T042 [P] Expand quickstart scheduler/API/manual validation sequence and expected results in `specs/041-task-recurring-schedules/quickstart.md` (DOC-REQ-004, DOC-REQ-021, DOC-REQ-023).
- [X] T043 Run required automated validation via `./tools/test_unit.sh` and capture command/results guidance in `specs/041-task-recurring-schedules/quickstart.md` and `specs/041-task-recurring-schedules/checklists/requirements.md` (DOC-REQ-023).
- [X] T044 Run runtime scope gate command `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and record passing evidence in `specs/041-task-recurring-schedules/checklists/requirements.md` (DOC-REQ-021, DOC-REQ-023).
- [X] T045 Finalize end-to-end requirement evidence entries for `DOC-REQ-001` through `DOC-REQ-024`, explicitly confirming implementation + validation coverage links per requirement (including deferred optional-scope evidence for `DOC-REQ-024`), in `specs/041-task-recurring-schedules/contracts/requirements-traceability.md` and `specs/041-task-recurring-schedules/checklists/requirements.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no prerequisites.
- Phase 2 depends on Phase 1 and blocks all user-story work.
- Phase 3 (US1) depends on Phase 2.
- Phase 4 (US2) depends on Phase 2 and should start after US1 API/service baseline is stable.
- Phase 5 (US3) depends on US1 plus US2 provenance/runtime state availability.
- Phase 6 depends on all story phases.

### User Story Dependencies

- **US1 (P1)**: Foundation only; no dependency on other user stories.
- **US2 (P2)**: Depends on foundational persistence/scheduler setup and benefits from US1 service contracts.
- **US3 (P3)**: Depends on US1 management APIs and US2 dispatch/provenance behavior.

### Within Each User Story

- Add/update tests first, confirm they fail for missing behavior, then implement runtime changes.
- Service/domain behavior before router/UI integration.
- Contract updates after implementation behavior is stable.

## Parallel Opportunities

- Phase 1 tasks `T001`-`T004` are parallel.
- In Phase 2, `T007` and `T008` can run in parallel after migration/model planning starts.
- In US1, tests `T012`-`T014` can run in parallel; dashboard tasks `T018` and `T020` can run in parallel after `T019` data contract shape is agreed.
- In US2, tests `T023`-`T026` are parallel; target adapter implementations `T029`-`T031` are parallel after core dispatch engine `T028` scaffolding.
- In US3, tests `T034`-`T035` can run in parallel; UI rendering `T039` can proceed once view-model task `T038` lands.

## Parallel Example: User Story 2

```bash
Task: "T023 [US2] cron/timezone DST tests in tests/unit/workflows/recurring_tasks/test_cron.py"
Task: "T024 [US2] due-scan idempotency tests in tests/unit/services/test_recurring_tasks_service.py"
Task: "T026 [US2] target adapter tests in tests/unit/services/test_recurring_tasks_service.py"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete US1 (`T012`-`T022`).
3. Validate independent US1 behavior through router and dashboard tests.
4. Demo schedule CRUD, enable/disable, and run-now workflows.

### Incremental Delivery

1. Deliver US1 for operator-facing management.
2. Deliver US2 for reliable scheduler dispatch and policy/idempotency behavior.
3. Deliver US3 for observability and provenance.
4. Execute Phase 6 validation/traceability closure before handoff.

## Task Summary & Validation

- Total tasks: **45**
- User story task counts:
  - **US1**: 11 tasks (`T012`-`T022`)
  - **US2**: 11 tasks (`T023`-`T033`)
  - **US3**: 7 tasks (`T034`-`T040`)
- Parallel opportunities: Present in every phase; explicitly identified above.
- Independent test criteria: Defined per user story and aligned to spec acceptance tests.
- Suggested MVP scope: Phase 1 + Phase 2 + US1.
- Checklist format validation: All tasks follow `- [ ] T### [P?] [US?] Description with path` format.
