# Tasks: Live Logs Session Timeline UI

**Input**: Design documents from `/specs/142-live-logs-session-timeline-ui/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing browser tests before implementing the corresponding Live Logs viewer changes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

## Phase 1: Setup

- [X] T001 Create or extend the Phase 4 Live Logs browser-test target in `frontend/src/entrypoints/task-detail.test.tsx`.
- [X] T015 Keep the Live Logs feature-flag payload grouped for dashboard consumers in `api_service/api/routers/task_dashboard_view_model.py`.
- [X] T016 Add unit coverage in `tests/unit/api/routers/test_task_dashboard_view_model.py` for the grouped Live Logs feature flags exposed to the frontend.

---

## Phase 2: User Story 1 - Load the Live Logs panel as a session-aware timeline (Priority: P1)

**Goal**: Keep the shipped panel lifecycle while preferring structured history over merged-text fallback.

### Tests for User Story 1

- [X] T002 [P] [US1] Write failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` covering summary -> structured history -> SSE load order and merged-text fallback when structured history is unavailable.
- [X] T003 [P] [US1] Write failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` covering feature-flag fallback to the legacy line viewer when `liveLogsSessionTimelineEnabled` is false.

### Implementation for User Story 1

- [X] T004 [US1] Update `frontend/src/entrypoints/task-detail.tsx` to consume the session timeline feature flag and preserve the summary -> structured history -> merged fallback -> SSE lifecycle.

---

## Phase 3: User Story 2 - Render session-aware timeline rows and header context (Priority: P1)

**Goal**: Render one unified timeline with a compact session snapshot header and explicit boundary treatment.

### Tests for User Story 2

- [X] T005 [P] [US2] Write failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` covering session snapshot header fields, mixed row kinds, and reset-boundary banner rendering.
- [X] T006 [P] [US2] Write failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` covering approval/publication/session lifecycle row semantics and session metadata derived from history rows.

### Implementation for User Story 2

- [X] T007 [US2] Update `frontend/src/entrypoints/task-detail.tsx` and `frontend/src/styles/mission-control.css` to render the session-aware timeline rows, header badges, and explicit boundary banners.

---

## Phase 4: User Story 3 - Harden the viewer for large logs with virtualization and ANSI rendering (Priority: P2)

**Goal**: Replace the naive row list with the desired viewer baseline from the Live Logs architecture.

### Tests for User Story 3

- [X] T008 [P] [US3] Write failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` covering virtualization markers and ANSI-aware output rendering.

### Implementation for User Story 3

- [X] T009 [US3] Update `package.json`, `package-lock.json`, and `frontend/src/entrypoints/task-detail.tsx` to adopt `react-virtuoso` and `anser` for the timeline viewer.

---

## Phase 5: Validation

- [X] T010 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
- [X] T011 Run `npm run ui:typecheck`
- [X] T012 Run `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T013 Run `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
- [X] T014 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **User Story 1 (Phase 2)**: Depends on Setup.
- **User Story 2 (Phase 3)**: Depends on the history-loading and feature-flag foundations from User Story 1.
- **User Story 3 (Phase 4)**: Depends on the row model from User Story 2.
- **Validation (Phase 5)**: Depends on all implementation work.

### Parallel Opportunities

- T002 and T003 can be written together before implementation begins.
- T005 and T006 can be written in parallel once the row model expectations are clear.
- T008 can be added while the viewer refactor is underway but before final verification.

## Implementation Strategy

### MVP First

1. Add failing tests for lifecycle ordering and feature-flag fallback.
2. Land the feature-flag-aware history-loading behavior.
3. Add the session-aware row/header rendering.
4. Finish with Virtuoso + ANSI rendering and run validation.
