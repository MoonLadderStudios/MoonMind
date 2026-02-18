# Tasks: Live Task Handoff

**Input**: Design documents from `/specs/024-live-task-handoff/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required validation coverage across API/router, service/repository, worker runtime, dashboard wiring, and settings/config behavior.

**Organization**: Tasks are grouped by user story with explicit `DOC-REQ-*` coverage and independent testability.

## Format: `[ID] [P?] [Story] Description`

- `[P]`: Can run in parallel (different files, no dependencies)
- `[Story]`: User story label (`[US1]`, `[US2]`, `[US3]`)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add schema + contract scaffolding for live handoff.

- [X] T001 Add migration for `task_run_live_sessions` and `task_run_control_events` in `api_service/migrations/versions/202602180001_live_task_handoff.py` (DOC-REQ-002, DOC-REQ-003)
- [X] T002 [P] Create/update live handoff API and traceability contracts in `specs/024-live-task-handoff/contracts/live-task-handoff.openapi.yaml` and `specs/024-live-task-handoff/contracts/requirements-traceability.md` (DOC-REQ-004, DOC-REQ-016)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement shared queue API/service/repository/model/config primitives required by all stories.

- [X] T003 Extend ORM models/enums/relationships for live sessions + control events in `moonmind/workflows/agent_queue/models.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003)
- [X] T004 [P] Add live-session/control request and response schemas in `moonmind/schemas/agent_queue_models.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006)
- [X] T005 Implement repository primitives for live-session upsert, control-event append, and `payload.liveControl` updates in `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-009)
- [X] T006 Implement queue service flows for create/report/heartbeat/grant/revoke/control/message in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-011)
- [X] T007 Add task-runs router and app wiring in `api_service/api/routers/task_runs.py` and `api_service/main.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006)
- [X] T008 [P] Add live-session runtime configuration plumbing in `moonmind/config/settings.py`, `.env-template`, `docker-compose.yaml`, and `tools/start-codex-worker.sh` (DOC-REQ-012)

**Checkpoint**: Shared live-session/control persistence and API wiring are available for story-level behavior.

---

## Phase 3: User Story 1 - Observe Active Task Sessions Live (Priority: P1) 🎯 MVP

**Goal**: Operators can enable/view task live sessions and see lifecycle/attach status without interrupting task execution.

**Independent Test**: Enable live session for running task and verify `starting -> ready -> ended` lifecycle and RO attach visibility.

### Tests for User Story 1

- [X] T009 [P] [US1] Add router tests for live-session create/get/grant/revoke/report/heartbeat behavior in `tests/unit/api/routers/test_task_runs.py` (DOC-REQ-004, DOC-REQ-016)
- [X] T010 [P] [US1] Add service/repository lifecycle coverage tests in `tests/unit/workflows/agent_queue/test_service_hardening.py` and `tests/unit/workflows/agent_queue/test_repositories.py` for live-session state transitions (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-016)

### Implementation for User Story 1

- [X] T011 [US1] Implement worker live-session bootstrap/report/heartbeat/teardown flow in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-001, DOC-REQ-007, DOC-REQ-008)
- [X] T012 [US1] Ensure RO/RW attach handling semantics (RO visible, RW protected) in `moonmind/workflows/agent_queue/models.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-007, DOC-REQ-011)
- [X] T013 [US1] Ensure runtime image includes required terminal transport dependencies in `api_service/Dockerfile` (DOC-REQ-015)

**Checkpoint**: P1 live observation flow is operational and independently testable.

---

## Phase 4: User Story 2 - Perform Controlled Operator Intervention (Priority: P2)

**Goal**: Operators can pause/resume/takeover, grant temporary RW access, and submit operator messages with auditable records.

**Independent Test**: Pause a running task, grant RW for bounded TTL, send operator message, and resume successfully.

### Tests for User Story 2

- [X] T014 [P] [US2] Add worker control-flow tests for pause checkpoint behavior and heartbeat control reads in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-005, DOC-REQ-009, DOC-REQ-016)
- [X] T015 [P] [US2] Add API/service tests for control actions and operator message persistence in `tests/unit/api/routers/test_task_runs.py` and `tests/unit/workflows/agent_queue/test_service_hardening.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-003, DOC-REQ-016)

### Implementation for User Story 2

- [X] T016 [US2] Implement control action handling (`pause`/`resume`/`takeover`) and liveControl payload updates in `moonmind/workflows/agent_queue/service.py` and `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-005, DOC-REQ-009)
- [X] T017 [US2] Implement worker pause checkpoints and cancellation-safe control polling in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-009)
- [X] T018 [US2] Implement RW grant/revoke session behavior and control-event audit metadata in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-011)
- [X] T019 [US2] Implement operator message endpoint handling and event fan-out in `api_service/api/routers/task_runs.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-006, DOC-REQ-010)

**Checkpoint**: P2 intervention and audit flows are operational and independently testable.

---

## Phase 5: User Story 3 - Maintain Secure, Reliable Fallback Behavior (Priority: P3)

**Goal**: Dashboard surfaces secure controls, and failure scenarios degrade gracefully to headless execution.

**Independent Test**: UI can drive live-session controls and failure-mode runs continue while reporting `error` lifecycle state.

### Tests for User Story 3

- [X] T020 [P] [US3] Add dashboard runtime-config tests for live-session endpoint exposure in `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-014, DOC-REQ-016)
- [X] T021 [P] [US3] Add settings/env parsing coverage for live-session knobs in `tests/unit/config/test_settings.py` and worker config parsing checks in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-012, DOC-REQ-016)

### Implementation for User Story 3

- [X] T022 [US3] Implement queue detail Live Session card and action handlers in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-014)
- [X] T023 [US3] Wire dashboard queue source config to task-run live endpoints in `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-014)
- [X] T024 [US3] Implement worker failure-mode reporting for missing/unavailable tmate while preserving task execution in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-013)
- [X] T025 [US3] Enforce allow-web + RW-reveal policy in service/worker response behavior via `moonmind/workflows/agent_queue/service.py` and `moonmind/agents/codex_worker/worker.py` (DOC-REQ-011, DOC-REQ-012)

**Checkpoint**: P3 security/reliability/dashboard behavior is operational and independently testable.

---

## Phase 6: Polish & Cross-Cutting Validation

**Purpose**: Final verification, scope gates, and DOC-REQ coverage confirmation.

- [X] T026 Run full regression via `./tools/test_unit.sh` and record outcomes in `specs/024-live-task-handoff/quickstart.md` (DOC-REQ-016)
- [X] T027 Run runtime tasks scope gate via `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` (DOC-REQ-016)
- [X] T028 Run runtime diff scope gate via `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` (DOC-REQ-016)
- [X] T029 Verify each `DOC-REQ-001..016` maps to at least one implementation and one validation task in `specs/024-live-task-handoff/tasks.md` and `specs/024-live-task-handoff/contracts/requirements-traceability.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015, DOC-REQ-016)

---

## Dependencies & Execution Order

- Phase 1 must complete before Phase 2 foundational work.
- Phase 2 foundational primitives are required before story implementations.
- US1 is MVP and should land first.
- US2 depends on control/session primitives from Phase 2 and worker runtime paths from US1.
- US3 depends on API/service/worker behavior from US1/US2 and can proceed once endpoints are stable.
- Polish tasks run after all implementation/testing tasks.

## Parallel Opportunities

- T002 can run in parallel with T001.
- T004 and T008 can run in parallel once migration scope is defined.
- T009 and T010 can run in parallel with staged implementation for US1.
- T014 and T015 can run in parallel in US2.
- T020 and T021 can run in parallel in US3.

## Implementation Strategy

1. Deliver MVP live observation first (US1).
2. Add intervention controls and operator messaging (US2).
3. Complete secure/failure/dashboard hardening (US3).
4. Run full validation and scope gates before finalizing.
