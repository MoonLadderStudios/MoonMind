# Tasks: Step Ledger Phase 4

**Input**: Design documents from `/specs/141-step-ledger-phase4/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing browser tests before implementing the corresponding task-detail UI behavior.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

## Phase 1: Setup

- [X] T001 Create or extend the Phase 4 task-detail browser-test target in `frontend/src/entrypoints/task-detail.test.tsx`.
- [X] T013 Extend `api_service/api/routers/task_dashboard_view_model.py` so Mission Control runtime config exposes task-run route templates needed by row-scoped observability.
- [X] T014 Add unit coverage in `tests/unit/api/routers/test_task_dashboard_view_model.py` for the task-run runtime-config route templates.

---

## Phase 2: User Story 1 - Steps become the primary task-detail surface (Priority: P1)

**Goal**: Render a Steps-first task-detail page backed by the latest-run step ledger.

### Tests for User Story 1

- [X] T002 [P] [US1] Write failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` covering Steps rendering above Timeline and Artifacts, latest-run-only row rendering, and execution-detail-then-steps fetch ordering.

### Implementation for User Story 1

- [X] T003 [US1] Update `frontend/src/entrypoints/task-detail.tsx` to fetch `/api/executions/{workflowId}/steps`, render a primary Steps section, and keep Timeline/Artifacts secondary.

---

## Phase 3: User Story 2 - Expanded steps attach observability and evidence lazily (Priority: P1)

**Goal**: Expanded rows own step-scoped observability and evidence drilldown.

### Tests for User Story 2

- [X] T004 [P] [US2] Write failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` covering lazy row-level observability fetches, no `/api/task-runs/*` requests for collapsed rows or rows without `taskRunId`, and delayed `taskRunId` attachment on a refreshed latest-run ledger.
- [X] T005 [P] [US2] Write failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` covering expanded-row groups for Summary, Checks, Logs & Diagnostics, Artifacts, and Metadata.

### Implementation for User Story 2

- [X] T006 [US2] Update `frontend/src/entrypoints/task-detail.tsx` to preserve expanded row state by `logicalStepId`, attach observability only for expanded bound rows, and render stable empty-state / delayed-binding copy for unbound rows.

---

## Phase 4: User Story 3 - Steps remain dense and readable across themes (Priority: P2)

**Goal**: Make the new Steps surface fit the existing Mission Control design system.

### Tests for User Story 3

- [X] T007 [P] [US3] Extend `frontend/src/entrypoints/task-detail.test.tsx` with semantic rendering checks for status chips, check badges, and secondary execution-wide artifacts below Steps.

### Implementation for User Story 3

- [X] T008 [US3] Update `frontend/src/styles/mission-control.css` and any related task-detail markup in `frontend/src/entrypoints/task-detail.tsx` for dense step rows, compact check badges, and readable expanded evidence groups in light/dark themes.

---

## Phase 5: Validation

- [X] T009 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T010 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
- [X] T011 Run `npm run ui:typecheck`
- [X] T012 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **User Story 1 (Phase 2)**: Depends on Setup.
- **User Story 2 (Phase 3)**: Depends on User Story 1 fetch/render foundations.
- **User Story 3 (Phase 4)**: Depends on the step-row markup from User Story 1 and the expanded groups from User Story 2.
- **Validation (Phase 5)**: Depends on all implementation work.

### Parallel Opportunities

- T002 can be written before implementation begins.
- T004 and T005 can be written in parallel once the basic row structure is understood.
- T007 can be added while polishing markup/CSS, but before final verification.

## Implementation Strategy

### MVP First

1. Add failing tests for the Steps-first layout and fetch order.
2. Render the latest-run step ledger above Timeline and Artifacts.
3. Move observability attachment into expanded step rows.
4. Polish density/styling and run verification.

### TDD Notes

- Prefer Red → Green for T002 before T003 and T004/T005 before T006.
- If some styling details are not practical to drive entirely from failing tests, add the closest semantic browser assertions first and then tighten the CSS implementation.
