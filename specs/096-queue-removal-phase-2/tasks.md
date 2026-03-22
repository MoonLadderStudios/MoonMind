# Tasks: Collapse Dashboard to Single Source

**Input**: Design documents from `/specs/096-queue-removal-phase-2/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), contracts/

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Verify active branch is `096-queue-removal-phase-2`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

- [x] T002 Update `api_service/api/routers/task_dashboard_view_model.py` to remove `queue` and `orchestrator` references in `build_runtime_config` and `_STATUS_MAPS` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003)
- [x] T003 Simplify `normalize_status` in `api_service/api/routers/task_dashboard_view_model.py` to target Temporal states only (DOC-REQ-004)
- [x] T004 Run unit test on `test_task_dashboard_view_model.py` to validate backend view models (Validation for DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004)

**Checkpoint**: Foundation ready - backend view model configures the frontend for single-source.

---

## Phase 3: User Story 1 - Viewing Tasks on Dashboard (Priority: P1) 🎯 MVP

**Goal**: Users view their tasks on the Mission Control dashboard and see only Temporal-backed tasks.

**Independent Test**: Load the dashboard UI and verify tasks populate via Temporal without error.

### Implementation for User Story 1

- [x] T005 [P] [US1] Remove orchestrator route matching, validation stubs, and state branches in `web/static/js/dashboard.js` (DOC-REQ-005)
- [x] T006 [P] [US1] Remove queue fetcher/renderer code and point all task lists at Temporal endpoints in `web/static/js/dashboard.js` (DOC-REQ-006)
- [x] T007 [P] [US1] Deprecate `source` filter in compatibility endpoints in `api_service/api/routers/task_compatibility.py` (DOC-REQ-007)
- [x] T008 [US1] Validate frontend functionality (Validation for DOC-REQ-005, DOC-REQ-006)
- [x] T009 [US1] Run unit tests covering compatibility router (Validation for DOC-REQ-007)

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently.

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T010 [P] Run full backend unit testing suite `./tools/test_unit.sh`

---

## Dependencies & Execution Order

- **Foundational**: API changes blocks frontend Javascript cleanup.
- **User Story 1**: Follows immediately after Foundational changes.
