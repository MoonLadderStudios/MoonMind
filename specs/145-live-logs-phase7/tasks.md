# Tasks: Live Logs Phase 7 Hardening and Rollback

**Input**: Design documents from `/specs/145-live-logs-phase7/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing tests before implementing the corresponding runtime/frontend changes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

## Phase 1: Setup

- [X] T001 Create the Phase 7 spec artifact set in `specs/145-live-logs-phase7/`.

## Phase 2: User Story 1 - Observe Live Logs surface health operationally (Priority: P1)

**Goal**: Emit best-effort metrics for the summary, structured-history, and stream router surfaces.

### Tests for User Story 1

- [X] T002 [P] [US1] Add failing router metric tests in `tests/unit/api/routers/test_task_runs.py` for summary latency, history latency/source, and history error emission.

### Implementation for User Story 1

- [X] T003 [US1] Update `api_service/api/routers/task_runs.py` to instrument summary/history metrics while preserving existing SSE metrics and route responses.

## Phase 3: User Story 2 - Protect structured history with the same ownership rules as other observability surfaces (Priority: P1)

**Goal**: Add explicit owner-versus-cross-owner regression coverage for `/observability/events`.

### Tests for User Story 2

- [X] T004 [P] [US2] Add failing owner-access and cross-owner rejection tests in `tests/unit/api/routers/test_task_runs.py` for `/api/task-runs/{id}/observability/events`.

### Implementation for User Story 2

- [X] T005 [US2] Update `api_service/api/routers/task_runs.py` as needed so metrics and access control remain correct for authorized and rejected `/observability/events` requests.

## Phase 4: User Story 3 - Roll back the structured-history timeline path without breaking Live Logs (Priority: P1)

**Goal**: Expose and honor a dedicated structured-history rollback flag in dashboard config and the task-detail page.

### Tests for User Story 3

- [X] T006 [P] [US3] Add failing feature-flag tests in `tests/unit/config/test_settings.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` for `live_logs_structured_history_enabled`.
- [X] T007 [P] [US3] Add failing browser tests in `frontend/src/entrypoints/task-detail.test.tsx` covering disabled structured-history fetch and merged-tail-first fallback.

### Implementation for User Story 3

- [X] T008 [US3] Update `moonmind/config/settings.py` and `api_service/api/routers/task_dashboard_view_model.py` to expose `liveLogsStructuredHistoryEnabled`.
- [X] T009 [US3] Update `frontend/src/entrypoints/task-detail.tsx` to skip `/observability/events` and load merged history directly when the rollback flag is disabled.

## Phase 5: Validation

- [X] T010 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
- [X] T011 Run `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py`
- [X] T012 Run `SPECIFY_FEATURE=145-live-logs-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T013 Run `SPECIFY_FEATURE=145-live-logs-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **User Story 1 (Phase 2)**: Depends on Setup.
- **User Story 2 (Phase 3)**: Can follow the same router test baseline as User Story 1.
- **User Story 3 (Phase 4)**: Depends on Setup and can proceed in parallel with router work until implementation touches the shared config/frontend files.
- **Validation (Phase 5)**: Depends on all implementation work.

### Parallel Opportunities

- `T002`, `T004`, `T006`, and `T007` can be written in parallel because they touch different test targets.
- `T003` and `T008` can proceed independently after the failing tests are in place.

## Implementation Strategy

### MVP First

1. Add the failing router, config, and browser tests.
2. Land the backend instrumentation and owner-access-safe metrics behavior.
3. Expose the structured-history rollback flag through config.
4. Honor the flag in the Live Logs browser lifecycle.
5. Run focused verification and scope validation.
