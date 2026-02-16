# Tasks: Thin Dashboard Task UI

**Input**: Design documents from `/specs/017-thin-dashboard-ui/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create dashboard scaffolding and baseline test files.

- [X] T001 Verify `specs/017-thin-dashboard-ui/` artifacts are internally consistent and implementation-ready.
- [X] T002 Create dashboard router module scaffold at `api_service/api/routers/task_dashboard.py`.
- [X] T003 [P] Create dashboard static/template scaffolding at `api_service/templates/task_dashboard.html`, `api_service/static/task_dashboard/dashboard.js`, and `api_service/static/task_dashboard/dashboard.css`.
- [X] T004 [P] Create test scaffolding at `tests/unit/api/routers/test_task_dashboard.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared view-model contracts and route registration required by all user stories.

- [X] T005 Implement normalized dashboard status helpers in `api_service/api/routers/task_dashboard_view_model.py`.
- [X] T006 Implement authenticated dashboard route handlers for `/tasks` and `/tasks/...` in `api_service/api/routers/task_dashboard.py`.
- [X] T007 Register the dashboard router in `api_service/main.py`.
- [X] T008 Inject dashboard runtime config (poll intervals, status maps, endpoint metadata) into `api_service/templates/task_dashboard.html`.

**Checkpoint**: Dashboard routes resolve and render the shell template with runtime config.

---

## Phase 3: User Story 1 - Monitor Active Work Across Systems (Priority: P1) 🎯 MVP

**Goal**: Render consolidated and source list pages that monitor active work across queue/workflow/orchestrator systems.

**Independent Test**: Open `/tasks` and source list routes, verify rows render from source APIs, normalized statuses are displayed, and updates occur during polling.

### Tests for User Story 1

- [X] T009 [P] [US1] Add unit tests for status normalization and source mapping in `tests/unit/api/routers/test_task_dashboard_view_model.py`.
- [X] T010 [P] [US1] Add route tests for `/tasks`, `/tasks/queue`, `/tasks/speckit`, and `/tasks/orchestrator` shell rendering in `tests/unit/api/routers/test_task_dashboard.py`.

### Implementation for User Story 1

- [X] T011 [US1] Implement consolidated active-work fetch and render flow in `api_service/static/task_dashboard/dashboard.js`.
- [X] T012 [US1] Implement queue/speckit/orchestrator list page fetch and render flow in `api_service/static/task_dashboard/dashboard.js`.
- [X] T013 [US1] Implement polling scheduler with visibility pause/resume behavior in `api_service/static/task_dashboard/dashboard.js`.
- [X] T014 [US1] Implement partial-failure per-source error handling and status indicators in `api_service/static/task_dashboard/dashboard.js` and `api_service/static/task_dashboard/dashboard.css`.

**Checkpoint**: Monitoring views are functional and update via polling without full page reload.

---

## Phase 4: User Story 2 - Submit New Queue, Workflow, and Orchestrator Runs (Priority: P1)

**Goal**: Provide submit forms for all three systems with validation feedback and navigation to detail pages on success.

**Independent Test**: Submit one valid payload for each source and verify created IDs and redirect-to-detail behavior; submit invalid payloads and confirm errors preserve form inputs.

### Tests for User Story 2

- [X] T015 [P] [US2] Add route tests for `/tasks/queue/new`, `/tasks/speckit/new`, and `/tasks/orchestrator/new` shell rendering in `tests/unit/api/routers/test_task_dashboard.py`.

### Implementation for User Story 2

- [X] T016 [US2] Implement queue submit form rendering and POST flow in `api_service/static/task_dashboard/dashboard.js`.
- [X] T017 [US2] Implement SpecKit submit form rendering and POST flow in `api_service/static/task_dashboard/dashboard.js`.
- [X] T018 [US2] Implement Orchestrator submit form rendering and POST flow in `api_service/static/task_dashboard/dashboard.js`.
- [X] T019 [US2] Implement submit error handling and form-value retention behavior in `api_service/static/task_dashboard/dashboard.js`.

**Checkpoint**: Submit flows work for all three sources with robust validation feedback.

---

## Phase 5: User Story 3 - Inspect Execution Details, Events, and Artifacts (Priority: P2)

**Goal**: Provide detail pages for queue/workflow/orchestrator including metadata, timeline/event data, and artifacts.

**Independent Test**: Open detail routes for each source and confirm records, timeline/event data, and artifact lists render; verify queue event polling uses incremental cursor.

### Tests for User Story 3

- [X] T020 [P] [US3] Add route tests for `/tasks/queue/{job_id}`, `/tasks/speckit/{run_id}`, and `/tasks/orchestrator/{run_id}` shell rendering in `tests/unit/api/routers/test_task_dashboard.py`.

### Implementation for User Story 3

- [X] T021 [US3] Implement queue detail data, incremental event polling, and artifact list/download-link rendering in `api_service/static/task_dashboard/dashboard.js`.
- [X] T022 [US3] Implement SpecKit detail task/artifact metadata rendering in `api_service/static/task_dashboard/dashboard.js`.
- [X] T023 [US3] Implement Orchestrator detail step/artifact metadata rendering in `api_service/static/task_dashboard/dashboard.js`.
- [X] T024 [US3] Add detail-page layout and artifact/download styling in `api_service/static/task_dashboard/dashboard.css` and `api_service/templates/task_dashboard.html`.

**Checkpoint**: Detail pages support operational diagnosis across all three systems.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize docs/testing and validate implementation scope.

- [X] T025 [P] Update dashboard implementation references in `docs/TaskUiStrategy1ThinDashboard.md` and `docs/TaskUiArchitecture.md` with implemented route details.
- [X] T026 Run unit validation via `./tools/test_unit.sh`.
- [X] T027 Run manual implementation scope validation against tasks and git diff (repository does not include `.specify/scripts/bash/validate-implementation-scope.sh`).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phases 3/4/5 -> Phase 6.
- User stories depend on foundational tasks T005-T008.

### User Story Dependencies

- US1 is MVP and should ship first after foundation.
- US2 depends on shared route/config/runtime shell from US1.
- US3 depends on route and API client primitives delivered in US1.

### Parallel Opportunities

- T003 and T004 are parallelizable.
- T009 and T010 are parallelizable.
- T015 can run in parallel with US2 implementation tasks once submit views exist.
- T020 can run in parallel with US3 implementation tasks.
- T025 can run in parallel with final validation prep.

---

## Implementation Strategy

### MVP First (US1)

1. Complete setup and foundational route/model integration.
2. Deliver consolidated and source list monitoring with polling and partial-failure handling.
3. Validate with unit tests before moving to submit/detail workflows.

### Incremental Delivery

1. Add submit flows (US2) with robust error/retention behavior.
2. Add detail/event/artifact views (US3).
3. Finalize docs and validation tasks.

### Runtime Scope Commitments

- Production runtime code changes will be implemented under `api_service/` (router, template, static client, and registration in `main.py`).
- Validation includes route/model unit tests and execution through `./tools/test_unit.sh`.
