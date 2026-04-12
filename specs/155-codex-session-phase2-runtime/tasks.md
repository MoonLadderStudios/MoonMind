# Tasks: Codex Session Phase 2 Runtime Behaviors

**Input**: Design documents from `/specs/155-codex-session-phase2-runtime/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/managed-session-phase2-controls.md, quickstart.md
**Tests**: Required. The feature request explicitly requires test-driven development and validation tests.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other marked tasks in the same phase because it touches different files or only adds independent tests.
- **[Story]**: Maps to user stories from `spec.md`.
- Every task includes a concrete repository path.

## Phase 1: Setup (Shared Context)

**Purpose**: Confirm the active runtime surfaces and make the task branch ready for implementation.

- [ ] T001 Review existing managed-session workflow control handlers in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T002 [P] Review existing session activity wrappers and route policies in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/temporal/activity_catalog.py`
- [ ] T003 [P] Review existing Docker controller behavior in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [ ] T004 [P] Review existing container-side Codex session runtime behavior in `moonmind/workflows/temporal/runtime/codex_session_runtime.py`
- [ ] T005 [P] Review current unit coverage in `tests/unit/workflows/temporal/workflows/test_agent_session.py`, `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`, and `tests/unit/services/temporal/runtime/test_managed_session_controller.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared validation scaffolding and route metadata needed by all control stories.

**CRITICAL**: No user story implementation should begin until these shared prerequisites are complete.

- [ ] T006 [P] Add shared fake Codex App Server steering recording support in `tests/helpers/codex_session_runtime.py`
- [ ] T007 [P] Add activity catalog expectations for heartbeat-enabled session controls in `tests/unit/workflows/temporal/test_activity_runtime.py`
- [ ] T008 Add heartbeat timeout policy for `agent_runtime.steer_turn`, `agent_runtime.interrupt_turn`, `agent_runtime.clear_session`, and `agent_runtime.terminate_session` in `moonmind/workflows/temporal/activity_catalog.py`
- [ ] T009 Add heartbeat wrapping for blocking session control activities in `moonmind/workflows/temporal/activity_runtime.py`
- [ ] T010 Add permanent session-control failure classification tests for invalid input, stale locator, and unsupported control-state activity failures in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [ ] T011 Implement permanent failure classification for invalid/stale session-control requests in `moonmind/workflows/temporal/activity_runtime.py`

**Checkpoint**: Activity route policies and shared failure/heartbeat behavior are ready for story work.

---

## Phase 3: User Story 1 - Terminating a Managed Session Cleans Up Runtime Resources (Priority: P1) MVP

**Goal**: `TerminateSession` removes the session container or treats it as already removed, finalizes supervision, clears active turn state, and only then allows workflow completion.

**Independent Test**: Invoke termination with runtime handles and verify cleanup activity invocation, final state application, duplicate terminate safety, and cleanup failure visibility.

### Tests for User Story 1

- [ ] T012 [P] [US1] Add workflow termination cleanup ordering tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T013 [P] [US1] Add controller terminate finalization and duplicate terminate tests in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- [ ] T014 [P] [US1] Add parent workflow termination regression tests in `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`

### Implementation for User Story 1

- [ ] T015 [US1] Update `TerminateSession` workflow behavior in `moonmind/workflows/temporal/workflows/agent_session.py` so cleanup failures are not swallowed and completion readiness is set only after cleanup confirmation
- [ ] T016 [US1] Update controller termination idempotency and supervision finalization behavior in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [ ] T017 [US1] Update container-side terminate state persistence in `moonmind/workflows/temporal/runtime/codex_session_runtime.py`
- [ ] T018 [US1] Run focused termination validation with `python -m pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Canceling a Session Stops Work Without Destroying Continuity (Priority: P1)

**Goal**: `CancelSession` stops active work and preserves session identity, container, and thread for later recovery or termination.

**Independent Test**: Invoke cancellation with and without an active turn and verify the session remains non-terminal while active work is stopped when present.

### Tests for User Story 2

- [ ] T019 [P] [US2] Add workflow cancel-vs-terminate tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T020 [P] [US2] Add controller duplicate interrupt, stale locator, unsupported state, and failed interrupt preservation tests in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`

### Implementation for User Story 2

- [ ] T021 [US2] Update `CancelSession` workflow behavior in `moonmind/workflows/temporal/workflows/agent_session.py` to interrupt active turns without setting termination readiness
- [ ] T022 [US2] Update controller interrupt idempotency to return durable idle/interrupted state only when the record proves the turn is already stopped in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [ ] T023 [US2] Run focused cancellation validation with `python -m pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`

**Checkpoint**: User Story 2 is independently functional and testable.

---

## Phase 5: User Story 3 - Steering an Active Turn Works End to End (Priority: P1)

**Goal**: `SteerTurn` reaches the Codex runtime turn protocol and no longer returns a hardcoded unsupported result.

**Independent Test**: Invoke steering for an active turn and verify steering input reaches the fake Codex App Server protocol, runtime state persists, workflow state updates, and invalid steering requests fail deterministically.

### Tests for User Story 3

- [ ] T024 [P] [US3] Add container-runtime steering protocol tests in `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`
- [ ] T025 [P] [US3] Add workflow `SteerTurn` state update tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T026 [P] [US3] Add controller steering observability/state tests in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`

### Implementation for User Story 3

- [ ] T027 [US3] Implement real `steer_turn` runtime protocol behavior in `moonmind/workflows/temporal/runtime/codex_session_runtime.py`
- [ ] T028 [US3] Update controller steering response persistence in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [ ] T029 [US3] Ensure workflow `SteerTurn` applies returned session state and latest control metadata in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T030 [US3] Run focused steering validation with `python -m pytest -q tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`

**Checkpoint**: User Story 3 is independently functional and testable.

---

## Phase 6: User Story 4 - Runtime Control Activities Are Safe Under Retry and Cancellation (Priority: P2)

**Goal**: Launch, clear, interrupt, and terminate controls are retry-safe at the activity/controller boundary and cancelable while blocking.

**Independent Test**: Inject duplicate control delivery, stale locators, permanent failures, and blocking activity cancellation; verify durable state stays consistent and invalid inputs do not retry as transient failures.

### Tests for User Story 4

- [ ] T031 [P] [US4] Add duplicate launch, duplicate clear, and stale locator idempotency-boundary tests in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- [ ] T032 [P] [US4] Add activity heartbeat wrapper tests in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [ ] T033 [P] [US4] Add route heartbeat timeout and retry policy tests in `tests/unit/workflows/temporal/test_activity_runtime.py`

### Implementation for User Story 4

- [ ] T034 [US4] Implement launch and clear idempotency guards using durable record proof in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [ ] T035 [US4] Implement terminate and interrupt idempotency guards using durable record proof in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [ ] T036 [US4] Apply heartbeat wrapping and permanent failure classification to control activity methods in `moonmind/workflows/temporal/activity_runtime.py`
- [ ] T037 [US4] Confirm heartbeat timeout and retry policy metadata in `moonmind/workflows/temporal/activity_catalog.py`
- [ ] T038 [US4] Run focused retry/cancellation validation, including stale locator and unsupported-state non-retryable assertions, with `python -m pytest -q tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/temporal/test_activity_runtime.py`

**Checkpoint**: User Story 4 is independently functional and testable.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Validate the complete feature and clean up any cross-story drift.

- [ ] T039 [P] Re-run the explicit validation checklist in `specs/155-codex-session-phase2-runtime/contracts/managed-session-phase2-controls.md` against completed implementation tasks and tests
- [ ] T040 [P] Verify no docs-only scope drift with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [ ] T041 Run focused end-to-end unit validation from `specs/155-codex-session-phase2-runtime/quickstart.md`
- [ ] T042 Run final unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T043 Review final diff for unrelated changes with `git status --short`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1; blocks all user stories.
- **Phase 3 US1**: Depends on Phase 2.
- **Phase 4 US2**: Depends on Phase 2; can run in parallel with US1 after shared heartbeat/failure foundations exist.
- **Phase 5 US3**: Depends on Phase 2; can run in parallel with US1/US2 after fake app-server steering support exists.
- **Phase 6 US4**: Depends on Phase 2 and should be reconciled after US1/US2/US3 because it hardens shared retry/cancellation behavior.
- **Phase 7 Polish**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 Termination cleanup**: MVP. No dependency on other user stories after foundation.
- **US2 Non-destructive cancel**: No dependency on US1 after foundation, but must not conflict with termination state machine changes.
- **US3 Real steering**: No dependency on US1/US2 after foundation, but shares workflow state update helpers.
- **US4 Retry/cancellation safety**: Depends on the control behaviors from US1/US2/US3 being stable enough to harden.

### Within Each User Story

- Tests must be written and fail before implementation.
- Workflow/controller/runtime behavior changes should follow test tasks.
- Focused validation must pass before marking the story checkpoint complete.

## Parallel Opportunities

- T002, T003, T004, and T005 can run in parallel after T001.
- T006 and T007 can run in parallel before T008-T011.
- US1 tests T012-T014 can run in parallel.
- US2 tests T019-T020 can run in parallel.
- US3 tests T024-T026 can run in parallel.
- US4 tests T031-T033 can run in parallel.
- After Phase 2, US1, US2, and US3 can proceed in parallel if edit ownership is split carefully:
  - US1 owns termination paths in `agent_session.py`, `managed_session_controller.py`, and terminate tests.
  - US2 owns cancel/interrupt paths in `agent_session.py`, `managed_session_controller.py`, and cancel/interrupt tests.
  - US3 owns steering paths in `codex_session_runtime.py`, `managed_session_controller.py`, `agent_session.py`, and steering tests.

## Parallel Example: User Story 3

```text
Task: "T024 [US3] Add container-runtime steering protocol tests in tests/unit/services/temporal/runtime/test_codex_session_runtime.py"
Task: "T025 [US3] Add workflow SteerTurn state update tests in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "T026 [US3] Add controller steering observability/state tests in tests/unit/services/temporal/runtime/test_managed_session_controller.py"
```

After those tests exist, implementation can proceed in dependency order:

```text
Task: "T027 [US3] Implement real steer_turn runtime protocol behavior in moonmind/workflows/temporal/runtime/codex_session_runtime.py"
Task: "T028 [US3] Update controller steering response persistence in moonmind/workflows/temporal/runtime/managed_session_controller.py"
Task: "T029 [US3] Ensure workflow SteerTurn applies returned session state and latest control metadata in moonmind/workflows/temporal/workflows/agent_session.py"
```

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete US1 termination cleanup tasks T012-T018.
3. Stop and validate termination independently.
4. Use US1 as the safety baseline before adding cancel and steer behavior.

### Incremental Delivery

1. Foundation ready: heartbeat/failure scaffolding in place.
2. US1: termination cannot leak containers or falsely complete after cleanup failure.
3. US2: cancel stops active work without destructive cleanup.
4. US3: steer reaches the runtime protocol.
5. US4: retry/cancellation hardening covers duplicate delivery and blocking activities.
6. Polish: run full quickstart and unit wrapper validation.

### Validation Gates

- Every story has a focused pytest command.
- Runtime scope gate must pass with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- Final verification uses `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.

## Notes

- `[P]` tasks are parallelizable only when workers avoid overlapping writes to the same file.
- User-story labels map to `spec.md` stories for traceability.
- No `DOC-REQ-*` identifiers exist in this feature, so `contracts/requirements-traceability.md` is not required.
- Do not convert this runtime phase into docs-only work; production runtime files and tests are required.
