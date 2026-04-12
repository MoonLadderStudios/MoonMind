# Tasks: Managed Session Observability and Reconcile

**Input**: Design documents from `specs/160-session-observability-reconcile/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/managed-session-observability-contract.md`, `quickstart.md`
**Tests**: Required. Runtime mode requires production runtime code changes plus validation tests.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or independent assertions
- **[Story]**: User story mapping from `spec.md` (`US1`, `US2`, `US3`)
- Every task names exact file paths
- Tests must be written first and fail before the paired implementation task

---

## Phase 1: Setup

**Purpose**: Confirm the active implementation surfaces and preserve the runtime-mode scope guard.

- [ ] T001 Review runtime scope, forbidden metadata, and verification commands in `specs/160-session-observability-reconcile/spec.md`, `specs/160-session-observability-reconcile/plan.md`, `specs/160-session-observability-reconcile/contracts/managed-session-observability-contract.md`, and `specs/160-session-observability-reconcile/quickstart.md`
- [ ] T002 [P] Inspect existing managed-session workflow, launch, worker, activity, and schedule surfaces in `moonmind/workflows/temporal/workflows/agent_session.py`, `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/workflows/agent_run.py`, `moonmind/workflows/temporal/activity_catalog.py`, `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/temporal/workers.py`, `moonmind/workflows/temporal/worker_entrypoint.py`, `moonmind/workflows/temporal/worker_runtime.py`, and `moonmind/workflows/temporal/client.py`

---

## Phase 2: Foundational

**Purpose**: Add shared observability constants and test fixtures that every story can rely on.

**CRITICAL**: No story implementation should begin until the bounded-field vocabulary and test helpers are in place.

- [ ] T003 Add shared test helpers for asserting bounded managed-session metadata and forbidden-value absence in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T004 [P] Add the canonical bounded Search Attribute names, transition labels, and metadata formatting helpers in `moonmind/workflows/temporal/workflows/agent_session.py`

**Checkpoint**: Tests and runtime code can reference one bounded vocabulary for session identity, status, degradation, and continuity refs.

---

## Phase 3: User Story 1 - Inspect Active Session State (Priority: P1) MVP

**Goal**: Operators can inspect bounded session identity, epoch, phase, degradation state, and latest continuity refs from workflow-visible metadata without seeing prompts, transcripts, logs, or secrets.

**Independent Test**: Start a managed session and drive major transitions, then verify static details, current details, and Search Attributes contain only bounded identity/status/ref values.

### Tests for User Story 1

- [ ] T005 [P] [US1] Add failing tests for initial static/current details and exact Search Attribute keys in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T006 [P] [US1] Add failing tests for task-scoped child workflow static summary/details and initial bounded Search Attributes in `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`
- [ ] T007 [P] [US1] Add failing tests for current-detail updates on started, active turn running, interrupted, cleared to new epoch, degraded, terminating, and terminated transitions in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T008 [P] [US1] Add failing tests that prompt-like, transcript-like, raw-log-like, credential-like, and raw-error-like values are excluded from workflow metadata in `tests/unit/workflows/temporal/workflows/test_agent_session.py`

### Implementation for User Story 1

- [ ] T009 [US1] Implement bounded Search Attribute upserts and current details updates for session start and all major transitions in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T010 [US1] Attach static summary/details and initial bounded Search Attributes when `MoonMind.Run` starts the task-scoped session child workflow in `moonmind/workflows/temporal/workflows/run.py`
- [ ] T011 [US1] Ensure `MoonMind.AgentRun` launch orchestration propagates only bounded task/session/runtime identity into session visibility metadata in `moonmind/workflows/temporal/workflows/agent_run.py`

**Checkpoint**: User Story 1 is independently testable with bounded operator metadata and no forbidden content.

---

## Phase 4: User Story 2 - Read Control Activity History (Priority: P2)

**Goal**: Operators can understand launch and control activity history entries without opening each activity payload.

**Independent Test**: Schedule launch, send, interrupt, clear, and terminate operations and verify each history summary identifies the operation using bounded identifiers only.

### Tests for User Story 2

- [ ] T012 [P] [US2] Add failing activity-summary assertions for send, interrupt, clear, and terminate scheduling in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T013 [P] [US2] Add failing launch activity-summary assertions for task-scoped managed session launch in `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`

### Implementation for User Story 2

- [ ] T014 [US2] Add readable bounded summaries to managed-session send, interrupt, clear, and terminate activity scheduling in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T015 [US2] Add readable bounded summaries to managed-session launch activity scheduling in `moonmind/workflows/temporal/workflows/run.py` and `moonmind/workflows/temporal/workflows/agent_run.py`

**Checkpoint**: User Story 2 is independently testable through activity history summary assertions.

---

## Phase 5: User Story 3 - Recover Stale Sessions Recurringly (Priority: P3)

**Goal**: Managed-session reconciliation runs from a durable recurring Temporal trigger, delegates Docker/runtime checks to the agent-runtime activity boundary, and returns a bounded outcome.

**Independent Test**: Create/update the recurring schedule, run the reconcile target, and verify activity routing plus bounded reconciliation output.

### Tests for User Story 3

- [ ] T016 [P] [US3] Add failing tests for stale degraded session detection, orphaned runtime container detection, bounded reconcile activity output, and forbidden record/log/credential leakage in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [ ] T017 [P] [US3] Add failing tests that `agent_runtime.reconcile_managed_sessions` is cataloged and routed to the agent-runtime worker family in `tests/unit/workflows/temporal/test_temporal_workers.py`
- [ ] T018 [P] [US3] Add failing tests that the main workflow fleet registers the reconcile workflow without moving Docker/runtime activity work onto workflow workers in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [ ] T019 [P] [US3] Add failing tests for idempotent create/update behavior, schedule ID `mm-operational:managed-session-reconcile`, workflow ID template `mm-operational:managed-session-reconcile:{{.ScheduleTime}}`, default cron `*/10 * * * *`, `UTC` timezone, and disabled paused-state behavior of the managed-session reconcile Temporal Schedule in `tests/unit/workflows/temporal/test_client_schedules.py`

### Implementation for User Story 3

- [ ] T020 [US3] Add `agent_runtime.reconcile_managed_sessions` to the activity catalog and runtime task-queue mapping in `moonmind/workflows/temporal/activity_catalog.py`
- [ ] T021 [US3] Implement the bounded managed-session reconcile activity wrapper and outcome normalization for stale degraded session records and orphaned runtime containers in `moonmind/workflows/temporal/activity_runtime.py`
- [ ] T022 [US3] Add the `MoonMind.ManagedSessionReconcile` workflow target that delegates reconciliation to `agent_runtime.reconcile_managed_sessions` in `moonmind/workflows/temporal/workflows/managed_session_reconcile.py`
- [ ] T023 [US3] Register the reconcile workflow on workflow-processing workers while preserving runtime activity separation in `moonmind/workflows/temporal/workers.py`, `moonmind/workflows/temporal/worker_entrypoint.py`, and `moonmind/workflows/temporal/worker_runtime.py`
- [ ] T024 [US3] Add the Temporal client helper that creates or updates the recurring managed-session reconcile schedule with bounded metadata, stable schedule ID `mm-operational:managed-session-reconcile`, workflow ID template `mm-operational:managed-session-reconcile:{{.ScheduleTime}}`, default cron `*/10 * * * *`, `UTC` timezone, and disabled paused-state behavior in `moonmind/workflows/temporal/client.py`

**Checkpoint**: User Story 3 is independently testable through schedule, workflow registration, activity routing, and bounded reconcile outcome assertions.

---

## Phase 6: Polish and Cross-Cutting Verification

**Purpose**: Validate the whole runtime slice and keep the feature artifacts aligned with the implemented verification surface.

- [ ] T025 [P] Update focused verification commands if any test class or path names changed in `specs/160-session-observability-reconcile/quickstart.md`
- [ ] T026 Run the focused runtime verification command from `specs/160-session-observability-reconcile/quickstart.md`
- [ ] T027 Run the required unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

---

## Dependencies and Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup and blocks all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational; MVP slice
- **User Story 2 (Phase 4)**: Depends on Foundational and can proceed after the bounded summary vocabulary from US1 is available
- **User Story 3 (Phase 5)**: Depends on Foundational and can proceed independently of US1/US2 implementation
- **Polish (Phase 6)**: Depends on selected user stories being complete

### User Story Dependencies

- **US1 Inspect Active Session State**: No dependency on other stories after Foundational
- **US2 Read Control Activity History**: Can be implemented after Foundational, but should reuse bounded formatting helpers from US1 if those land first
- **US3 Recover Stale Sessions Recurringly**: No dependency on US1 or US2 after Foundational

### Within Each User Story

- Write failing tests before implementation
- Implement production runtime code after the story's failing tests exist
- Re-run the story-specific tests before moving to the checkpoint
- Preserve the forbidden metadata rule in every metadata, summary, schedule, and reconcile output path

---

## Parallel Opportunities

- T002 can run in parallel with T001
- T003 and T004 can run in parallel after Setup because they touch test/runtime helper surfaces separately
- T005, T006, T007, and T008 can run in parallel
- T012 and T013 can run in parallel
- T016, T017, T018, and T019 can run in parallel
- US3 implementation tasks T020 through T024 can be split by file once the tests define the contract

---

## Parallel Example: User Story 3

```text
Task: "Add failing tests for bounded reconcile activity output and forbidden record/log/credential leakage in tests/unit/workflows/temporal/test_agent_runtime_activities.py"
Task: "Add failing tests that agent_runtime.reconcile_managed_sessions is cataloged and routed to the agent-runtime worker family in tests/unit/workflows/temporal/test_temporal_workers.py"
Task: "Add failing tests that the main workflow fleet registers the reconcile workflow without moving Docker/runtime activity work onto workflow workers in tests/unit/workflows/temporal/test_temporal_worker_runtime.py"
Task: "Add failing tests for idempotent create/update behavior of the managed-session reconcile Temporal Schedule in tests/unit/workflows/temporal/test_client_schedules.py"
```

---

## Implementation Strategy

### MVP First

1. Complete Setup and Foundational tasks.
2. Complete US1 tests and runtime implementation.
3. Validate US1 independently before adding activity summaries or recurring reconcile.

### Incremental Delivery

1. Deliver US1 for bounded operator visibility.
2. Deliver US2 for readable activity timeline summaries.
3. Deliver US3 for recurring operational reconciliation and worker separation.
4. Run focused verification and then the required unit suite.

### Runtime Scope Guard

This task set is invalid unless it produces production runtime code changes and validation tests. Docs/spec-only completion does not satisfy FR-009 or this runtime-mode plan.
