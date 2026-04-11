# Tasks: Codex Managed Session Phase 0 and Phase 1

**Input**: Design documents from `/specs/141-codex-managed-session-phase0-1/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: This feature is explicitly TDD-driven. Write or update failing tests before the corresponding implementation tasks.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Phase 1: Setup (Shared Planning And Validation Harness)

**Purpose**: Establish the shared validation surfaces required before story work begins.

- [ ] T001 [P] Confirm Spec Kit source requirement coverage in `specs/141-codex-managed-session-phase0-1/contracts/requirements-traceability.md`
- [ ] T002 [P] Add the DOC-REQ traceability validation command to `specs/141-codex-managed-session-phase0-1/quickstart.md`
- [ ] T003 [P] Confirm runtime-mode scope validation command in `specs/141-codex-managed-session-phase0-1/plan.md`

---

## Phase 2: Foundational Runtime Contracts (Blocking Prerequisites)

**Purpose**: Define request payloads and validation context shared by both user stories.

**Critical**: No user story implementation should proceed until these schema and contract tasks are complete.

- [ ] T004 [P] Add failing schema tests for `sessionEpoch` requirements on clear/cancel/terminate requests in `tests/unit/schemas/test_managed_session_models.py` for DOC-REQ-002 and DOC-REQ-003
- [ ] T005 Add `sessionEpoch` to clear/cancel/terminate workflow control request models in `moonmind/schemas/managed_session_models.py` for DOC-REQ-002 and DOC-REQ-003
- [ ] T006 [P] Update workflow-control contract details in `specs/141-codex-managed-session-phase0-1/contracts/agent-session-workflow-controls.md`

**Checkpoint**: Shared schemas and contract artifacts are ready for story-specific implementation.

---

## Phase 3: User Story 1 - Align The Canonical Doc With The Production Path (Priority: P1) MVP

**Goal**: Remove ambiguity about production artifact publication and recovery sources.

**Independent Test**: Review the canonical document and automated checks to confirm operator/audit truth, recovery index, disposable cache, and production artifact publisher roles are explicit.

### Tests for User Story 1

- [ ] T007 [P] [US1] Add documentation validation tests for truth surfaces in `tests/unit/docs/test_codex_managed_session_plane.py` covering DOC-REQ-001
- [ ] T008 [P] [US1] Add documentation validation tests for controller/supervisor publication language in `tests/unit/docs/test_codex_managed_session_plane.py` covering DOC-REQ-004 and DOC-REQ-005
- [ ] T009 [P] [US1] Add managed-session controller publication regression coverage in `tests/unit/workflows/temporal/runtime/test_managed_session_controller.py` covering DOC-REQ-004 and DOC-REQ-005

### Implementation for User Story 1

- [ ] T010 [US1] Update `docs/ManagedAgents/CodexManagedSessionPlane.md` to distinguish operator/audit truth, operational recovery index, and disposable cache for DOC-REQ-001
- [ ] T011 [US1] Update `docs/ManagedAgents/CodexManagedSessionPlane.md` to identify controller/supervisor as the only production artifact publisher and demote in-container helpers for DOC-REQ-004 and DOC-REQ-005
- [ ] T012 [US1] Verify managed-session controller publication surfaces remain production-owned in `moonmind/workflows/temporal/runtime/managed_session_controller.py` for DOC-REQ-004 and DOC-REQ-005
- [ ] T013 [US1] Verify recovery and reconciliation still use `ManagedSessionStore` as an operational index in `moonmind/workflows/temporal/runtime/managed_session_controller.py` for DOC-REQ-001

**Checkpoint**: User Story 1 is independently complete when the doc and controller tests confirm production truth and publication roles.

---

## Phase 4: User Story 2 - Expose The Workflow's Canonical Typed Control Surface (Priority: P1)

**Goal**: Replace the generic mutating signal surface with typed Updates plus validators and wire callers to those Update names.

**Independent Test**: Focused workflow, adapter, service, and API tests confirm typed Update routing, deterministic validation failures, and absence of the generic mutating signal.

### Tests for User Story 2

- [ ] T014 [P] [US2] Add workflow initialization and typed Update surface tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py` covering DOC-REQ-002
- [ ] T015 [P] [US2] Add workflow validator tests for missing handles, stale epoch, missing active turn, duplicate clear, and terminating state in `tests/unit/workflows/temporal/workflows/test_agent_session.py` covering DOC-REQ-002 and DOC-REQ-003
- [ ] T016 [P] [US2] Add `InterruptTurn`, `SteerTurn`, `ClearSession`, and `TerminateSession` activity-routing tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py` covering DOC-REQ-002, DOC-REQ-003, and DOC-REQ-004
- [ ] T017 [P] [US2] Add parent workflow termination Update routing tests in `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py` covering DOC-REQ-002 and DOC-REQ-003
- [ ] T018 [P] [US2] Add adapter projection and typed contract routing tests in `tests/unit/workflows/adapters/test_codex_session_adapter.py` covering DOC-REQ-002
- [ ] T019 [P] [US2] Add API and service payload tests in `tests/unit/api/routers/test_task_runs.py` and `tests/unit/workflows/temporal/test_temporal_service.py` covering DOC-REQ-002 and DOC-REQ-003

### Implementation for User Story 2

- [ ] T020 [US2] Refactor `moonmind/workflows/temporal/workflows/agent_session.py` to initialize binding state in `@workflow.init` for DOC-REQ-002
- [ ] T021 [US2] Remove the generic mutating `control_action` signal and keep `attach_runtime_handles` as the state-propagation Signal in `moonmind/workflows/temporal/workflows/agent_session.py` for DOC-REQ-002
- [ ] T022 [US2] Implement typed Update validators and handlers in `moonmind/workflows/temporal/workflows/agent_session.py` for send, interrupt, steer, clear, cancel, and terminate covering DOC-REQ-002 and DOC-REQ-003
- [ ] T023 [US2] Wire `InterruptTurn`, `SteerTurn`, `ClearSession`, and `TerminateSession` to runtime activities and continuity refreshes in `moonmind/workflows/temporal/workflows/agent_session.py` for DOC-REQ-002, DOC-REQ-003, and DOC-REQ-004
- [ ] T024 [US2] Update session projection naming and callback wiring in `moonmind/workflows/adapters/codex_session_adapter.py` and `moonmind/workflows/temporal/workflows/agent_run.py` for DOC-REQ-002
- [ ] T025 [US2] Update parent run termination routing to call `TerminateSession` with the current epoch in `moonmind/workflows/temporal/workflows/run.py` for DOC-REQ-002 and DOC-REQ-003
- [ ] T026 [US2] Update task-run API and Temporal service callers to send typed Update payloads in `api_service/api/routers/task_runs.py` and `moonmind/workflows/temporal/service.py` for DOC-REQ-002 and DOC-REQ-003

**Checkpoint**: User Story 2 is independently complete when typed Update tests pass and no production caller depends on the generic mutating signal.

---

## Phase 5: Polish And Cross-Cutting Verification

**Purpose**: Confirm runtime scope, traceability, and full-suite health.

- [ ] T027 [P] Run focused workflow/API/adapter/schema verification with `pytest tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_task_runs.py tests/unit/schemas/test_managed_session_models.py -q --tb=short`
- [ ] T028 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T029 [P] Run DOC-REQ mapping validation from `specs/141-codex-managed-session-phase0-1/quickstart.md`
- [ ] T030 [P] Run runtime scope validation with `SPECIFY_FEATURE=141-codex-managed-session-phase0-1 .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational Runtime Contracts**: Depends on Phase 1.
- **Phase 3 User Story 1**: Depends on Phase 2; can run in parallel with User Story 2 after shared schemas are ready.
- **Phase 4 User Story 2**: Depends on Phase 2; can run in parallel with User Story 1 after shared schemas are ready.
- **Phase 5 Polish And Cross-Cutting Verification**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1**: Independent after Phase 2. Delivers canonical documentation and controller publication/recovery validation.
- **US2**: Independent after Phase 2. Delivers runtime workflow mutation contract and caller routing.
- **MVP Scope**: US1 plus the foundational schema contract is the smallest useful increment, but full Phase 0/1 completion requires US1 and US2.

### Within Each User Story

- Tests must be added before the corresponding implementation tasks.
- Documentation tests should precede documentation edits.
- Workflow validator tests should precede workflow handler changes.
- Caller-routing tests should precede parent, adapter, API, and service updates.

---

## Parallel Opportunities

- T001, T002, and T003 can run in parallel.
- T004 and T006 can run in parallel before T005.
- T007, T008, and T009 can run in parallel for US1.
- T014 through T019 can run in parallel for US2 because they target separate test files.
- T024, T025, and T026 can run in parallel after T020 through T023 define the workflow contract.
- T027, T029, and T030 can run in parallel after implementation is complete.

---

## Parallel Example: User Story 2

```bash
Task: "Add workflow initialization and typed Update surface tests in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "Add parent workflow termination Update routing tests in tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py"
Task: "Add adapter projection and typed contract routing tests in tests/unit/workflows/adapters/test_codex_session_adapter.py"
Task: "Add API and service payload tests in tests/unit/api/routers/test_task_runs.py and tests/unit/workflows/temporal/test_temporal_service.py"
```

---

## Implementation Strategy

### MVP First

1. Complete Phase 1 setup and Phase 2 foundational schema work.
2. Complete Phase 3 User Story 1 to remove truth-surface ambiguity.
3. Stop and validate US1 independently before workflow mutation work.

### Full Phase 0/1 Delivery

1. Complete Phase 1 and Phase 2.
2. Implement US1 and US2 in priority order, or in parallel after Phase 2 if separate owners are available.
3. Run Phase 5 verification before marking the feature complete.

### Traceability Requirement

Every `DOC-REQ-*` appears in at least one implementation task and at least one validation task:

- `DOC-REQ-001`: T007, T010, T013
- `DOC-REQ-002`: T004, T014, T015, T016, T017, T018, T019, T020, T021, T022, T023, T024, T025, T026
- `DOC-REQ-003`: T004, T005, T015, T016, T017, T019, T022, T023, T025, T026
- `DOC-REQ-004`: T008, T009, T011, T012, T016, T023
- `DOC-REQ-005`: T008, T009, T011, T012
