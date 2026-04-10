# Tasks: Codex Managed Session Phase 0 and Phase 1

**Input**: Design documents from `/specs/141-codex-managed-session-phase0-1/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: This feature is explicitly TDD-driven. Write or update failing tests before the corresponding implementation tasks.

## Phase 1: Setup (Failing Tests First)

**Purpose**: Establish failing coverage for the new workflow control contract before runtime code is updated.

- [X] T001 [P] Add workflow tests for `@workflow.init`, typed update names, and validator rejection paths in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T002 [P] Add parent-run workflow routing coverage for `TerminateSession` in `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`
- [X] T003 [P] Add adapter routing coverage for typed session updates in `tests/unit/workflows/adapters/test_codex_session_adapter.py`
- [X] T004 [P] Add task-run router coverage for the updated workflow update names in `tests/unit/api/routers/test_task_runs.py`

---

## Phase 2: Foundational Contract Changes

**Purpose**: Align docs and schema contracts with the intended Phase 0 and Phase 1 surface.

- [X] T005 Update `docs/ManagedAgents/CodexManagedSessionPlane.md` to distinguish operator truth, recovery index, and disposable cache state
- [X] T006 Add typed workflow update request models to `moonmind/schemas/managed_session_models.py`

---

## Phase 3: User Story 1 - Align the canonical doc with the production path (Priority: P1)

**Goal**: Remove ambiguity about production artifact publication and recovery sources.

**Independent Test**: Read the canonical doc and confirm the production artifact publisher and truth surfaces are explicit.

- [X] T007 [US1] Document the controller/supervisor publication path and demote the in-container summary/publication helpers to fallback-only language in `docs/ManagedAgents/CodexManagedSessionPlane.md`

---

## Phase 4: User Story 2 - Expose the workflow’s canonical typed control surface (Priority: P1)

**Goal**: Replace the generic mutating signal surface with typed updates plus validators and wire callers to those update names.

**Independent Test**: Focused workflow/adapter/router tests confirm typed update routing and deterministic validation failures.

- [X] T008 [US2] Refactor `moonmind/workflows/temporal/workflows/agent_session.py` to use `@workflow.init`, typed updates, and update validators
- [X] T009 [US2] Update `moonmind/workflows/adapters/codex_session_adapter.py` session-control propagation coverage to remain aligned with the Phase 1 typed workflow contract
- [X] T010 [US2] Update `moonmind/workflows/temporal/workflows/agent_run.py` and `moonmind/workflows/temporal/workflows/run.py` to target the typed update names
- [X] T011 [US2] Confirm `api_service/api/routers/task_runs.py` continues targeting the current typed workflow update names

---

## Phase 5: Polish & Verification

- [X] T012 Run focused verification in `./.venv/bin/pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/api/routers/test_task_runs.py`
- [X] T013 Run `./tools/test_unit.sh`
