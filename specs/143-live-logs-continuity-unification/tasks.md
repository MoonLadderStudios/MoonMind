# Tasks: Live Logs Continuity Unification

**Input**: Design documents from `/specs/143-live-logs-continuity-unification/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing tests before implementing the corresponding backend/frontend changes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`)

## Phase 1: Setup

- [X] T001 Create the Phase 5 spec artifact set for `143-live-logs-continuity-unification`.

## Phase 2: User Story 1 - Open continuity artifacts directly from timeline events (Priority: P1)

**Goal**: Let operators move from a timeline row to the related continuity artifact without leaving the Live Logs flow.

### Tests for User Story 1

- [X] T002 [P] [US1] Add failing backend tests in `tests/unit/api/routers/test_task_runs.py` for synthesized `summary_published`, `checkpoint_published`, `session_cleared`, and `session_reset_boundary` metadata.
- [X] T003 [P] [US1] Add failing frontend tests in `frontend/src/entrypoints/task-detail.test.tsx` covering inline artifact links for publication and clear/reset rows.

### Implementation for User Story 1

- [X] T004 [US1] Update `api_service/api/routers/task_runs.py` to preserve specific artifact-ref metadata for synthesized historical session events.
- [X] T005 [US1] Update `frontend/src/entrypoints/task-detail.tsx` and `frontend/src/styles/mission-control.css` to render inline artifact links in session/publication/boundary timeline rows.

## Phase 3: User Story 2 - Explain timeline vs continuity drill-down clearly (Priority: P1)

**Goal**: Make the task detail page read as one observability model with different surfaces for event history and durable evidence.

### Tests for User Story 2

- [X] T006 [P] [US2] Add failing frontend tests in `frontend/src/entrypoints/task-detail.test.tsx` covering the new Live Logs and Session Continuity explanatory copy.

### Implementation for User Story 2

- [X] T007 [US2] Update `frontend/src/entrypoints/task-detail.tsx` to add the operator-facing copy for timeline history vs continuity drill-down.

## Phase 4: Validation

- [X] T008 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
- [X] T009 Run `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- [X] T010 Run `SPECIFY_FEATURE=143-live-logs-continuity-unification ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T011 Run `SPECIFY_FEATURE=143-live-logs-continuity-unification ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
