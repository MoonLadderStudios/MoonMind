# Tasks: Resubmit Terminal Tasks

**Input**: Design documents from `/specs/043-resubmit-terminal-tasks/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `quickstart.md`, `contracts/`

**Tests**: This feature explicitly requires validation coverage across service, router, view-model, and dashboard flows; include `./tools/test_unit.sh`.

**Organization**: Tasks are grouped by user story to keep each story independently implementable and testable.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared contracts and scaffolding for the resubmit mode before runtime behavior changes.

- [X] T001 Add resubmit request/response schema scaffolding in `moonmind/schemas/agent_queue_models.py` and endpoint placeholders in `api_service/api/routers/agent_queue.py` (DOC-REQ-002)
- [X] T002 [P] Add dashboard runtime endpoint template scaffolding for `sources.queue.resubmit` in `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-003)
- [X] T003 [P] Add test scaffolding for resubmit service/router/dashboard coverage in `tests/unit/workflows/agent_queue/test_service_resubmit.py`, `tests/unit/api/routers/test_agent_queue.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, and `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement shared eligibility, authorization, normalization, and event-linkage primitives that all stories depend on.

**⚠️ CRITICAL**: User-story implementation depends on these foundational invariants.

- [X] T004 Implement shared resubmittable eligibility helpers (`type=task`, `status in {failed,cancelled}`) while preserving existing queued-edit eligibility in `moonmind/workflows/agent_queue/service.py` and `moonmind/workflows/agent_queue/models.py` (DOC-REQ-001, DOC-REQ-002)
- [X] T005 [P] Implement owner-authorization parity helpers for resubmit (`created_by_user_id`/`requested_by_user_id`) in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-002)
- [X] T006 [P] Implement create-envelope parity validation for resubmit (`type`, `priority`, `maxAttempts`, `affinityKey`, `payload`, optional `note`) in `moonmind/schemas/agent_queue_models.py` (DOC-REQ-002)
- [X] T007 [P] Add source/new lineage event payload helpers (`Job resubmitted`, `Job resubmitted from`) in `moonmind/workflows/agent_queue/models.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-002)
- [X] T008 [P] Add dashboard mode-resolution helpers for `create|edit|resubmit` using prefill source snapshots in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-003)

**Checkpoint**: Foundational resubmit contracts are ready for story-specific implementation.

---

## Phase 3: User Story 1 - Resubmit a Failed or Cancelled Task (Priority: P1) 🎯 MVP

**Goal**: Operators can resubmit failed/cancelled task jobs as new queued jobs using the existing prefill route.

**Independent Test**: From a failed/cancelled task detail page, enter resubmit mode, submit edited values, and verify redirect to a new job while source job remains unchanged.

### Tests for User Story 1

- [X] T009 [P] [US1] Add service tests for successful failed/cancelled resubmit creating a distinct new job and preserving source immutability in `tests/unit/workflows/agent_queue/test_service_resubmit.py` (DOC-REQ-001, DOC-REQ-002)
- [X] T010 [P] [US1] Add router tests for `POST /api/queue/jobs/{jobId}/resubmit` success (`201`) and response shape in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-002)
- [X] T011 [P] [US1] Add dashboard tests for Resubmit CTA label, prefill reuse, submit endpoint selection, and success redirect notice in `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-001, DOC-REQ-003)

### Implementation for User Story 1

- [X] T012 [US1] Implement authenticated `POST /api/queue/jobs/{jobId}/resubmit` route returning new `JobModel` in `api_service/api/routers/agent_queue.py` (DOC-REQ-002)
- [X] T013 [US1] Implement `resubmit_job(...)` service transaction that loads source job, normalizes payload with `normalize_task_job_payload`, creates new job, and keeps source immutable in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-002)
- [X] T014 [US1] Implement detail-page Resubmit action gating and route reuse (`/tasks/queue/new?editJobId=<id>`) for terminal task jobs in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-003)
- [X] T015 [US1] Implement create/edit/resubmit submit branching, cancel-to-source navigation, and success redirect/banner behavior in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-001, DOC-REQ-003)
- [X] T016 [US1] Expose runtime-injected resubmit endpoint template under `sources.queue.resubmit` in `api_service/api/routers/task_dashboard_view_model.py` and consume it in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-002, DOC-REQ-003)

**Checkpoint**: User Story 1 provides an end-to-end resubmit flow for failed/cancelled tasks.

---

## Phase 4: User Story 2 - Enforce Safe Eligibility and Ownership (Priority: P2)

**Goal**: Resubmit requests are rejected for ineligible states/types and unauthorized users with deterministic API semantics.

**Independent Test**: Attempt resubmit on queued/running/non-task/foreign-owned jobs and verify rejections with documented error codes and no new job creation.

### Tests for User Story 2

- [X] T017 [P] [US2] Add service tests for ineligible state/type rejections (`queued`, `running`, non-`task`) and no-write guarantees in `tests/unit/workflows/agent_queue/test_service_resubmit.py` (DOC-REQ-002)
- [X] T018 [P] [US2] Add service tests for non-owner rejection with ownership parity behavior in `tests/unit/workflows/agent_queue/test_service_resubmit.py` (DOC-REQ-002)
- [X] T019 [P] [US2] Add router tests for `403/404/409/422/400` resubmit error mappings in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-002)
- [X] T020 [P] [US2] Add dashboard tests for mode inference fallback/error handling when source becomes ineligible between prefill and submit in `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-003)

### Implementation for User Story 2

- [X] T021 [US2] Enforce submit-time eligibility and ownership checks with deterministic domain exceptions in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-002)
- [X] T022 [US2] Map resubmit domain exceptions to stable HTTP error envelopes in `api_service/api/routers/agent_queue.py` (DOC-REQ-002)
- [X] T023 [US2] Ensure dashboard mode resolution falls back to update only for queued-never-started and rejects unsupported prefill combinations in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-003)

**Checkpoint**: User Story 2 hardens safety boundaries and authorization.

---

## Phase 5: User Story 3 - Preserve Audit History and Communicate Attachment Limits (Priority: P3)

**Goal**: Source/new job lineage is auditable and UI/docs clearly state v1 attachment no-copy behavior.

**Independent Test**: Successful resubmit persists source/new linkage events and resubmit UI communicates attachments are not copied.

### Tests for User Story 3

- [X] T024 [P] [US3] Add service tests verifying source `Job resubmitted` event payload (`newJobId`, `actorUserId`, optional `note`, `changedFields`) and optional new-job linkage event in `tests/unit/workflows/agent_queue/test_service_resubmit.py` (DOC-REQ-001, DOC-REQ-002)
- [X] T025 [P] [US3] Add dashboard tests ensuring resubmit-mode attachment notice is rendered and cancel path returns to source detail in `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-001, DOC-REQ-003)
- [X] T026 [P] [US3] Add view-model tests for runtime config exposure of `sources.queue.resubmit` used by thin dashboard submit plumbing in `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-003)

### Implementation for User Story 3

- [X] T027 [US3] Persist source/new audit linkage events for successful resubmits in `moonmind/workflows/agent_queue/service.py` and `moonmind/workflows/agent_queue/models.py` (DOC-REQ-001, DOC-REQ-002)
- [X] T028 [US3] Add explicit resubmit attachment policy UI message (attachments are not copied in v1) in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-001, DOC-REQ-003)
- [X] T029 [US3] Update resubmit API contract documentation and endpoint semantics in `docs/TaskQueueSystem.md` (DOC-REQ-002)
- [X] T030 [US3] Update task-editing documentation with terminal resubmit behavior, source immutability, and no-copy attachments policy in `docs/TaskEditingSystem.md` (DOC-REQ-001)
- [X] T031 [US3] Update thin-dashboard architecture documentation for create/edit/resubmit mode resolution and submit routing in `docs/TaskUiArchitecture.md` (DOC-REQ-003)

**Checkpoint**: User Story 3 completes lineage transparency and operator-facing policy clarity.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and traceability gates across all stories.

- [X] T032 [P] Reconcile implementation/validation evidence for `DOC-REQ-001`, `DOC-REQ-002`, and `DOC-REQ-003` in `specs/043-resubmit-terminal-tasks/contracts/requirements-traceability.md`
- [X] T033 Run full validation suite via `./tools/test_unit.sh` covering `tests/unit/workflows/agent_queue/test_service_resubmit.py`, `tests/unit/api/routers/test_agent_queue.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, and `tests/task_dashboard/test_submit_runtime.js` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003)
- [X] T034 Run runtime scope validation via `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: Starts immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2 and delivers MVP.
- **Phase 4 (US2)**: Depends on Phase 3 baseline resubmit path.
- **Phase 5 (US3)**: Depends on Phase 3 baseline and can run in parallel with late US2 hardening.
- **Phase 6 (Polish)**: Depends on completion of all targeted user stories.

### User Story Dependencies

- **US1 (P1)**: Independent after foundational completion.
- **US2 (P2)**: Depends on US1 API/service baseline.
- **US3 (P3)**: Depends on US1 lineage hooks and dashboard mode baseline.

### Within Each User Story

- Write tests before finalizing implementation and confirm failures before fixes.
- Service invariants and API contracts must land before dashboard submit wiring that depends on them.
- Story checkpoint should pass before advancing to the next priority.

## Parallel Opportunities

- **Setup**: T002 and T003 can run in parallel after T001.
- **Foundational**: T005, T006, T007, and T008 can run in parallel after T004 defines shared invariants.
- **US1**: T009, T010, and T011 are parallel; T014 and T016 can run in parallel once T012/T013 interfaces are stable.
- **US2**: T017, T018, T019, and T020 are parallel; T022 and T023 can run in parallel once T021 is in place.
- **US3**: T024, T025, and T026 are parallel; T028 and docs tasks T029-T031 can run in parallel once T027 event shape is fixed.
- **Polish**: T032 can run before final validation gates T033 and T034.

## Parallel Example: User Story 1

```bash
Task T009: tests/unit/workflows/agent_queue/test_service_resubmit.py
Task T010: tests/unit/api/routers/test_agent_queue.py
Task T011: tests/task_dashboard/test_submit_runtime.js
```

## Parallel Example: User Story 2

```bash
Task T017: tests/unit/workflows/agent_queue/test_service_resubmit.py
Task T019: tests/unit/api/routers/test_agent_queue.py
Task T020: tests/task_dashboard/test_submit_runtime.js
```

## Parallel Example: User Story 3

```bash
Task T024: tests/unit/workflows/agent_queue/test_service_resubmit.py
Task T025: tests/task_dashboard/test_submit_runtime.js
Task T026: tests/unit/api/routers/test_task_dashboard_view_model.py
```

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 and Phase 2.
2. Deliver Phase 3 (US1) end-to-end resubmit flow.
3. Validate US1 independently before hardening and polish.

### Incremental Delivery

1. Add US2 eligibility/authorization hardening.
2. Add US3 audit lineage and attachment-policy communication.
3. Run full validation and scope gates in Phase 6.

### Team Parallelization

1. Backend owner: T012-T013, T021-T022, T027.
2. Dashboard owner: T014-T016, T023, T025, T028.
3. Docs/contract owner: T029-T032.
4. Validation owner: T009-T011, T017-T020, T024-T026, T033-T034.
