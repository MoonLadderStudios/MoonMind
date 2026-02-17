# Tasks: Dashboard Queue Task Default Pre-Population

**Input**: Design documents from `/specs/019-prepopulate-run-defaults/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm feature artifacts and implementation/test targets for defaults behavior.

- [X] T001 Verify implementation references and contracts in `specs/019-prepopulate-run-defaults/spec.md`, `specs/019-prepopulate-run-defaults/plan.md`, and `specs/019-prepopulate-run-defaults/contracts/queue-defaults-contract.md`.
- [X] T002 Capture baseline dashboard default wiring in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/static/task_dashboard/dashboard.js` before edits.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish settings-backed defaults and shared backend resolution paths.

- [X] T003 Implement baseline settings defaults for repository/model/effort in `moonmind/config/settings.py`.
- [X] T004 [P] Document baseline default env configuration values in `.env-template`.
- [X] T005 Implement canonical task payload default-enrichment for missing repository/runtime/model/effort in `moonmind/workflows/agent_queue/service.py`.

**Checkpoint**: Settings and queue service can resolve default runtime parameters even when task submissions omit fields.

---

## Phase 3: User Story 1 - Apply Defaults on Submit (Priority: P1) 🎯 MVP

**Goal**: Omitted queue submission fields resolve to settings defaults (`codex`, `gpt-5.3-codex`, `high`, `MoonLadderStudios/MoonMind`).

**Independent Test**: Submit `type=task` payload with blank repository/model/effort/runtime fields and confirm persisted payload contains resolved defaults.

### Tests for User Story 1

- [X] T006 [P] [US1] Add queue service tests for default resolution of repository/runtime/model/effort omissions and invalid repository format handling in `tests/unit/workflows/agent_queue/test_service_hardening.py`.

### Implementation for User Story 1

- [X] T007 [US1] Finalize queue default-resolution logic, enforce token-free repository reference validation, and guard explicit user overrides in `moonmind/workflows/agent_queue/service.py`.
- [X] T008 [US1] Add/update canonical payload validation coverage for resolved runtime and repository behavior in `tests/unit/workflows/agent_queue/test_task_contract.py`.

**Checkpoint**: Backend queue task creation safely resolves missing fields using settings defaults and preserves explicit overrides.

---

## Phase 4: User Story 2 - Pre-Populate Dashboard Inputs (Priority: P2)

**Goal**: Queue submit form shows settings-derived defaults in runtime/model/effort/repository inputs while allowing per-run edits.

**Independent Test**: Open `/tasks/queue/new`, verify defaults are pre-filled, edit them, submit, and confirm edited values are used.

### Tests for User Story 2

- [X] T009 [P] [US2] Add runtime config tests for default model/effort/repository metadata in `tests/unit/api/routers/test_task_dashboard_view_model.py`.

### Implementation for User Story 2

- [X] T010 [US2] Extend dashboard runtime config with `defaultTaskModel` and `defaultTaskEffort` sourced from settings in `api_service/api/routers/task_dashboard_view_model.py`.
- [X] T011 [US2] Pre-populate queue submit form runtime/model/effort/repository fields, enforce client-side token-free repository reference validation, and keep fields editable in `api_service/static/task_dashboard/dashboard.js`.

**Checkpoint**: Dashboard queue submit inputs render settings defaults and still support per-run adjustment.

---

## Phase 5: User Story 3 - Keep Defaults in Sync with Settings (Priority: P3)

**Goal**: Updating settings defaults updates future dashboard pre-population and backend fallback behavior without code changes.

**Independent Test**: Override default settings values, rebuild runtime config, and verify both UI defaults and service resolution reflect updated settings.

### Tests for User Story 3

- [X] T012 [P] [US3] Add settings tests for new default repository/model/effort baseline and env override behavior in `tests/unit/config/test_settings.py`.
- [X] T013 [P] [US3] Add view-model tests confirming runtime config reflects updated settings values in `tests/unit/api/routers/test_task_dashboard_view_model.py`.

### Implementation for User Story 3

- [X] T014 [US3] Align runtime config fallback behavior and settings-derived repository/runtime defaults in `api_service/api/routers/task_dashboard_view_model.py` and `moonmind/config/settings.py`.

**Checkpoint**: Default values are centrally controlled through settings and consumed consistently by UI + queue submit path.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation and scope checks.

- [X] T015 [P] Run unit validation through `./tools/test_unit.sh`.
- [X] T016 [P] Run implementation scope validation commands using `.specify/scripts/bash/validate-implementation-scope.sh` for tasks and diff checks.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phases 3/4/5 -> Phase 6.
- Foundational tasks T003-T005 block all user stories.

### User Story Dependencies

- US1 depends on foundational defaults in settings + queue service.
- US2 depends on foundational defaults and can proceed after T003-T005.
- US3 depends on US1/US2 baseline behavior for settings-sync verification.

### Parallel Opportunities

- T004 can run in parallel with T003 once settings fields are identified.
- T006 and T009 can run in parallel after foundational implementation starts.
- T012 and T013 can run in parallel for settings/UI synchronization coverage.
- T015 and T016 can run in parallel after implementation completes.

---

## Implementation Strategy

### MVP First (US1)

1. Complete Phase 1 and Phase 2.
2. Deliver US1 backend default resolution and tests.
3. Validate omitted-field submissions resolve correctly.

### Incremental Delivery

1. Add UI pre-population (US2) after backend fallback works.
2. Add settings synchronization coverage and behavior hardening (US3).
3. Run full unit + scope validation gates.

### Runtime Scope Commitments

- Production runtime changes are required in `moonmind/config/settings.py`, `moonmind/workflows/agent_queue/service.py`, `api_service/api/routers/task_dashboard_view_model.py`, and `api_service/static/task_dashboard/dashboard.js`.
- Validation includes targeted unit tests and execution via `./tools/test_unit.sh`.
