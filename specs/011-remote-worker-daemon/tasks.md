# Tasks: Agent Queue Remote Worker Daemon (Milestone 3)

**Input**: Design documents from `/specs/011-remote-worker-daemon/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare Milestone 3 scaffolding for worker package and test layout.

- [X] T001 Verify branch `011-remote-worker-daemon` and feature artifacts exist in `specs/011-remote-worker-daemon/`.
- [X] T002 Create worker package scaffolding in `moonmind/agents/codex_worker/` with module placeholders (DOC-REQ-002).
- [X] T003 [P] Create worker unit test scaffolding in `tests/unit/agents/codex_worker/`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add configuration, CLI registration, and base worker abstractions required by all user stories.

- [X] T004 Add poetry CLI script `moonmind-codex-worker` in `pyproject.toml` (DOC-REQ-001, DOC-REQ-012).
- [X] T005 Add worker environment config parsing/defaults in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-007).
- [X] T006 Implement queue API client primitives (claim/heartbeat/complete/fail/upload) in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-006).
- [X] T007 [P] Implement startup preflight checks for Codex executable + login status in `moonmind/agents/codex_worker/cli.py` (DOC-REQ-008).
- [X] T008 [P] Implement `codex_exec` payload parsing/validation structures in `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-009).
- [X] T009 Implement shared subprocess and artifact helper utilities for handler execution in `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-010).

**Checkpoint**: Worker CLI, config, queue client, and payload foundations are ready.

---

## Phase 3: User Story 1 - Worker Executes `codex_exec` Jobs (Priority: P1) 🎯 MVP

**Goal**: Remote daemon claims and executes `codex_exec` jobs end-to-end.

**Independent Test**: Worker loop claims a queued job and dispatches to `codex_exec` handler, producing terminal outcome.

### Tests for User Story 1

- [X] T010 [P] [US1] Add daemon polling/claim/dispatch plus worker config default/override unit tests in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-003, DOC-REQ-007, DOC-REQ-012).
- [X] T011 [P] [US1] Add handler unit tests for payload validation and Codex command invocation in `tests/unit/agents/codex_worker/test_handlers.py` (DOC-REQ-004, DOC-REQ-009).

### Implementation for User Story 1

- [X] T012 [US1] Implement daemon run loop with claim polling and job-type dispatch in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-003, DOC-REQ-012).
- [X] T013 [US1] Implement `codex_exec` handler checkout + `codex exec` + log/patch generation in `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-004, DOC-REQ-009, DOC-REQ-010).
- [X] T014 [US1] Implement CLI entrypoint lifecycle and graceful shutdown in `moonmind/agents/codex_worker/cli.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-012).

**Checkpoint**: Worker can run and process `codex_exec` jobs with deterministic local outputs.

---

## Phase 4: User Story 2 - Artifact Upload and Terminal Reporting (Priority: P1)

**Goal**: Worker publishes execution artifacts and marks jobs complete/failed.

**Independent Test**: Processed jobs upload artifacts and submit terminal queue transitions with summary/error details.

### Tests for User Story 2

- [X] T015 [P] [US2] Add worker client tests for artifact upload plus complete/fail API calls in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-005, DOC-REQ-006).
- [X] T016 [P] [US2] Add handler tests for artifact output bundle and publish mode branching in `tests/unit/agents/codex_worker/test_handlers.py` (DOC-REQ-010).

### Implementation for User Story 2

- [X] T017 [US2] Implement artifact upload flow from handler outputs in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-005).
- [X] T018 [US2] Implement terminal success/failure update behavior in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-006).
- [X] T019 [US2] Implement optional publish mode actions (`none|branch|pr`) in `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-010).

**Checkpoint**: Worker publishes outputs and updates queue terminal state correctly.

---

## Phase 5: User Story 3 - Lease Renewal and Recovery Robustness (Priority: P2)

**Goal**: Worker heartbeats renew leases and failure paths remain reclaim-safe.

**Independent Test**: Heartbeats run at lease cadence during execution; on interruption/error, no stale heartbeat loop persists and job reclaim remains possible.

### Tests for User Story 3

- [X] T020 [P] [US3] Add heartbeat cadence/cancellation tests in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-003, DOC-REQ-011).
- [X] T021 [P] [US3] Add CLI preflight tests for missing codex and failed login in `tests/unit/agents/codex_worker/test_cli.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-007, DOC-REQ-008).

### Implementation for User Story 3

- [X] T022 [US3] Implement background heartbeat loop (`leaseSeconds/3`) around handler execution in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-003, DOC-REQ-011).
- [X] T023 [US3] Implement robust error handling that preserves reclaim semantics and stop-heartbeat behavior in `moonmind/agents/codex_worker/worker.py` and `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-006, DOC-REQ-011).

**Checkpoint**: Lease renewal and crash-recovery expectations are satisfied.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize exports/traceability and run validation command.

- [X] T024 [P] Wire package exports and module docs in `moonmind/agents/codex_worker/__init__.py` and `specs/011-remote-worker-daemon/contracts/codex-worker-runtime-contract.md` (DOC-REQ-002).
- [X] T025 [P] Reconcile implementation and tests against `specs/011-remote-worker-daemon/contracts/requirements-traceability.md` (DOC-REQ-001, DOC-REQ-012).
- [ ] T026 Run unit validation via `./tools/test_unit.sh` focusing on new codex worker tests (DOC-REQ-011).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phase 3/4/5 -> Phase 6.
- All user stories depend on foundational tasks T004-T009.
- Polish phase runs after selected user stories are complete.

### User Story Dependencies

- US1 starts after foundational phase.
- US2 depends on US1 handler outputs and queue dispatch behavior.
- US3 depends on US1 loop and US2 terminal/reporting paths.

### Parallel Opportunities

- T003, T007, T008 can run in parallel in setup/foundation.
- T010/T011, T015/T016, and T020/T021 are parallelizable test tasks.
- T024/T025 can run in parallel during polish.

---

## Implementation Strategy

### MVP First (US1)

1. Build CLI + worker loop + `codex_exec` execution path.
2. Validate claim/dispatch and core handler behavior.
3. Expand to artifact publication and robustness.

### Incremental Delivery

1. Add artifact upload + terminal status reporting (US2).
2. Add heartbeat cadence and recovery protections (US3).
3. Run full unit validation and traceability reconciliation.

### Runtime Scope Commitments

- Production runtime files will be modified in `moonmind/agents/codex_worker/` and `pyproject.toml`.
- Validation coverage will be delivered with new unit tests plus execution of `./tools/test_unit.sh`.
