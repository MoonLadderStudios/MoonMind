# Tasks: Task Editing System

**Input**: Design documents from `/specs/042-task-editing-system/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `quickstart.md`, `contracts/`

**Tests**: This feature explicitly requires validation coverage; include service, router, and dashboard tests.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared update contract scaffolding and test harness alignment.

- [X] T001 Add shared queue-update route scaffolding in `api_service/api/routers/agent_queue.py` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-006, DOC-REQ-014)
- [X] T002 [P] Add queued-update request schema scaffolding in `moonmind/schemas/agent_queue_models.py` and `moonmind/workflows/agent_queue/task_contract.py` (DOC-REQ-005, DOC-REQ-007)
- [X] T003 [P] Add queued-update test scaffolding in `tests/unit/workflows/agent_queue/test_service_update.py`, `tests/unit/api/routers/test_agent_queue.py`, and `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-015, DOC-REQ-018)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core runtime update infrastructure that must be complete before user stories.

**⚠️ CRITICAL**: No user story implementation starts until this phase is done.

- [X] T004 Implement row-lock update retrieval helpers in `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-010, DOC-REQ-011, DOC-REQ-012)
- [X] T005 [P] Implement shared editability and owner-authorization guards in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-006, DOC-REQ-010)
- [X] T006 [P] Implement normalized update error mapping helpers in `moonmind/workflows/agent_queue/service.py` and `api_service/api/routers/agent_queue.py` (DOC-REQ-009)
- [X] T007 [P] Implement `expectedUpdatedAt` and `note` validation with create-envelope parity in `moonmind/schemas/agent_queue_models.py` (DOC-REQ-005, DOC-REQ-007)
- [X] T008 [P] Add queue update endpoint template plumbing in `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-014)

**Checkpoint**: Foundational runtime update plumbing is ready.

---

## Phase 3: User Story 1 - Edit an Eligible Queued Task (Priority: P1) 🎯 MVP

**Goal**: Let operators edit eligible queued task jobs in place while preserving job identity.

**Independent Test**: Open `/tasks/queue/new?editJobId=<jobId>` from an eligible queued job, submit a valid update, and confirm the same job ID shows updated fields.

### Tests for User Story 1

- [X] T009 [P] [US1] Add service tests for eligible queued update success, editability checks, and existing-schema reuse invariants in `tests/unit/workflows/agent_queue/test_service_update.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-006, DOC-REQ-010, DOC-REQ-011, DOC-REQ-015)
- [X] T010 [P] [US1] Add router tests for authenticated PUT success, not-found, and authorization outcomes in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-006, DOC-REQ-008, DOC-REQ-009, DOC-REQ-015)
- [X] T011 [P] [US1] Add dashboard tests for edit entry point, prefill, and Update submit flow in `tests/task_dashboard/test_submit_runtime.js` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-014, DOC-REQ-015)

### Implementation for User Story 1

- [X] T012 [US1] Implement authenticated `PUT /api/queue/jobs/{jobId}` update handler returning `JobModel` in `api_service/api/routers/agent_queue.py` (DOC-REQ-006, DOC-REQ-008, DOC-REQ-015)
- [X] T013 [US1] Implement in-place queued task mutation preserving job ID and mutable-field updates in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-010, DOC-REQ-011, DOC-REQ-015, DOC-REQ-018)
- [X] T014 [US1] Implement transactional queue-update repository writes for mutable fields in `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-010, DOC-REQ-011, DOC-REQ-012)
- [X] T015 [US1] Implement edit mode query parsing (including `/tasks/new` alias), prefill, Update CTA label, and cancel-to-detail behavior in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-014, DOC-REQ-015)

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Prevent Unsafe or Stale Updates (Priority: P2)

**Goal**: Reject claim races and stale-tab updates with explicit conflict semantics.

**Independent Test**: Reproduce claim/update race and stale `expectedUpdatedAt` submissions; verify conflicts are explicit and no silent overwrites occur.

### Tests for User Story 2

- [X] T016 [P] [US2] Add service tests for claim-race and stale `expectedUpdatedAt` conflicts in `tests/unit/workflows/agent_queue/test_service_update.py` (DOC-REQ-012, DOC-REQ-013, DOC-REQ-015)
- [X] T017 [P] [US2] Add router tests for `409/422/400` update conflict and runtime-gate mappings in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-009, DOC-REQ-013, DOC-REQ-015)
- [X] T018 [P] [US2] Add dashboard tests for stale-token conflict handling and actionable error UX in `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-005, DOC-REQ-009, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015)

### Implementation for User Story 2

- [X] T019 [US2] Enforce optional optimistic concurrency token checks in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-005, DOC-REQ-013)
- [X] T020 [US2] Enforce lock-first state validation for worker claim/update race safety in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-012)
- [X] T021 [US2] Normalize update exception-to-HTTP mappings for `404/403/409/422/400` in `api_service/api/routers/agent_queue.py` (DOC-REQ-009)
- [X] T022 [US2] Submit create-parity update payloads with `expectedUpdatedAt` through edit mode in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-005, DOC-REQ-014, DOC-REQ-015)

**Checkpoint**: User Story 2 conflict protections are independently testable.

---

## Phase 5: User Story 3 - Preserve Auditability and Scope Boundaries (Priority: P3)

**Goal**: Record auditable update events and enforce v1 scope exclusions.

**Independent Test**: Verify successful updates append `Job updated` events; verify running/start-state and attachment-edit attempts are rejected in v1.

### Tests for User Story 3

- [X] T023 [P] [US3] Add service tests for update audit event payload (`actorUserId`, `changedFields`, optional `note`) in `tests/unit/workflows/agent_queue/test_service_update.py` (DOC-REQ-007, DOC-REQ-010, DOC-REQ-015)
- [X] T024 [P] [US3] Add tests that running/started jobs and attachment-mutation attempts are rejected in `tests/unit/workflows/agent_queue/test_service_update.py` and `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-001, DOC-REQ-016, DOC-REQ-015)
- [X] T025 [P] [US3] Add dashboard/view-model tests for endpoint visibility and editability gating in `tests/unit/api/routers/test_task_dashboard_view_model.py` and `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-003, DOC-REQ-014, DOC-REQ-017, DOC-REQ-015)

### Implementation for User Story 3

- [X] T026 [US3] Append `Job updated` queue events with changed-field summary and optional note in `moonmind/workflows/agent_queue/service.py` and `moonmind/workflows/agent_queue/models.py` (DOC-REQ-007, DOC-REQ-010)
- [X] T027 [US3] Enforce v1 non-goal guardrails that block attachment mutation in `moonmind/workflows/agent_queue/task_contract.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-016)
- [X] T028 [US3] Keep edit UX scoped to supported task fields and hide unsupported attachment editing controls in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-016)
- [X] T029 [US3] Document additive queue update endpoint and edit flow in `docs/TaskQueueSystem.md` and `docs/TaskUiArchitecture.md` (DOC-REQ-017)

**Checkpoint**: User Story 3 auditability and scope boundaries are independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final traceability, validation, and readiness gates across all stories.

- [X] T030 [P] Reconcile `DOC-REQ-*` to implementation/evidence coverage entries in `specs/042-task-editing-system/contracts/requirements-traceability.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017, DOC-REQ-018)
- [X] T031 Run full validation suite via `./tools/test_unit.sh` and resolve regressions in `tests/unit/workflows/agent_queue/test_service_update.py`, `tests/unit/api/routers/test_agent_queue.py`, and `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-015, DOC-REQ-018)
- [X] T032 Run runtime scope validation with `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` (DOC-REQ-018)
- [X] T033 [P] Refresh endpoint/edit-flow and v1 non-goal notes in `docs/TaskEditingSystem.md` (DOC-REQ-017)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: Starts immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user-story work.
- **Phase 3 (US1)**: Depends on Phase 2 and delivers MVP.
- **Phase 4 (US2)**: Depends on Phase 3 runtime update path.
- **Phase 5 (US3)**: Depends on Phase 3 runtime update path; can run in parallel with Phase 4 after Phase 3 checkpoint.
- **Phase 6 (Polish)**: Depends on completion of all targeted user stories.

### User Story Dependencies

- **US1 (P1)**: No dependency on other stories after foundational completion.
- **US2 (P2)**: Depends on US1 update endpoint/service baseline.
- **US3 (P3)**: Depends on US1 update endpoint/service baseline; can proceed without waiting for all US2 tasks.

### Within Each User Story

- Test tasks should be authored before or alongside implementation and must fail before corresponding fixes.
- Service/repository invariants must land before router/dashboard wiring that depends on them.
- Story-level checkpoint must pass before advancing to final polish gates.

## Parallel Opportunities

- **Setup**: T002 and T003 can run in parallel once T001 defines shared scaffolding boundaries.
- **Foundational**: T005, T006, T007, and T008 can run in parallel after T004 begins lock/update primitives.
- **US1**: T009, T010, and T011 can run in parallel; T012 and T015 can proceed in parallel after T013/T014 interfaces are stable.
- **US2**: T016, T017, and T018 can run in parallel; T021 and T022 can run in parallel after T019/T020.
- **US3**: T023, T024, and T025 can run in parallel; T027 and T028 can run in parallel after T026 event-shape decisions.
- **Polish**: T030 and T033 can run in parallel before final validation gates T031 and T032.

## Parallel Example: User Story 1

```bash
# Parallel validation tasks
Task T009: tests/unit/workflows/agent_queue/test_service_update.py
Task T010: tests/unit/api/routers/test_agent_queue.py
Task T011: tests/task_dashboard/test_submit_runtime.js + tests/unit/api/routers/test_task_dashboard_view_model.py

# Parallel implementation tasks after service contract stabilizes
Task T012: api_service/api/routers/agent_queue.py
Task T015: api_service/static/task_dashboard/dashboard.js
```

## Parallel Example: User Story 2

```bash
# Parallel validation tasks
Task T016: tests/unit/workflows/agent_queue/test_service_update.py
Task T017: tests/unit/api/routers/test_agent_queue.py
Task T018: tests/task_dashboard/test_submit_runtime.js

# Parallel implementation tasks after core conflict checks
Task T021: api_service/api/routers/agent_queue.py
Task T022: api_service/static/task_dashboard/dashboard.js
```

## Parallel Example: User Story 3

```bash
# Parallel validation tasks
Task T023: tests/unit/workflows/agent_queue/test_service_update.py
Task T024: tests/unit/workflows/agent_queue/test_service_update.py + tests/task_dashboard/test_submit_runtime.js
Task T025: tests/unit/api/routers/test_task_dashboard_view_model.py + tests/task_dashboard/test_submit_runtime.js

# Parallel implementation tasks after audit payload shape is fixed
Task T027: moonmind/workflows/agent_queue/task_contract.py + moonmind/workflows/agent_queue/service.py
Task T028: api_service/static/task_dashboard/dashboard.js
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete US1 tasks (Phase 3).
3. Validate US1 independently via T009-T011 and targeted manual queue edit check.
4. Demonstrate MVP with in-place queued update preserving job ID.

### Incremental Delivery

1. Deliver US1 (core edit flow).
2. Add US2 (conflict and stale-write protections).
3. Add US3 (auditability and scope guardrails).
4. Run Phase 6 gates for full acceptance.

### Runtime Scope Guard

- Runtime implementation tasks are explicitly included under `moonmind/` and `api_service/` paths.
- Validation tasks are explicitly included under `tests/` plus `./tools/test_unit.sh` and scope-check commands.
- Completion is invalid unless both runtime implementation and validation tasks are complete (DOC-REQ-018).
