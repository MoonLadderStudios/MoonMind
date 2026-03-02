# Tasks: Task Cursor Pagination

**Input**: Design documents from `/specs/043-task-cursor-pagination/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `quickstart.md`, `contracts/`

**Tests**: Runtime validation is required for this feature. Include repository/service/router/dashboard tests plus `./tools/test_unit.sh`.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Prompt B Scope Controls (Step 12/16)

- Runtime mode is mandatory: production runtime code and validation tasks must both be present.
- `DOC-REQ-001` through `DOC-REQ-011` require implementation and validation traceability entries.
- Deterministic updates across `spec.md`, `plan.md`, and `tasks.md` are required for this remediation step.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Lock pagination scope, traceability, and shared scaffolding before foundational coding.

- [X] T001 Confirm runtime validation checkpoints in `specs/043-task-cursor-pagination/quickstart.md` and `specs/043-task-cursor-pagination/checklists/requirements.md` (DOC-REQ-011)
- [X] T002 [P] Refresh `specs/043-task-cursor-pagination/contracts/requirements-traceability.md` with explicit implementation and validation owners for `DOC-REQ-001` through `DOC-REQ-011` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011)
- [X] T003 [P] Add pagination test scaffolding placeholders in `tests/unit/workflows/agent_queue/test_service_pagination.py`, `tests/unit/api/routers/test_agent_queue.py`, and `tests/task_dashboard/test_queue_layouts.js` (DOC-REQ-004, DOC-REQ-011)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared cursor/query/index contracts that all stories rely on.

**CRITICAL**: No user story implementation starts until this phase is complete.

- [X] T004 Implement shared cursor token encode/decode helpers for base64url JSON `(created_at,id)` in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-006, DOC-REQ-007)
- [X] T005 [P] Add list response pagination fields (`page_size`, `next_cursor`) while preserving compatibility metadata in `moonmind/schemas/agent_queue_models.py` (DOC-REQ-004)
- [X] T006 [P] Implement canonical ordering and filtered keyset seek query helpers in `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-007)
- [X] T007 [P] Enforce `limit` default/clamp and reject mixed `cursor` + `offset` inputs in `moonmind/workflows/agent_queue/service.py` and `api_service/api/routers/agent_queue.py` (DOC-REQ-002, DOC-REQ-004)
- [X] T008 Add or update ordering index migration for `(created_at DESC, id DESC)` in `api_service/migrations/versions/202603010001_task_cursor_pagination_indexes.py` (DOC-REQ-008)
  - Verified existing migration `api_service/migrations/versions/202602210001_agent_queue_list_indexes.py` already provides `ix_agent_jobs_created_at_id`; no new migration added to avoid duplicate-index churn.

**Checkpoint**: Cursor/query/index foundations are stable for story implementation.

---

## Phase 3: User Story 1 - Load First Page Reliably (Priority: P1) (MVP)

**Goal**: Return bounded, deterministic first-page task lists with default page size 50.

**Independent Test**: Call `GET /api/tasks` with no pagination params and verify `items` length is `<=50`, ordered `created_at DESC, id DESC`, with valid pagination metadata.

### Tests for User Story 1

- [X] T009 [P] [US1] Add service tests for default `limit=50`, clamp `1..200`, and first-page metadata behavior in `tests/unit/workflows/agent_queue/test_service_pagination.py` (DOC-REQ-002, DOC-REQ-004, DOC-REQ-011)
- [X] T010 [P] [US1] Add repository tests for canonical first-page ordering and same-timestamp id tie-breaks in `tests/unit/workflows/agent_queue/test_repositories.py` (DOC-REQ-003, DOC-REQ-011)
- [X] T011 [P] [US1] Add router tests for `GET /api/tasks` response envelope (`items`, `page_size`, `next_cursor`) and compatibility fields in `tests/unit/api/routers/test_task_dashboard.py` and `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-004, DOC-REQ-011)

### Implementation for User Story 1

- [X] T012 [US1] Implement first-page cursor pagination defaults in queue list service path in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-002)
- [X] T013 [US1] Wire `GET /api/tasks` and `GET /api/queue/jobs` handlers to emit cursor pagination metadata in `api_service/api/routers/task_dashboard.py` and `api_service/api/routers/agent_queue.py` (DOC-REQ-004)
- [X] T014 [US1] Ensure compatibility fallback fields remain populated for existing consumers in `moonmind/schemas/agent_queue_models.py` and `api_service/api/routers/agent_queue.py` (DOC-REQ-004)

**Checkpoint**: User Story 1 is independently functional and validates first-page reliability.

---

## Phase 4: User Story 2 - Navigate Forward Without Gaps or Duplicates (Priority: P2)

**Goal**: Support stable forward navigation using opaque cursors and keyset boundaries.

**Independent Test**: Fetch page 1, request page 2 with `next_cursor`, verify no duplicate IDs across pages and deterministic descending ordering with `next_cursor=null` at the end.

### Tests for User Story 2

- [X] T015 [P] [US2] Add repository/service tests for descending seek predicate, `limit+1` page slicing, and index-backed ordering assumptions in `tests/unit/workflows/agent_queue/test_repositories.py` and `tests/unit/workflows/agent_queue/test_service_pagination.py` (DOC-REQ-005, DOC-REQ-007, DOC-REQ-008, DOC-REQ-011)
- [X] T016 [P] [US2] Add service tests for cursor encode/decode round-trip and malformed cursor rejection in `tests/unit/workflows/agent_queue/test_service_pagination.py` (DOC-REQ-006, DOC-REQ-011)
- [X] T017 [P] [US2] Add router tests for second-page traversal and end-of-results `next_cursor=null` behavior in `tests/unit/api/routers/test_agent_queue.py` and `tests/unit/api/routers/test_task_dashboard.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-007, DOC-REQ-011)

### Implementation for User Story 2

- [X] T018 [US2] Implement filtered keyset pagination execution (`limit+1`, trim-to-limit, `next_cursor`) in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-007)
- [X] T019 [US2] Implement cursor validation error mapping for API clients in `api_service/api/routers/agent_queue.py` and `api_service/api/routers/task_dashboard.py` (DOC-REQ-006)
- [X] T020 [US2] Apply canonical ordering `created_at DESC, id DESC` to all paginated task list paths in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-003)

**Checkpoint**: User Story 2 independently validates stable cursor navigation.

---

## Phase 5: User Story 3 - Combine Filtering and URL-Persisted Pagination (Priority: P3)

**Goal**: Persist pagination state in URL, expose page-size/next controls, and reset cursor on filter changes.

**Independent Test**: Load `/tasks/list` with `limit` and `cursor` query params, confirm state restores; change filters and confirm cursor resets to first page.

### Tests for User Story 3

- [X] T021 [P] [US3] Add dashboard state tests for URL query sync of `limit` and `cursor` plus load-time restoration in `tests/task_dashboard/test_queue_layouts.js` (DOC-REQ-009, DOC-REQ-011)
- [X] T022 [P] [US3] Add dashboard tests for filter/page-size changes resetting cursor stack and returning to first page in `tests/task_dashboard/test_queue_layouts.js` (DOC-REQ-010, DOC-REQ-011)
- [X] T023 [P] [US3] Add router/service tests ensuring filters are applied before pagination seek boundaries in `tests/unit/workflows/agent_queue/test_service_pagination.py` and `tests/unit/api/routers/test_task_dashboard.py` (DOC-REQ-005, DOC-REQ-011)

### Implementation for User Story 3

- [X] T024 [US3] Implement dashboard pagination URL state (`limit`, optional `cursor`) and initial hydration in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-009)
- [X] T025 [US3] Implement page-size selector (`25/50/100`) and next-button gating by `next_cursor` in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-009)
- [X] T026 [US3] Implement filter-change pagination reset behavior (clear cursor and cursor stack) in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-010)
- [X] T027 [US3] Update task dashboard view model pagination payload/state mapping in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/api/routers/task_dashboard.py` (DOC-REQ-004, DOC-REQ-009)

**Checkpoint**: User Story 3 independently validates filter-aware URL-persisted pagination UX.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, scope gates, and traceability closure.

- [X] T028 [P] Reconcile final `DOC-REQ-*` task/test evidence in `specs/043-task-cursor-pagination/contracts/requirements-traceability.md` and `specs/043-task-cursor-pagination/quickstart.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011)
- [X] T029 Run required unit validation via `./tools/test_unit.sh` and record outcomes in `specs/043-task-cursor-pagination/quickstart.md` (DOC-REQ-011)
- [X] T030 Run runtime task-scope validation via `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and record outcome in `specs/043-task-cursor-pagination/quickstart.md` (DOC-REQ-011)
- [ ] T031 [P] Run manual dashboard smoke checks for first page, next page, filter reset, and URL refresh flows; document results in `specs/043-task-cursor-pagination/quickstart.md` (DOC-REQ-001, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: Starts immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user-story work.
- **Phase 3 (US1)**: Depends on Phase 2 and delivers MVP behavior.
- **Phase 4 (US2)**: Depends on Phase 3 list contract baseline.
- **Phase 5 (US3)**: Depends on Phase 3 list contract baseline and can proceed in parallel with late US2 hardening tasks once shared service/router interfaces are stable.
- **Phase 6 (Polish)**: Depends on completion of all targeted user stories.

### User Story Dependencies

- **US1 (P1)**: No dependency on other user stories after foundational completion.
- **US2 (P2)**: Depends on US1 response contract and clamping defaults.
- **US3 (P3)**: Depends on US1 response contract and uses US2 `next_cursor` behavior for UX controls.

### Within Each User Story

- Validation tasks should be authored before implementation and fail before fixes.
- Repository/service behavior should land before router/view-model wiring that depends on it.
- Router/view-model behavior should land before dashboard UI wiring that consumes new fields.

## Parallel Opportunities

- **Setup**: `T002` and `T003` can run in parallel after `T001`.
- **Foundational**: `T005`, `T006`, and `T007` can run in parallel once `T004` cursor contract is defined.
- **US1**: `T009`, `T010`, and `T011` are parallel test tasks; `T013` and `T014` can run in parallel after `T012`.
- **US2**: `T015`, `T016`, and `T017` can run in parallel; `T019` and `T020` can run in parallel after `T018` query pipeline changes.
- **US3**: `T021`, `T022`, and `T023` can run in parallel; `T024`, `T025`, and `T026` can run in parallel after shared URL-state helpers are introduced.
- **Polish**: `T028` and `T031` can run in parallel before final closure tasks.

## Parallel Example: User Story 2

```bash
Task T015: tests/unit/workflows/agent_queue/test_repositories.py + tests/unit/workflows/agent_queue/test_service_pagination.py
Task T016: tests/unit/workflows/agent_queue/test_service_pagination.py
Task T017: tests/unit/api/routers/test_agent_queue.py + tests/unit/api/routers/test_task_dashboard.py
```

## Parallel Example: User Story 3

```bash
Task T021: tests/task_dashboard/test_queue_layouts.js
Task T022: tests/task_dashboard/test_queue_layouts.js
Task T023: tests/unit/workflows/agent_queue/test_service_pagination.py + tests/unit/api/routers/test_task_dashboard.py
```

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 and Phase 2.
2. Deliver Phase 3 (US1) for bounded first-page behavior.
3. Validate US1 independently before expanding scope.

### Incremental Delivery

1. Add US2 keyset traversal and cursor reliability.
2. Add US3 dashboard URL persistence and filter-reset UX.
3. Complete Phase 6 for full validation and traceability.

### Runtime Scope Guard

- Runtime implementation tasks explicitly target `moonmind/`, `api_service/`, and migration paths.
- Validation tasks explicitly target `tests/` and required commands (`./tools/test_unit.sh`, scope validation script).
- Completion is invalid unless runtime implementation and validation tasks are both satisfied.
