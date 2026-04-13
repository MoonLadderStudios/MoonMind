# Tasks: Agent Session Deployment Safety

**Input**: Design documents from `/specs/165-agent-session-deployment-safety/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/agent-session-deployment-safety.md`, `quickstart.md`
**Tests**: Required. This feature explicitly requires test-driven development, so add or update targeted validation before treating production runtime changes as complete.
**Organization**: Tasks are grouped by user story to keep each story independently implementable and testable.

## Phase 1: Setup

**Purpose**: Establish feature scope, traceability status, and current implementation inventory before changing runtime code.

- [X] T001 Verify current managed-session workflow, runtime, controller, worker, reconcile, and tests against FR-001 through FR-028 in `specs/165-agent-session-deployment-safety/quickstart.md`
- [X] T002 [P] Confirm no `DOC-REQ-*` identifiers exist and no requirements traceability artifact is required in `specs/165-agent-session-deployment-safety/plan.md`
- [X] T003 [P] Record TDD sequencing and runtime-mode scope guard in `specs/165-agent-session-deployment-safety/research.md`
- [X] T004 [P] Ensure the production/session-plane contract includes validation mapping for TDD, replay gates, and runtime deliverables in `specs/165-agent-session-deployment-safety/contracts/agent-session-deployment-safety.md`

---

## Phase 2: Foundational

**Purpose**: Shared schemas, routing, retry, heartbeat, task-queue, and worker-versioning prerequisites used by every story.

**CRITICAL**: Complete this phase before story-specific implementation.

- [X] T005 [P] Add or update managed-session request idempotency, epoch, continuity ref, and Continue-As-New carry-forward schemas in `moonmind/schemas/managed_session_models.py`
- [X] T006 [P] Add schema regression tests for managed-session control and carry-forward payloads in `tests/unit/schemas/test_managed_session_models.py`
- [X] T007 Add heartbeat, retry, timeout, and non-retryable expectations for managed-session activities in `moonmind/workflows/temporal/activity_catalog.py`
- [X] T008 Add route policy regression tests for managed-session activities in `tests/unit/workflows/temporal/test_activity_catalog.py`
- [X] T009 Update worker routing so workflow processing and heavy managed-runtime activities use separated task queues in `moonmind/workflows/temporal/worker_runtime.py`
- [X] T010 Add worker task-queue separation tests in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [X] T011 Add or update deployment-safety helper coverage for worker-versioning and replay/cutover gates in `tests/unit/workflows/temporal/test_agent_session_deployment_safety.py`

**Checkpoint**: Shared schemas, activity policies, worker routing, and deployment-safety gates are ready.

---

## Phase 3: User Story 1 - Control Sessions Without Leaks (Priority: P1)

**Goal**: Production controls use the canonical vocabulary, reject invalid mutations deterministically, and make terminate/cancel/steer/interrupt real runtime behaviors.
**Independent Test**: Exercise each control through the workflow boundary and verify state, artifacts, recovery records, and runtime cleanup without treating container-local cache as durable truth.

### Tests for User Story 1

- [X] T012 [P] [US1] Add accepted-path workflow tests for session start/resume binding initialization, runtime handle attachment, `SendFollowUp`, `SteerTurn`, `InterruptTurn`, `ClearSession`, `CancelSession`, and `TerminateSession` in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T013 [P] [US1] Add workflow rejection tests for stale epoch, missing handles, missing active turn, duplicate request, clear while clearing, and mutator after termination in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T014 [P] [US1] Add runtime-level steer, interrupt, cancel, clear, and terminate behavior tests in `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`
- [X] T015 [P] [US1] Add controller idempotency and finalization tests for clear, interrupt, steer, cancel, and terminate in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- [X] T016 [P] [US1] Add parent/session termination race coverage proving no orphaned session remains in `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`
- [X] T017 [P] [US1] Add activity-wrapper tests for heartbeat delivery and non-retryable permanent failures in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`

### Implementation for User Story 1

- [X] T018 [US1] Enforce typed workflow Update validators, deterministic pre-mutation rejection, `@workflow.init` start-state binding, and resume/carry-forward input handling in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T019 [US1] Restrict generic `control_action` handling to replay or explicit bridge behavior in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T020 [US1] Wire workflow `InterruptTurn` and `SteerTurn` through runtime activities and bounded state updates in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T021 [US1] Implement distinct workflow `CancelSession` semantics that stop active work without marking runtime teardown complete in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T022 [US1] Implement cleanup-complete workflow `TerminateSession` semantics that wait for runtime termination and supervision finalization in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T023 [US1] Make managed-session runtime send, steer, interrupt, clear, cancel, and terminate operations state-aware and idempotent in `moonmind/workflows/temporal/runtime/codex_session_runtime.py`
- [X] T024 [US1] Make controller clear, interrupt, steer, cancel, and terminate operations retry-safe and finalization-aware in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [X] T025 [US1] Ensure supervisor finalization records terminal cleanup and bounded terminal artifact refs in `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`
- [X] T026 [US1] Surface permanent invalid-state or unsupported-runtime failures as non-retryable activity errors and heartbeat blocking control calls in `moonmind/workflows/temporal/activity_runtime.py`

### Validation for User Story 1

- [X] T027 [US1] Run focused US1 validation with `./tools/test_unit.sh` for `tests/unit/workflows/temporal/workflows/test_agent_session.py`, `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`, and `tests/unit/services/temporal/runtime/test_managed_session_controller.py`

**Checkpoint**: User Story 1 is independently functional and leak-focused controls are verifiable.

---

## Phase 4: User Story 2 - Keep Long-Lived Sessions Safe (Priority: P2)

**Goal**: Long-lived, message-heavy session workflows serialize mutators, wait for runtime readiness, drain accepted handlers, and Continue-As-New with compact carry-forward state.
**Independent Test**: Drive concurrent controls, early runtime-bound updates, handler handoff, and forced shortened-history rollover.

### Tests for User Story 2

- [X] T028 [P] [US2] Add concurrent mutator ordering and shared-state serialization tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T029 [P] [US2] Add accepted-before-handles readiness tests for runtime-bound controls in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T030 [P] [US2] Add handler-drain-before-complete and handler-drain-before-Continue-As-New tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T031 [P] [US2] Add shortened-history Continue-As-New carry-forward tests for locator, epoch, refs, degradation, and request tracking in `tests/unit/workflows/temporal/test_agent_session_replayer.py`
- [X] T032 [P] [US2] Add local workflow lifecycle coverage for early updates and Continue-As-New handoff in `tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py`

### Implementation for User Story 2

- [X] T033 [US2] Ensure all async mutators touching locator, thread, active turn, status, control metadata, refs, and degradation state share one workflow-safe lock in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T034 [US2] Gate accepted runtime-bound controls on runtime-handle readiness with deterministic wait conditions in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T035 [US2] Wait for `workflow.all_handlers_finished` before workflow completion and Continue-As-New handoff in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T036 [US2] Keep Continue-As-New initiation in the workflow main run path and carry forward binding, epoch, locator, control metadata, refs, degradation, and request-tracking state in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T037 [US2] Preserve parent workflow child-session invocation and handoff payload shape in `moonmind/workflows/temporal/workflows/run.py`

### Validation for User Story 2

- [X] T038 [US2] Run focused US2 unit validation with `./tools/test_unit.sh` for `tests/unit/workflows/temporal/workflows/test_agent_session.py` and `tests/unit/workflows/temporal/test_agent_session_replayer.py`
- [X] T039 [US2] Run local Temporal lifecycle validation with `pytest tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py -q`

**Checkpoint**: User Story 2 is independently functional and long-lived workflow hazards are covered.

---

## Phase 5: User Story 3 - Recover and Observe Sessions Safely (Priority: P3)

**Goal**: Operators can observe and recover sessions through bounded metadata, artifact refs, managed-session records, and recurring reconciliation without leaking sensitive or unbounded runtime content.
**Independent Test**: Transition sessions through launch, active turn, interruption, clear, degradation, cancellation, and termination, then verify bounded metadata, summaries, telemetry correlation, and reconcile outcomes.

### Tests for User Story 3

- [X] T040 [P] [US3] Add bounded current-details and Search Attribute tests for managed-session identity, epoch, status, and degradation state in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T041 [P] [US3] Add activity summary tests for launch, send, steer, interrupt, clear, cancel, terminate, publish, and reconcile in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [X] T042 [P] [US3] Add controller publication tests proving summary/checkpoint/control/reset refs come from durable managed-session records in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- [X] T043 [P] [US3] Add supervisor artifact publication and terminal summary tests in `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py`
- [X] T044 [P] [US3] Add reconcile workflow tests for missing containers, stale degraded sessions, orphaned runtime state, and bounded outcomes in `tests/unit/workflows/temporal/workflows/test_managed_session_reconcile.py`
- [X] T045 [P] [US3] Add client schedule tests for recurring managed-session reconcile creation, identifiers, and bounded metadata in `tests/unit/workflows/temporal/test_client_schedules.py`
- [X] T046 [P] [US3] Add forbidden-content regression tests for workflow metadata, activity summaries, schedule metadata, and replay fixtures in `tests/unit/workflows/temporal/workflows/test_agent_session.py`

### Implementation for User Story 3

- [X] T047 [US3] Update workflow static summary, static details, current details, Search Attributes, and query state to bounded managed-session fields only in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T048 [US3] Update Temporal client Search Attribute construction and managed-session schedule metadata to use only bounded identifiers in `moonmind/workflows/temporal/client.py`
- [X] T049 [US3] Update managed-session activity summaries and telemetry correlation fields to avoid prompts, transcripts, scrollback, raw logs, credentials, secrets, and unbounded output in `moonmind/workflows/temporal/activity_runtime.py`
- [X] T050 [US3] Ensure production summary and artifact publication reads from controller/supervisor durable records rather than container-local fallback helpers in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [X] T051 [US3] Ensure supervisor summary, checkpoint, control, and reset artifacts are bounded and finalization-safe in `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`
- [X] T052 [US3] Implement or harden recurring reconcile outcomes for stale degraded records, missing containers, orphaned runtime state, and supervision drift in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [X] T053 [US3] Update `MoonMindManagedSessionReconcileWorkflow` to return bounded reconcile summaries and avoid sensitive payloads in `moonmind/workflows/temporal/workflows/managed_session_reconcile.py`
- [X] T054 [US3] Register and route managed-session reconcile workflow and runtime activities onto intended task queues in `moonmind/workflows/temporal/workers.py`

### Validation for User Story 3

- [X] T055 [US3] Run focused US3 validation with `./tools/test_unit.sh` for `tests/unit/workflows/temporal/workflows/test_agent_session.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, `tests/unit/workflows/temporal/workflows/test_managed_session_reconcile.py`, `tests/unit/workflows/temporal/test_client_schedules.py`, `tests/unit/services/temporal/runtime/test_managed_session_controller.py`, and `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py`
- [X] T056 [US3] Run the forbidden-content scan from `specs/165-agent-session-deployment-safety/quickstart.md` against `moonmind/workflows/temporal`, `tests/unit/workflows/temporal`, and `tests/integration/services/temporal/workflows`

**Checkpoint**: User Story 3 is independently functional and bounded recovery/observability behavior is covered.

---

## Phase 6: User Story 4 - Gate Workflow Changes Before Rollout (Priority: P4)

**Goal**: Incompatible managed-session workflow changes cannot roll out without Worker Versioning, scoped patching or explicit cutover, replay validation, and cutover guidance.
**Independent Test**: Run representative replay and deployment-safety validation for workflow-shape changes and verify rollout is blocked when versioning or replay coverage is absent.

### Tests for User Story 4

- [X] T057 [P] [US4] Add Worker Versioning configuration tests for managed-session workflow workers in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [X] T058 [P] [US4] Add replay gate tests for representative open and closed `AgentSessionWorkflow` histories in `tests/unit/workflows/temporal/test_agent_session_replayer.py`
- [X] T059 [P] [US4] Add patch or versioned-cutover assertion tests for handler, payload, Continue-As-New, and visibility-shape changes in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [X] T060 [P] [US4] Add deployment-safety helper tests for sensitive path detection, changed-path base-ref handling, active feature override behavior, worker-versioning enforcement, replay coverage, and cutover topics in `tests/unit/workflows/temporal/test_agent_session_deployment_safety.py`

### Implementation for User Story 4

- [X] T061 [US4] Enforce managed-session Worker Versioning configuration and safe default behavior in `moonmind/workflows/temporal/worker_runtime.py`
- [X] T062 [US4] Add or harden explicit patch/version gates around replay-sensitive managed-session workflow-shape changes in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T063 [US4] Add deployment-safety helper logic for sensitive changed paths, explicit base-ref comparison, replay coverage, worker-versioning, active feature override, and cutover validation in `moonmind/workflows/temporal/deployment_safety.py`
- [X] T064 [US4] Add executable deployment-safety validation entrypoint with `--base-ref` and local `SPECIFY_FEATURE`/active-feature handling in `tools/validate_agent_session_deployment_safety.py`
- [X] T065 [US4] Wire AgentSession deployment-safety validation into backend CI with full-history checkout and explicit pull-request base SHA handling in `.github/workflows/pytest-unit-tests.yml`
- [X] T066 [US4] Add cutover playbook entries for enabling steering, enabling Continue-As-New, changing cancel/terminate semantics, and introducing visibility metadata in `docs/tmp/remaining-work/agent-session-deployment-safety-cutover.md`
- [X] T067 [US4] Wire deployment-safety validation guidance into the feature quickstart in `specs/165-agent-session-deployment-safety/quickstart.md`

### Validation for User Story 4

- [X] T068 [US4] Run focused US4 validation with `./tools/test_unit.sh` for `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, `tests/unit/workflows/temporal/test_agent_session_replayer.py`, `tests/unit/workflows/temporal/workflows/test_agent_session.py`, and `tests/unit/workflows/temporal/test_agent_session_deployment_safety.py`
- [X] T069 [US4] Run AgentSession deployment-safety validation with explicit base-ref and local active-feature override coverage using `tools/validate_agent_session_deployment_safety.py`

**Checkpoint**: User Story 4 is independently functional and deployment gates are enforceable.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, cleanup, and release readiness across all stories.

- [X] T070 [P] Update cross-story managed-session lifecycle validation commands in `specs/165-agent-session-deployment-safety/quickstart.md`
- [X] T071 [P] Remove obsolete or indefinite legacy managed-session bridge paths after replay/cutover conditions are satisfied in `moonmind/workflows/temporal/workflows/agent_session.py`
- [X] T072 [P] Update affected managed-session architecture notes while keeping desired-state docs separate from migration backlog in `docs/ManagedAgents/CodexManagedSessionPlane.md`
- [X] T073 Run full required unit validation with `./tools/test_unit.sh`
- [X] T074 Run hermetic integration validation with `./tools/test_integration.sh` if implementation changed an `integration_ci` seam
- [X] T075 Run `git diff --check` and `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational and is the MVP.
- **User Story 2 (Phase 4)**: Depends on Foundational; can follow or run alongside User Story 1 if `agent_session.py` edits are coordinated.
- **User Story 3 (Phase 5)**: Depends on Foundational; should reflect final cancel/terminate outcomes from User Story 1.
- **User Story 4 (Phase 6)**: Depends on Foundational and should cover workflow-shape changes introduced by User Stories 1-3.
- **Polish (Phase 7)**: Depends on completed story scope.

### User Story Dependencies

- **US1 Control Sessions Without Leaks**: MVP; no dependency on US2-US4 after Foundational.
- **US2 Keep Long-Lived Sessions Safe**: Independent after Foundational, but shares `agent_session.py` with US1.
- **US3 Recover and Observe Sessions Safely**: Independent after Foundational, but final observability semantics should reflect US1 lifecycle behavior.
- **US4 Gate Workflow Changes Before Rollout**: Can start after Foundational, but final gates must cover workflow-shape changes from US1-US3.

### Within Each User Story

- Tests are listed before implementation and should be added or updated first.
- Runtime schema/activity/worker tasks in Phase 2 must land before story-specific code depends on them.
- Workflow tests precede workflow edits.
- Runtime/controller tests precede runtime/controller edits.
- Story validation commands run before moving to broad rollout or final polish.

## Parallel Opportunities

- T002, T003, and T004 can run in parallel with T001.
- T005 and T006 can run in parallel with T007 and T008.
- T009, T010, and T011 can run in parallel with schema and activity route work.
- US1 test tasks T012 through T017 can run in parallel because they target distinct files or behavior.
- US2 test tasks T028 through T032 can run in parallel, while implementation tasks T033 through T037 should be serialized around `agent_session.py`.
- US3 test tasks T040 through T046 can run in parallel across workflow, activity, controller, supervisor, reconcile, and client tests.
- US4 test tasks T057 through T060 can run in parallel; implementation tasks T061 through T067 should follow the workflow-shape decisions from US1-US3.

## Parallel Example: User Story 1

```text
Task: "T012 Add accepted-path workflow tests in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "T014 Add runtime-level control behavior tests in tests/unit/services/temporal/runtime/test_codex_session_runtime.py"
Task: "T015 Add controller-level idempotency and finalization tests in tests/unit/services/temporal/runtime/test_managed_session_controller.py"
Task: "T017 Add activity-wrapper heartbeat and non-retryable tests in tests/unit/workflows/temporal/test_agent_runtime_activities.py"
```

## Parallel Example: User Story 4

```text
Task: "T057 Add Worker Versioning configuration tests in tests/unit/workflows/temporal/test_temporal_worker_runtime.py"
Task: "T058 Add replay gate tests in tests/unit/workflows/temporal/test_agent_session_replayer.py"
Task: "T060 Add deployment-safety helper tests in tests/unit/workflows/temporal/test_agent_session_deployment_safety.py"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Write or update US1 tests T012 through T017.
3. Implement US1 runtime changes T018 through T026.
4. Run US1 validation T027.
5. Stop and confirm terminate cannot leak containers and cancel remains distinct from terminate before broadening scope.

### Incremental Delivery

1. Deliver US1 for canonical control and leak-proof lifecycle behavior.
2. Deliver US2 for long-lived workflow safety and Continue-As-New.
3. Deliver US3 for bounded observability, artifact/recovery separation, and scheduled reconcile.
4. Deliver US4 for Worker Versioning, replay gates, and cutover controls.
5. Complete Phase 7 full validation and cleanup.

### Bug-Fix Strategy

1. Add the smallest failing regression test at the workflow, activity, runtime, controller, replay, or integration boundary that proves the defect.
2. Implement the minimal production runtime fix in the corresponding `moonmind/` module.
3. Run the focused story validation command.
4. Run full unit validation before finalizing.

### Release Gate

1. Required story validation passes for every implemented story.
2. Replay validation passes for workflow-shape changes.
3. Worker Versioning, patching, or explicit cutover protects incompatible changes.
4. TDD evidence exists for every production runtime behavior changed or newly relied upon.
