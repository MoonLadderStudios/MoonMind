# Tasks: Workflow Scheduling

**Input**: Design documents from `/specs/086-workflow-scheduling/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Shared schema/model additions blocking all user stories

- [ ] T001 Add `ScheduleParameters` Pydantic model in moonmind/schemas/temporal_models.py (DOC-REQ-001)
- [ ] T002 [P] Add `schedule` field to `CreateExecutionRequest` in moonmind/schemas/temporal_models.py (DOC-REQ-001)
- [ ] T003 [P] Add `schedule` field to `CreateJobRequest` in moonmind/schemas/agent_queue_models.py (DOC-REQ-001)
- [ ] T004 [P] Add `ScheduleCreatedResponse` model in moonmind/schemas/temporal_models.py (DOC-REQ-008)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database schema and state enum changes that must exist before API logic

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T005 Add `SCHEDULED = "scheduled"` to `MoonMindWorkflowState` enum in api_service/db/models.py (DOC-REQ-003)
- [ ] T006 Add `scheduled` → `queued` mapping to `_DASHBOARD_STATUS_BY_STATE` in api_service/api/routers/executions.py (DOC-REQ-003)
- [ ] T007 Add `scheduled_for` column to `TemporalExecutionRecord` in api_service/db/models.py (DOC-REQ-015)
- [ ] T008 Create Alembic migration for `scheduled_for` column in api_service/migrations/versions/ (DOC-REQ-015)
- [ ] T009 Add `start_delay` parameter to `TemporalClientAdapter.start_workflow()` in moonmind/workflows/temporal/client.py (DOC-REQ-014)
- [ ] T010 Add `submitScheduleEnabled` feature flag to `build_runtime_config()` in api_service/api/routers/task_dashboard_view_model.py (DOC-REQ-016)

**Checkpoint**: Foundation ready — deferred/recurring API logic can now be implemented

---

## Phase 3: User Story 3 — Backend Schedule Object on Create Endpoint (Priority: P1) 🎯 MVP

**Goal**: The `POST /api/executions` endpoint accepts an optional `schedule` object and routes to either deferred (Temporal start_delay) or recurring (RecurringTasksService) creation paths

**Independent Test**: Send curl requests with `schedule.mode=once` and `schedule.mode=recurring` and verify correct routing, responses, and persisted records

### Implementation for User Story 3 (Backend)

- [ ] T011 [US3] Implement `schedule.mode=once` path in `create_execution()` in api_service/api/routers/executions.py — compute `start_delay`, pass to `start_workflow()`, set `mm_state=scheduled`, persist `scheduled_for` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-007)
- [ ] T012 [US3] Implement `schedule.mode=recurring` path in `create_execution()` in api_service/api/routers/executions.py — construct target payload, delegate to `RecurringTasksService.create_definition()`, return `ScheduleCreatedResponse` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-008)
- [ ] T013 [US3] Implement `schedule.mode=once` path in `_create_execution_from_task_request()` in api_service/api/routers/executions.py for `CreateJobRequest` payloads (DOC-REQ-002)
- [ ] T014 [US3] Implement `schedule.mode=recurring` path in `_create_execution_from_task_request()` for `CreateJobRequest` payloads (DOC-REQ-005, DOC-REQ-006)
- [ ] T015 [US3] Add validation: reject past `scheduledFor` with 422 for `mode=once`, validate cron expression for `mode=recurring` in api_service/api/routers/executions.py (DOC-REQ-018)
- [ ] T016 [US3] Add `scheduledFor` and `scheduled` state to `ExecutionModel` serialization in moonmind/schemas/temporal_models.py (DOC-REQ-007)

### Validation for User Story 3

- [ ] T017 [US3] Unit test: immediate create without schedule field works unchanged (regression) in tests/unit/api_service/api/routers/test_executions_schedule.py (DOC-REQ-001)
- [ ] T018 [P] [US3] Unit test: `schedule.mode=once` computes correct `start_delay` and sets `scheduled` state in tests/unit/api_service/api/routers/test_executions_schedule.py (DOC-REQ-002, DOC-REQ-003)
- [ ] T019 [P] [US3] Unit test: `schedule.mode=once` with past `scheduledFor` returns 422 in tests/unit/api_service/api/routers/test_executions_schedule.py (DOC-REQ-018)
- [ ] T020 [P] [US3] Unit test: `schedule.mode=recurring` delegates to `RecurringTasksService` and returns correct response in tests/unit/api_service/api/routers/test_executions_schedule.py (DOC-REQ-005, DOC-REQ-008)
- [ ] T021 [P] [US3] Unit test: `schedule.mode=recurring` with invalid cron returns 422 in tests/unit/api_service/api/routers/test_executions_schedule.py (DOC-REQ-018)
- [ ] T022 [P] [US3] Unit test: `start_workflow()` passes `start_delay` to Temporal SDK in tests/unit/moonmind/workflows/temporal/test_client_start_delay.py (DOC-REQ-014)

**Checkpoint**: Backend schedule object fully functional — API accepts schedule, defers, and creates recurring definitions

---

## Phase 4: User Story 1 — Schedule a One-Time Deferred Task (Priority: P1)

**Goal**: Users can schedule a deferred task from the Mission Control submit form and see it in the list with "Scheduled" state

**Independent Test**: Navigate to `/tasks/new`, select "Schedule for later", pick a future time, submit, and verify the task appears with scheduled badge and detail banner

### Implementation for User Story 1 (Frontend)

- [ ] T023 [US1] Add schedule panel HTML/JS to submit form in api_service/static/task_dashboard/dashboard.js — radio buttons for "Run immediately" / "Schedule for later" / "Set up recurring schedule" (DOC-REQ-009)
- [ ] T024 [US1] Add date/time/timezone picker for "Schedule for later" mode in api_service/static/task_dashboard/dashboard.js (DOC-REQ-010)
- [ ] T025 [US1] Add dynamic submit button label per mode in api_service/static/task_dashboard/dashboard.js (DOC-REQ-012)
- [ ] T026 [US1] Wire schedule panel to submit payload — include `schedule` object in API request in api_service/static/task_dashboard/dashboard.js (DOC-REQ-001)
- [ ] T027 [US1] Gate schedule panel visibility behind `submitScheduleEnabled` feature flag in api_service/static/task_dashboard/dashboard.js (DOC-REQ-016)
- [ ] T028 [US1] Add "Scheduled" status badge and countdown banner for deferred tasks on detail page in api_service/static/task_dashboard/dashboard.js (DOC-REQ-017)
- [ ] T029 [US1] Implement redirect per mode after submit — task detail for immediate/deferred, schedule detail for recurring in api_service/static/task_dashboard/dashboard.js (DOC-REQ-013)

**Checkpoint**: Deferred one-time scheduling fully functional end-to-end

---

## Phase 5: User Story 2 — Create Recurring Schedule from Submit Form (Priority: P2)

**Goal**: Users can create a recurring schedule from the same submit form by selecting "Set up recurring schedule"

**Independent Test**: Navigate to `/tasks/new`, select "Set up recurring schedule", enter cron, submit, and verify redirect to schedule detail page

### Implementation for User Story 2 (Frontend)

- [ ] T030 [US2] Add cron expression input with inline validation in api_service/static/task_dashboard/dashboard.js (DOC-REQ-011)
- [ ] T031 [US2] Add human-readable cron preview label in api_service/static/task_dashboard/dashboard.js (DOC-REQ-011)
- [ ] T032 [US2] Add schedule name field (auto-populated from task title) in api_service/static/task_dashboard/dashboard.js (DOC-REQ-011)
- [ ] T033 [US2] Add timezone picker for recurring mode in api_service/static/task_dashboard/dashboard.js (DOC-REQ-011)
- [ ] T034 [US2] Wire recurring fields into submit payload with `schedule.mode=recurring` in api_service/static/task_dashboard/dashboard.js (DOC-REQ-005)

**Checkpoint**: Recurring schedule creation from submit form is fully functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validation, error handling, and documentation updates

- [ ] T035 Add `scheduled_for` field to `ExecutionModel` response in moonmind/schemas/temporal_models.py
- [ ] T036 Add validation: prevent scheduling in the past in dashboard.js (client-side guard before API call)
- [ ] T037 [P] Add cron input error state rendering for invalid expressions in dashboard.js
- [ ] T038 [P] Run full unit test suite via `./tools/test_unit.sh` to verify no regressions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **User Story 3/Backend (Phase 3)**: Depends on Phase 2 — backend must exist before frontend
- **User Story 1/Deferred UI (Phase 4)**: Depends on Phase 3 (backend schedule routing)
- **User Story 2/Recurring UI (Phase 5)**: Depends on Phase 3 (backend schedule routing); can run in parallel with Phase 4
- **Polish (Phase 6)**: Depends on Phases 3–5

### User Story Dependencies

- **US3 (Backend)**: Foundation only — no story dependencies
- **US1 (Deferred UI)**: Requires US3 (backend API) — can start after Phase 3
- **US2 (Recurring UI)**: Requires US3 (backend API) — can start after Phase 3, parallel with US1

### Parallel Opportunities

- T002, T003, T004 can run in parallel (different files)
- T005, T006, T007 can run in parallel (different files)
- T018–T022 can run in parallel (same test file but independent tests)
- Phase 4 (US1) and Phase 5 (US2) can proceed in parallel

---

## Implementation Strategy

### MVP First (User Story 3 Only — Backend API)

1. Complete Phase 1: Schema models
2. Complete Phase 2: DB migration + state enum + start_delay + feature flag
3. Complete Phase 3: Backend schedule routing (deferred + recurring via API)
4. **STOP and VALIDATE**: All unit tests pass, curl works for both modes

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US3 (Backend) → Test via curl → MVP backend
3. US1 (Deferred UI) → Test via browser → End-to-end deferred flow
4. US2 (Recurring UI) → Test via browser → Full feature

---

## Summary

- **Total tasks**: 38
- **Phase 1 (Setup)**: 4 tasks
- **Phase 2 (Foundational)**: 6 tasks
- **Phase 3 (US3 Backend)**: 12 tasks (6 impl + 6 validation)
- **Phase 4 (US1 Deferred UI)**: 7 tasks
- **Phase 5 (US2 Recurring UI)**: 5 tasks
- **Phase 6 (Polish)**: 4 tasks
- **DOC-REQ coverage**: All 18 DOC-REQ-* IDs referenced in task descriptions
