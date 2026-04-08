# Tasks: Step Ledger Phase 3

**Input**: Design documents from `/specs/139-step-ledger-phase3/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing tests before implementing the corresponding route, serialization, and client-surface behavior.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

## Phase 1: Setup

- [X] T001 Create or extend the Phase 3 API test targets in `tests/unit/api/routers/test_executions.py`, `tests/contract/test_temporal_execution_api.py`, and `tests/unit/api/routers/test_task_dashboard_view_model.py`.

---

## Phase 2: User Story 1 - Execution detail exposes bounded progress (Priority: P1)

**Goal**: `GET /api/executions/{workflowId}` returns canonical bounded progress for `MoonMind.Run`.

### Tests for User Story 1

- [X] T002 [P] [US1] Write failing router/contract tests in `tests/unit/api/routers/test_executions.py` and `tests/contract/test_temporal_execution_api.py` covering `ExecutionModel.progress`, null progress for unsupported workflows, and safe degradation when query data is unavailable.

### Implementation for User Story 1

- [X] T003 [US1] Extend `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` so execution detail serializes bounded `progress` and `stepsHref` in the canonical camelCase shape.
- [X] T004 [US1] Add a Temporal client/query helper in `moonmind/workflows/temporal/client.py` and wire `api_service/api/routers/executions.py` to fetch workflow progress for `MoonMind.Run` detail reads.

---

## Phase 3: User Story 2 - Step ledger is available through `/api/executions/{workflowId}/steps` (Priority: P1)

**Goal**: Provide a first-class latest-run step-ledger API route.

### Tests for User Story 2

- [X] T005 [P] [US2] Write failing router/contract tests in `tests/unit/api/routers/test_executions.py` and `tests/contract/test_temporal_execution_api.py` for `GET /api/executions/{workflowId}/steps`, including latest-run `runId` handling and ownership semantics.

### Implementation for User Story 2

- [X] T006 [US2] Implement `GET /api/executions/{workflowId}/steps` in `api_service/api/routers/executions.py` using the canonical workflow query contract and fail-fast validation for unsupported workflow types.

---

## Phase 4: User Story 3 - Compatibility detail surfaces `stepsHref` (Priority: P2)

**Goal**: Keep task-oriented detail bounded while advertising the new steps surface.

### Tests for User Story 3

- [X] T007 [P] [US3] Write failing compatibility/runtime-config tests in `tests/unit/api/routers/test_executions.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` asserting `stepsHref` and the temporal steps endpoint are exposed.

### Implementation for User Story 3

- [X] T008 [US3] Update `api_service/api/routers/task_dashboard_view_model.py` and execution serialization so compatibility consumers receive `stepsHref` plus the configured temporal steps endpoint.
- [X] T009 [US3] Regenerate `frontend/src/generated/openapi.ts` so the checked-in client includes `progress`, `stepsHref`, and `/api/executions/{workflow_id}/steps`.

---

## Phase 5: Validation

- [X] T010 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T011 Run targeted Phase 3 tests via `pytest tests/unit/api/routers/test_executions.py tests/contract/test_temporal_execution_api.py tests/unit/api/routers/test_task_dashboard_view_model.py -q`
- [X] T012 Run `./tools/test_unit.sh`
