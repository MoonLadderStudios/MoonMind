# Tasks: Live Logs Phase 6 Compatibility and Cleanup

**Input**: Design documents from `/specs/144-live-logs-phase6/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing tests before implementing the corresponding runtime/frontend changes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

## Phase 1: Setup

- [x] T001 [P] Add failing rollout-scope runtime-config coverage in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [x] T002 [P] [US1] Add failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` for `codex_managed`, `all_managed`, and disabled rollout eligibility
- [x] T003 [P] [US2] Add failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` for empty structured-history fallback to `/logs/merged`
- [x] T004 [P] [US2] Add failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` for camelCase, snake_case, and minimal SSE/history observability payload compatibility

## Phase 2: Foundational

- [x] T005 Implement centralized Live Logs rollout feature helpers in `api_service/api/routers/task_dashboard_view_model.py`
- [x] T006 Implement compatibility-aware timeline eligibility and observability-event normalization helpers in `frontend/src/entrypoints/task-detail.tsx`

## Phase 3: User Story 1 - Respect rollout scope when enabling the session timeline (Priority: P1)

**Goal**: Turn on the session-aware viewer only for runs that are inside the configured rollout boundary.

### Implementation for User Story 1

- [x] T007 [US1] Update `frontend/src/entrypoints/task-detail.tsx` to select the session-aware or legacy Live Logs viewer from rollout scope plus run context

## Phase 4: User Story 2 - Degrade cleanly across mixed observability payloads and empty history (Priority: P1)

**Goal**: Keep Live Logs readable for historical runs and mixed frontend/backend deploy windows.

### Implementation for User Story 2

- [x] T008 [US2] Update `frontend/src/entrypoints/task-detail.tsx` to fall back to `/logs/merged` when structured history is unavailable or empty
- [x] T009 [US2] Update `frontend/src/entrypoints/task-detail.tsx` to normalize camelCase, snake_case, and minimal observability payloads for both history and SSE

## Phase 5: User Story 3 - Remove remaining legacy-only assumptions from the Live Logs viewer path (Priority: P2)

**Goal**: Consolidate the compatibility logic into one maintainable viewer path instead of scattered legacy assumptions.

### Implementation for User Story 3

- [x] T010 [US3] Refactor `frontend/src/entrypoints/task-detail.tsx` so rollout eligibility, event normalization, and fallback intent flow through one compatibility-aware helper path

## Phase 6: Validation

- [x] T011 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
- [x] T012 Run `npm run ui:typecheck`
- [x] T013 Run `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- [x] T014 Run `SPECIFY_FEATURE=144-live-logs-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [x] T015 Run `SPECIFY_FEATURE=144-live-logs-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
- [x] T016 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on the failing tests from Phase 1.
- **User Story 1 (Phase 3)**: Depends on Phase 2.
- **User Story 2 (Phase 4)**: Depends on Phase 2.
- **User Story 3 (Phase 5)**: Depends on Phase 3 and Phase 4.
- **Validation (Phase 6)**: Depends on all implementation work.

### Parallel Opportunities

- `T001` through `T004` can be written in parallel because they touch separate test targets or separate test cases.
- `T007` and `T008` can be staged sequentially on the same frontend file once the shared helpers from `T006` exist.

## Implementation Strategy

### MVP First

1. Add the failing runtime-config and browser tests.
2. Land the shared rollout/helper changes.
3. Enable rollout-aware viewer selection.
4. Finish empty-history fallback and payload alias compatibility.
5. Consolidate the compatibility path and run validation.
