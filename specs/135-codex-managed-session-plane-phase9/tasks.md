# Tasks: Codex Managed Session Plane Phase 9

**Input**: Design documents from `/specs/135-codex-managed-session-plane-phase9/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/task-run-artifact-session-projection.md

**Tests**: TDD is required for this phase. Add or update failing tests before implementation.

**Organization**: Tasks are grouped by user story to keep the minimal projection API independently testable.

## Phase 1: Setup

**Purpose**: Lock the MVP projection contract and add the first failing tests.

- [X] T001 [P] Record the API contract in `specs/135-codex-managed-session-plane-phase9/contracts/task-run-artifact-session-projection.md`
- [X] T002 [P] Add failing projection endpoint tests in `tests/unit/api/routers/test_task_runs.py`

---

## Phase 2: Foundational

**Purpose**: Add the typed response models and reusable projection helpers before story-specific route behavior.

- [X] T003 [P] Add session projection response models in `moonmind/schemas/temporal_artifact_models.py`
- [X] T004 [P] Add managed-session store and projection helper utilities in `api_service/api/routers/task_runs.py`

**Checkpoint**: Typed models and projection helpers exist; user-story route work can proceed.

---

## Phase 3: User Story 1 - Read a durable session continuity projection (Priority: P1) 🎯 MVP

**Goal**: Return one task-scoped session projection from durable session state and artifact metadata only.

**Independent Test**: Call the endpoint in `tests/unit/api/routers/test_task_runs.py` and verify it returns the latest task/session identity, latest epoch, and continuity refs without any live session dependency.

### Tests for User Story 1

- [X] T005 [P] [US1] Add success-path projection assertions in `tests/unit/api/routers/test_task_runs.py`
- [X] T006 [P] [US1] Add durable-only/no-live-container assertions in `tests/unit/api/routers/test_task_runs.py`

### Implementation for User Story 1

- [X] T007 [US1] Implement `GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}` in `api_service/api/routers/task_runs.py`
- [X] T008 [US1] Build the projection from `ManagedSessionStore` and artifact metadata lookups in `api_service/api/routers/task_runs.py`

---

## Phase 4: User Story 2 - Group continuity artifacts server-side (Priority: P1)

**Goal**: Return stable server-defined runtime and continuity/control groups plus latest refs so the UI does not infer continuity locally.

**Independent Test**: Verify the endpoint groups runtime, continuity, and control artifacts correctly and resolves the latest summary/checkpoint/control refs from the same persisted artifacts.

### Tests for User Story 2

- [X] T009 [P] [US2] Add grouped-artifact assertions in `tests/unit/api/routers/test_task_runs.py`
- [X] T010 [P] [US2] Add reset-boundary and latest-ref assertions in `tests/unit/api/routers/test_task_runs.py`

### Implementation for User Story 2

- [X] T011 [US2] Return grouped runtime and continuity/control artifacts in `api_service/api/routers/task_runs.py`
- [X] T012 [US2] Surface `latest_summary_ref`, `latest_checkpoint_ref`, `latest_control_event_ref`, and reset-boundary visibility in `api_service/api/routers/task_runs.py`

---

## Phase 5: User Story 3 - Enforce projection ownership and missing-session behavior (Priority: P2)

**Goal**: Keep the new endpoint aligned with existing task-run access control and stable missing-resource semantics.

**Independent Test**: Verify owner success, non-owner `403`, and missing-session `404` responses.

### Tests for User Story 3

- [X] T013 [P] [US3] Add owner/non-owner access tests in `tests/unit/api/routers/test_task_runs.py`
- [X] T014 [P] [US3] Add missing or mismatched session-task error tests in `tests/unit/api/routers/test_task_runs.py`

### Implementation for User Story 3

- [X] T015 [US3] Enforce task-run ownership checks for session projections in `api_service/api/routers/task_runs.py`
- [X] T016 [US3] Return stable `session_projection_not_found` errors for missing or mismatched task/session pairs in `api_service/api/routers/task_runs.py`

---

## Phase 6: Polish & Validation

**Purpose**: Run focused verification, scope validation, and the full unit suite.

- [X] T017 [P] Run `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py`
- [X] T018 [P] Run `SPECIFY_FEATURE=135-codex-managed-session-plane-phase9 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T019 [P] Run `SPECIFY_FEATURE=135-codex-managed-session-plane-phase9 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
- [X] T020 [P] Run `./tools/test_unit.sh`

## Dependencies & Execution Order

- Phase 1 must complete before implementation starts.
- Phase 2 depends on Phase 1 and blocks all user stories.
- User Stories 1 and 2 share the same endpoint and should proceed sequentially after Phase 2.
- User Story 3 depends on the endpoint existing but can add access/error handling after the success path is in place.
- Phase 6 depends on all implementation tasks being complete.

## Implementation Strategy

### MVP First

1. Add failing tests for the projection endpoint.
2. Add response models and helper utilities.
3. Implement the success-path projection from durable state only.
4. Add grouped-artifact behavior and latest refs.
5. Add access control and missing-session handling.
6. Run focused tests, scope gates, and the full unit suite.

### Parallel Opportunities

- `T001` and `T002` can run in parallel.
- `T003` and `T004` can run in parallel.
- Story-phase tests marked `[P]` can be added together before the corresponding implementation tasks.
- Validation commands in Phase 6 can be run independently once code and tests are complete.
