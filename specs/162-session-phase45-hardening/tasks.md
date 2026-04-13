# Tasks: Codex Managed Session Phase 4/5 Hardening

**Input**: Design documents from `/specs/162-session-phase45-hardening/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/managed-session-phase45-contract.md`, `quickstart.md`  
**Tests**: Required. Runtime mode requires production runtime code changes plus validation tests. Use TDD: add or update automated tests before implementation tasks for each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or independent assertions
- **[Story]**: User story mapping from `spec.md` (`US1`, `US2`, `US3`, `US4`)
- Every task names exact file paths
- No `DOC-REQ-*` identifiers exist for this feature, so no requirements-traceability task is required

---

## Phase 1: Setup

**Purpose**: Establish the implementation baseline and prevent duplicate work for already-complete Phase 4/5 behavior.

- [X] T001 Review current runtime contract, scope guard, and validation commands in `specs/162-session-phase45-hardening/spec.md`, `specs/162-session-phase45-hardening/plan.md`, `specs/162-session-phase45-hardening/contracts/managed-session-phase45-contract.md`, and `specs/162-session-phase45-hardening/quickstart.md`
- [X] T002 [P] Inventory existing managed-session workflow visibility, control updates, Continue-As-New, and activity-summary behavior in `moonmind/workflows/temporal/workflows/agent_session.py`, `moonmind/workflows/temporal/workflows/run.py`, and `moonmind/workflows/temporal/workflows/agent_run.py`
- [X] T003 [P] Inventory existing runtime reconcile, schedule, worker-routing, controller, and supervisor behavior in `moonmind/workflows/temporal/activity_catalog.py`, `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/temporal/client.py`, `moonmind/workflows/temporal/workers.py`, `moonmind/workflows/temporal/worker_runtime.py`, `moonmind/workflows/temporal/runtime/managed_session_controller.py`, and `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`
- [X] T004 [P] Inventory existing Phase 4/5 validation coverage in `tests/unit/workflows/temporal/workflows/test_agent_session.py`, `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, `tests/unit/workflows/temporal/test_client_schedules.py`, `tests/unit/workflows/temporal/test_temporal_workers.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, `tests/unit/services/temporal/runtime/test_managed_session_controller.py`, and `tests/integration/services/temporal/workflows/`

---

## Phase 2: Foundational

**Purpose**: Shared test helpers, safety assertions, and routing checks that all user stories depend on.

**CRITICAL**: No user story implementation should begin until this phase confirms what is missing versus already complete.

- [X] T005 Add or update forbidden-value assertion helpers for prompts, transcripts, scrollback, raw logs, credentials, and raw errors in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T006 [P] Add or update bounded reconcile-output assertion helpers in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [X] T007 [P] Add or update managed-session runtime routing assertions in `tests/unit/workflows/temporal/test_temporal_workers.py` and `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [X] T008 Record the existing-complete versus missing Phase 4/5 implementation findings in `specs/162-session-phase45-hardening/quickstart.md`

**Checkpoint**: The implementation delta is known, shared safety helpers exist, and every story can add focused tests without duplicating setup.

---

## Phase 3: User Story 1 - Inspect Session Health Safely (Priority: P1)

**Goal**: Operators can inspect bounded session identity, phase, epoch, degradation state, and continuity refs without exposing prompts, transcripts, scrollback, logs, or secrets.

**Independent Test**: Start and transition a managed session, then verify only bounded identity/status/ref values appear in workflow visibility metadata and Search Attributes.

### Tests for User Story 1

- [X] T009 [P] [US1] Add or update tests for initial bounded current details and exact Search Attribute keys in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T010 [P] [US1] Add or update tests for child workflow static summary/details and initial bounded Search Attributes in `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`
- [X] T011 [P] [US1] Add or update transition-detail tests for started, active turn running, interrupted, cleared, degraded, terminating, and terminated states in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T012 [P] [US1] Add or update forbidden metadata leakage tests for workflow visibility and Search Attributes in `tests/unit/workflows/temporal/workflows/test_agent_session.py`

### Implementation for User Story 1

- [X] T013 [US1] Add or correct bounded Search Attribute and current-detail updates in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T014 [US1] Add or correct task-scoped session child workflow static summary/details and bounded initial Search Attributes in `moonmind/workflows/temporal/workflows/run.py`
- [X] T015 [US1] Add or correct bounded managed-session launch visibility propagation in `moonmind/workflows/temporal/workflows/agent_run.py`
- [X] T016 [US1] Remove or replace any obsolete unbounded managed-session metadata path found during implementation in `moonmind/workflows/temporal/workflows/agent_session.py`

### Validation for User Story 1

- [X] T017 [US1] Verify User Story 1 with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`

**Checkpoint**: User Story 1 is independently testable with bounded operator metadata and no forbidden content.

---

## Phase 4: User Story 2 - Review Control History Without Payload Inspection (Priority: P2)

**Goal**: Operators can understand launch and control operations from durable history summaries without opening payloads.

**Independent Test**: Drive launch, send, interrupt, clear, cancel, steer, and terminate operations and verify bounded readable summaries exclude instructions, logs, raw errors, and secrets.

### Tests for User Story 2

- [X] T018 [P] [US2] Add or update activity-summary tests for send, steer, interrupt, clear, cancel, and terminate controls in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T019 [P] [US2] Add or update launch activity-summary tests for task-scoped session launch in `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`
- [X] T020 [P] [US2] Add or update forbidden-value summary tests for instructions, raw output, raw errors, and secret-like values in `tests/unit/workflows/temporal/workflows/test_agent_session.py`

### Implementation for User Story 2

- [X] T021 [US2] Add or correct bounded activity summaries for session controls in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T022 [US2] Add or correct bounded launch activity summaries in `moonmind/workflows/temporal/workflows/run.py`
- [X] T023 [US2] Add or correct bounded launch activity summaries in `moonmind/workflows/temporal/workflows/agent_run.py`

### Validation for User Story 2

- [X] T024 [US2] Verify User Story 2 with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`

**Checkpoint**: User Stories 1 and 2 expose bounded operator visibility and readable control history independently.

---

## Phase 5: User Story 3 - Recover Sessions Recurringly (Priority: P3)

**Goal**: Managed session reconciliation runs from a durable recurring trigger, delegates runtime checks to the runtime boundary, and returns a bounded recovery outcome.

**Independent Test**: Configure the recurring trigger, execute reconcile, and verify runtime routing plus bounded stale/degraded/orphan outcome handling.

### Tests for User Story 3

- [X] T025 [P] [US3] Add or update bounded reconcile outcome tests for stale degraded sessions, missing containers, orphaned runtime state, and forbidden leakage in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [X] T026 [P] [US3] Add or update controller reconcile tests for reattach, degrade, terminal, missing-container, and orphan detection in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- [X] T027 [P] [US3] Add or update activity catalog and worker routing tests for `agent_runtime.reconcile_managed_sessions` in `tests/unit/workflows/temporal/test_temporal_workers.py`
- [X] T028 [P] [US3] Add or update workflow fleet registration tests for `MoonMind.ManagedSessionReconcile` in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [X] T029 [P] [US3] Add or update schedule helper tests for stable schedule ID, workflow ID template, cadence, timezone, and paused disabled state in `tests/unit/workflows/temporal/test_client_schedules.py`

### Implementation for User Story 3

- [X] T030 [US3] Add or correct reconcile routing in the activity catalog in `moonmind/workflows/temporal/activity_catalog.py`
- [X] T031 [US3] Add or correct bounded reconcile activity wrapper and outcome normalization in `moonmind/workflows/temporal/activity_runtime.py`
- [X] T032 [US3] Add or correct recurring reconcile workflow target in `moonmind/workflows/temporal/workflows/managed_session_reconcile.py`
- [X] T033 [US3] Add or correct workflow and runtime fleet registration in `moonmind/workflows/temporal/workers.py`, `moonmind/workflows/temporal/worker_entrypoint.py`, and `moonmind/workflows/temporal/worker_runtime.py`
- [X] T034 [US3] Add or correct managed-session reconcile schedule creation/update helper in `moonmind/workflows/temporal/client.py`
- [X] T035 [US3] Add or correct controller reconcile behavior for stale, missing, orphaned, and terminal session records in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [X] T036 [US3] Add or correct supervisor finalization or reattachment behavior needed by reconcile in `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`

### Validation for User Story 3

- [X] T037 [US3] Verify User Story 3 with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/temporal/test_temporal_workers.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_client_schedules.py`

**Checkpoint**: Recurring recovery is independently testable and runtime/container work remains on the runtime activity boundary.

---

## Phase 6: User Story 4 - Prove Lifecycle Semantics End-to-End (Priority: P4)

**Goal**: Maintainers can prove managed session lifecycle controls, Continue-As-New handoff, and replay safety through integration and replay coverage.

**Independent Test**: Run fault-injected lifecycle and replay tests that cover create, attach handles, send, clear, interrupt, cancel, steer, terminate, reconcile, race/idempotency, and Continue-As-New carry-forward.

### Tests for User Story 4

- [X] T038 [P] [US4] Add or update Temporal lifecycle integration test for create, attach handles, send, clear, interrupt, cancel, and terminate in `tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py`
- [X] T039 [P] [US4] Add or update clear-session invariant tests for same session/container, incremented epoch, new thread, cleared active turn, and reset/control refs in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T040 [P] [US4] Add or update interrupt and steer contract tests for active-turn requirements, stale epochs, unavailable guarded behavior, and success path in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T041 [P] [US4] Add or update terminate cleanup and cancel-distinct-from-terminate tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T042 [P] [US4] Add or update duplicate request, stale epoch, update-before-handles, and parent/session shutdown race tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py` and `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`
- [X] T043 [P] [US4] Add or update Continue-As-New carry-forward regression tests for locator, epoch, continuity refs, and request tracking in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T044 [P] [US4] Add or update managed-session replay validation for representative histories in `tests/unit/workflows/temporal/test_agent_session_replayer.py`

### Implementation for User Story 4

- [X] T045 [US4] Add or correct lifecycle update handlers, validators, request tracking, and handler-completion behavior in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T046 [US4] Add or correct Continue-As-New threshold handling and carry-forward payload behavior in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T047 [US4] Add or correct parent/session shutdown and termination coordination in `moonmind/workflows/temporal/workflows/run.py`
- [X] T048 [US4] Add or correct managed-session controller cleanup, cancel, interrupt, steer, clear, and terminate behavior in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [X] T049 [US4] Add or correct runtime protocol behavior for steer, interrupt, clear, and terminate in `moonmind/workflows/temporal/runtime/codex_session_runtime.py`
- [X] T050 [US4] Add or correct schema validation for lifecycle request/response state used by tests in `moonmind/schemas/managed_session_models.py`

### Validation for User Story 4

- [X] T051 [US4] Verify User Story 4 with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py tests/unit/workflows/temporal/test_agent_session_replayer.py`

**Checkpoint**: Lifecycle semantics are covered through actual workflow execution, unit boundaries, and replay-safety validation.

---

## Phase 7: Polish and Cross-Cutting Verification

**Purpose**: Run final verification, update task artifacts, and remove obsolete internal paths uncovered by implementation.

- [X] T052 [P] Add managed-session telemetry/log-correlation safety tests for bounded dimensions and forbidden-value exclusion in `tests/unit/workflows/temporal/workflows/test_agent_session.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [X] T053 Add or correct managed-session metrics, tracing, and structured log correlation using bounded identifiers in `moonmind/workflows/temporal/workflows/agent_session.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/workflows/temporal/worker_runtime.py`
- [X] T054 [P] Update focused verification commands if target names changed in `specs/162-session-phase45-hardening/quickstart.md`
- [X] T055 [P] Remove obsolete internal compatibility shims or superseded managed-session paths found during implementation in `moonmind/workflows/temporal/workflows/agent_session.py`, `moonmind/workflows/temporal/runtime/managed_session_controller.py`, and adjacent tests
- [X] T056 Run focused verification from `specs/162-session-phase45-hardening/quickstart.md`
- [X] T057 Run required full unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T058 Run hermetic integration verification with `./tools/test_integration.sh` if changed files touch required `integration_ci` seams
- [X] T059 Record any intentionally deferred provider-verification coverage in `specs/162-session-phase45-hardening/quickstart.md`

---

## Dependencies and Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational; MVP bounded visibility slice.
- **User Story 2 (Phase 4)**: Depends on Foundational and can proceed after or alongside User Story 1 if file ownership is coordinated.
- **User Story 3 (Phase 5)**: Depends on Foundational and can proceed independently of User Stories 1 and 2.
- **User Story 4 (Phase 6)**: Depends on Foundational; should integrate results from User Stories 1-3 before final lifecycle/replay validation.
- **Polish (Phase 7)**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 Inspect Session Health Safely**: No dependency on other stories after Foundational.
- **US2 Review Control History Without Payload Inspection**: Shares workflow files with US1; coordinate ordering if parallel.
- **US3 Recover Sessions Recurringly**: Independent after Foundational; primarily activity/runtime/client/worker surfaces.
- **US4 Prove Lifecycle Semantics End-to-End**: Can start with tests after Foundational but final implementation should account for any lifecycle-affecting changes from US1-US3.

### Within Each User Story

- Write or update automated tests before production code changes.
- Confirm new tests fail for the expected reason when behavior is missing.
- Implement production runtime changes only for behavior proven missing or defective.
- Run story-specific validation before moving to the next checkpoint.
- Keep metadata and fixtures free of prompts, transcripts, scrollback, raw logs, credentials, and secrets.

---

## Parallel Opportunities

- T002, T003, and T004 can run in parallel.
- T006 and T007 can run in parallel after T005 starts.
- T009, T010, T011, and T012 can run in parallel.
- T018, T019, and T020 can run in parallel.
- T025, T026, T027, T028, and T029 can run in parallel.
- T038 through T044 can run in parallel if each worker owns disjoint test files or coordinates shared test file edits.
- T030 through T036 can be split by module after the US3 tests define the expected contract.
- T052 can run after story tests exist and before T053 implements telemetry/log-correlation changes.

---

## Parallel Example: User Story 3

```text
Task: "Add or update bounded reconcile outcome tests in tests/unit/workflows/temporal/test_agent_runtime_activities.py"
Task: "Add or update controller reconcile tests in tests/unit/services/temporal/runtime/test_managed_session_controller.py"
Task: "Add or update worker routing tests in tests/unit/workflows/temporal/test_temporal_workers.py"
Task: "Add or update schedule helper tests in tests/unit/workflows/temporal/test_client_schedules.py"
```

---

## Implementation Strategy

### MVP First

1. Complete Setup and Foundational audit tasks.
2. Complete User Story 1 tests and runtime implementation for bounded operator visibility.
3. Validate User Story 1 independently before adding broader lifecycle or reconcile changes.

### Incremental Delivery

1. Deliver US1 for bounded operator visibility.
2. Deliver US2 for readable control history.
3. Deliver US3 for durable recurring recovery.
4. Deliver US4 for lifecycle, Continue-As-New, and replay safety.
5. Run final focused and full verification.

### Runtime Guard

This task set is incomplete unless it includes:

- production runtime code changes when missing behavior is found,
- automated validation tests for each runtime story,
- metrics/tracing/log correlation tasks when that Phase 4 behavior is missing,
- focused verification,
- required full unit verification,
- no docs-only completion.
