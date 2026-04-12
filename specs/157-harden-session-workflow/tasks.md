# Tasks: Harden Session Workflow

**Input**: Design documents from `/specs/157-harden-session-workflow/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required. The feature specification requires production runtime code changes plus validation tests, and validation must cover locking, readiness gating, handler drain, Continue-As-New trigger, and handoff carry-forward.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or has no dependency on incomplete tasks.
- **[Story]**: User story label for story phases only.
- Every task includes an exact file path or validation command path.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the feature scope and existing implementation boundaries before runtime edits.

- [X] T001 Review planned workflow/schema surfaces in specs/157-harden-session-workflow/plan.md and specs/157-harden-session-workflow/contracts/agent-session-workflow-hardening.md
- [X] T002 [P] Inspect existing managed-session schema fields in moonmind/schemas/managed_session_models.py
- [X] T003 [P] Inspect existing `MoonMind.AgentSession` handlers and query state in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T004 [P] Inspect current workflow unit-test fixtures in tests/unit/workflows/temporal/workflows/test_agent_session.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared bounded state support required by all user stories.

**CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 Add bounded carry-forward fields to `CodexManagedSessionWorkflowInput` in moonmind/schemas/managed_session_models.py
- [X] T006 Add latest continuity-ref fields and compact request-tracking state to `CodexManagedSessionSnapshot` in moonmind/schemas/managed_session_models.py
- [X] T007 Add normalization rules for optional locator, continuity-ref, threshold, and request-tracking fields in moonmind/schemas/managed_session_models.py
- [X] T008 Add shared workflow state restore and Continue-As-New payload builder helpers in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T009 [P] Add schema validation coverage for bounded carry-forward fields in tests/unit/workflows/temporal/workflows/test_agent_session.py

**Checkpoint**: Shared schema and workflow state helpers are ready for story-specific behavior.

---

## Phase 3: User Story 1 - Safe Concurrent Session Control (Priority: P1)

**Goal**: Session-control mutators update one coherent managed-session state even when accepted close together.

**Independent Test**: Run the US1 tests in tests/unit/workflows/temporal/workflows/test_agent_session.py and verify that concurrent mutators serialize and final query state reflects a complete ordered outcome.

### Tests for User Story 1

> Write these tests first and confirm they fail before implementing US1 runtime changes.

- [X] T010 [P] [US1] Add async mutator lock serialization test in tests/unit/workflows/temporal/workflows/test_agent_session.py
- [X] T011 [P] [US1] Add coherent continuity-ref query state test after send/interrupt/clear in tests/unit/workflows/temporal/workflows/test_agent_session.py

### Implementation for User Story 1

- [X] T012 [US1] Add workflow-level async lock initialization in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T013 [US1] Wrap `SendFollowUp`, `InterruptTurn`, `SteerTurn`, `ClearSession`, `CancelSession`, and `TerminateSession` async mutators with the workflow lock in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T014 [US1] Persist latest continuity refs into workflow query state after projection refresh in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T015 [US1] Run `python -m pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py` and confirm US1 tests pass

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Deterministic Readiness Handling (Priority: P2)

**Goal**: Runtime-bound accepted controls wait for runtime handles when launch is still completing instead of failing due to missing locator state.

**Independent Test**: Send a runtime-bound update before handles are attached in tests/unit/workflows/temporal/workflows/test_agent_session.py, attach handles during the wait, and verify the update invokes runtime activity with the attached locator.

### Tests for User Story 2

> Write these tests first and confirm they fail before implementing US2 runtime changes.

- [X] T016 [P] [US2] Add pre-handle accepted update readiness test for `SendFollowUp` in tests/unit/workflows/temporal/workflows/test_agent_session.py
- [X] T017 [P] [US2] Add validator regression tests for stale epoch, missing active turn after readiness, duplicate clear, and post-termination mutation in tests/unit/workflows/temporal/workflows/test_agent_session.py
- [X] T018 [P] [US2] Add no-handle terminate regression test in tests/unit/workflows/temporal/workflows/test_agent_session.py

### Implementation for User Story 2

- [X] T019 [US2] Add runtime-handle readiness predicate and wait helper in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T020 [US2] Move locator readiness checks from accepted runtime-bound update validators into update bodies where readiness waits are required in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T021 [US2] Preserve deterministic validator rejection for stale epoch, active-turn absence after handles, duplicate clear, and terminating state in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T022 [US2] Run `python -m pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py` and confirm US2 tests pass

**Checkpoint**: User Story 2 is independently functional and testable.

---

## Phase 5: User Story 3 - Bounded Long-Running Session History (Priority: P3)

**Goal**: Long-lived message-heavy sessions continue as new before history grows without bound while preserving bounded session state.

**Independent Test**: Force a low handoff threshold in tests/unit/workflows/temporal/workflows/test_agent_session.py and verify handler drain plus carry-forward payload before Continue-As-New.

### Tests for User Story 3

> Write these tests first and confirm they fail before implementing US3 runtime changes.

- [X] T023 [P] [US3] Add completion handler-drain test using `workflow.all_handlers_finished` in tests/unit/workflows/temporal/workflows/test_agent_session.py
- [X] T024 [P] [US3] Add Continue-As-New trigger and handler-drain test in tests/unit/workflows/temporal/workflows/test_agent_session.py
- [X] T025 [P] [US3] Add handoff payload carry-forward test for identity, epoch, locator, active turn, control metadata, continuity refs, threshold, and request-tracking state in tests/unit/workflows/temporal/workflows/test_agent_session.py

### Implementation for User Story 3

- [X] T026 [US3] Add main-loop Continue-As-New suggestion and threshold checks in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T027 [US3] Wait for `workflow.all_handlers_finished` before terminal completion and Continue-As-New in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T028 [US3] Carry bounded handoff payload through `CodexManagedSessionWorkflowInput` restoration in moonmind/workflows/temporal/workflows/agent_session.py
- [X] T029 [US3] Run `python -m pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py` and confirm US3 tests pass

**Checkpoint**: User Story 3 is independently functional and testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup across all stories.

- [X] T030 [P] Run `python -m py_compile moonmind/workflows/temporal/workflows/agent_session.py moonmind/schemas/managed_session_models.py tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T031 Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_agent_session.py` for focused repository-wrapper validation
- [X] T032 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit validation
- [X] T033 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and confirm runtime scope validation passes
- [X] T034 Review docs generated for this feature in specs/157-harden-session-workflow/ and ensure no implementation checklist leaked into canonical docs/

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup; blocks all user stories because schema and payload helpers are shared.
- **User Story 1 (Phase 3)**: Depends on Foundational.
- **User Story 2 (Phase 4)**: Depends on Foundational. Can run in parallel with US1 after T005-T009, but must coordinate edits to agent_session.py.
- **User Story 3 (Phase 5)**: Depends on Foundational. Can run in parallel with US1/US2 test design, but implementation must merge with lock/readiness edits in agent_session.py.
- **Polish (Phase 6)**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 Safe Concurrent Session Control**: MVP. No dependency on US2 or US3 after Foundational.
- **US2 Deterministic Readiness Handling**: No dependency on US1 behavior, but both touch update handlers and must be integrated carefully.
- **US3 Bounded Long-Running Session History**: No dependency on US1/US2 tests, but final implementation must preserve their state updates in the handoff payload.

### Within Each User Story

- Tests must be written before implementation tasks in the same story.
- Schema changes precede workflow implementation where payload fields are needed.
- Workflow implementation precedes focused validation command.
- Story checkpoint validation must pass before marking the story complete.

### Parallel Opportunities

- T002, T003, and T004 can run in parallel during setup.
- T009 can run in parallel with T008 after T005-T007 are clear.
- US1 tests T010 and T011 can run in parallel.
- US2 tests T016, T017, and T018 can run in parallel.
- US3 tests T023, T024, and T025 can run in parallel.
- T030 can run before the full wrapper once implementation is complete.

---

## Parallel Example: User Story 1

```bash
Task: "T010 [P] [US1] Add async mutator lock serialization test in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "T011 [P] [US1] Add coherent continuity-ref query state test after send/interrupt/clear in tests/unit/workflows/temporal/workflows/test_agent_session.py"
```

## Parallel Example: User Story 2

```bash
Task: "T016 [P] [US2] Add pre-handle accepted update readiness test for SendFollowUp in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "T017 [P] [US2] Add validator regression tests in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "T018 [P] [US2] Add no-handle terminate regression test in tests/unit/workflows/temporal/workflows/test_agent_session.py"
```

## Parallel Example: User Story 3

```bash
Task: "T023 [P] [US3] Add completion handler-drain test in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "T024 [P] [US3] Add Continue-As-New trigger and handler-drain test in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "T025 [P] [US3] Add handoff payload carry-forward test in tests/unit/workflows/temporal/workflows/test_agent_session.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup.
2. Complete Phase 2 foundational schema and payload helper tasks.
3. Complete Phase 3 for US1.
4. Stop and validate US1 with `python -m pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py`.

### Incremental Delivery

1. Add shared bounded state support.
2. Deliver US1 serialization and coherent query state.
3. Deliver US2 readiness waits and validator regressions.
4. Deliver US3 Continue-As-New handoff.
5. Run focused and full validation.

### Parallel Team Strategy

With multiple implementers:

1. One implementer owns schema and payload helper tasks T005-T009.
2. Story implementers write tests for US1, US2, and US3 in parallel after foundational payload shape is clear.
3. Runtime implementation edits to moonmind/workflows/temporal/workflows/agent_session.py are merged sequentially to avoid lock/readiness/handoff conflicts.

## Notes

- Runtime scope is explicit: production tasks touch moonmind/schemas/managed_session_models.py and moonmind/workflows/temporal/workflows/agent_session.py.
- Validation scope is explicit: test tasks and commands touch tests/unit/workflows/temporal/workflows/test_agent_session.py and `./tools/test_unit.sh`.
- No `DOC-REQ-*` identifiers exist for this feature, so requirements-traceability.md is not required.
