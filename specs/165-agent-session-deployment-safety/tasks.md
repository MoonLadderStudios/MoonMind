# Tasks: Agent Session Deployment Safety

**Input**: Design documents from `/specs/165-agent-session-deployment-safety/`
**Prerequisites**: `plan.md` (required), `spec.md` (required for user stories), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Automated tests are required for each runtime story. Write or update the listed tests before production edits and confirm they fail for the expected missing or incorrect behavior when the behavior is not already present.
**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the feature-specific audit and validation scaffolding before story work.

- [ ] T001 Verify the current managed-session runtime surfaces against FR-001 through FR-027 and record any discovered implementation gaps in `specs/165-agent-session-deployment-safety/quickstart.md`
- [ ] T002 [P] Add a focused managed-session contract coverage checklist to `specs/165-agent-session-deployment-safety/contracts/agent-session-deployment-safety.md`
- [ ] T003 [P] Confirm no `DOC-REQ-*` identifiers exist for this feature and leave traceability status documented in `specs/165-agent-session-deployment-safety/plan.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared runtime contracts and activity semantics that every story depends on.

**CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T004 [P] Add or update managed-session payload fields for control request idempotency, epoch validation, bounded refs, and Continue-As-New carry-forward in `moonmind/schemas/managed_session_models.py`
- [ ] T005 [P] Add schema regression coverage for managed-session control, snapshot, and carry-forward payloads in `tests/unit/schemas/test_managed_session_models.py`
- [ ] T006 Update managed-session activity routes with heartbeat and retry policy expectations for send, steer, interrupt, clear, cancel, terminate, publish, and reconcile in `moonmind/workflows/temporal/activity_catalog.py`
- [ ] T007 Add activity route timeout, retry, and heartbeat regression coverage for managed-session activities in `tests/unit/workflows/temporal/test_activity_catalog.py`
- [ ] T008 Update managed-session worker/runtime configuration plumbing for separated workflow and heavy runtime activity task queues in `moonmind/workflows/temporal/worker_runtime.py`
- [ ] T009 Add worker task-queue separation regression coverage for managed-session workflow and runtime activities in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`

**Checkpoint**: Shared schemas, activity routing, and worker routing are ready for story implementation.

---

## Phase 3: User Story 1 - Control Sessions Without Leaks (Priority: P1)

**Goal**: Production managed-session controls use the canonical vocabulary, reject invalid mutations deterministically, and make terminate/cancel/steer/interrupt real end-to-end runtime behaviors.
**Independent Test**: Exercise each control through the workflow boundary and verify state, artifacts, recovery records, and runtime cleanup without treating container-local cache as durable truth.

### Tests for User Story 1

- [ ] T010 [P] [US1] Add workflow-boundary tests for `SendFollowUp`, `SteerTurn`, `InterruptTurn`, `ClearSession`, `CancelSession`, and `TerminateSession` accepted paths in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T011 [P] [US1] Add workflow-boundary rejection tests for stale epoch, missing runtime handles, missing active turn, clear while clearing, and mutator after termination in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T012 [P] [US1] Add runtime-level steering, interruption, cancellation, clear, and termination behavior tests in `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`
- [ ] T013 [P] [US1] Add controller-level idempotent clear, interrupt, steer, cancel, and terminate cleanup tests in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- [ ] T014 [P] [US1] Add parent/session termination race coverage proving no orphaned session remains in `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`
- [ ] T015 [P] [US1] Add activity-wrapper tests for non-retryable permanent failures and heartbeat-wrapped managed-session controls in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`

### Implementation for User Story 1

- [ ] T016 [US1] Enforce typed workflow Update validators and deterministic pre-mutation rejection for managed-session controls in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T017 [US1] Restrict generic `control_action` handling to replay or explicit bridge behavior and keep production mutation outcomes on typed Updates in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T018 [US1] Wire workflow `InterruptTurn` and `SteerTurn` through runtime activities and update active-turn state, control refs, and bounded status in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T019 [US1] Implement distinct workflow `CancelSession` semantics that stop active work without marking runtime container teardown complete in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T020 [US1] Implement cleanup-complete workflow `TerminateSession` semantics that wait for runtime termination and supervision finalization before workflow completion in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T021 [US1] Make managed-session runtime send, steer, interrupt, clear, cancel, and terminate operations state-aware and idempotent in `moonmind/workflows/temporal/runtime/codex_session_runtime.py`
- [ ] T022 [US1] Make controller clear, interrupt, steer, cancel, and terminate operations retry-safe and finalization-aware in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [ ] T023 [US1] Ensure supervisor finalization records terminal cleanup and bounded terminal artifact refs in `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`
- [ ] T024 [US1] Surface permanent invalid-state or unsupported-runtime failures as non-retryable activity errors and heartbeat blocking control calls in `moonmind/workflows/temporal/activity_runtime.py`

### Validation for User Story 1

- [ ] T025 [US1] Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`

**Checkpoint**: User Story 1 is independently functional and leak-focused controls are verifiable.

---

## Phase 4: User Story 2 - Keep Long-Lived Sessions Safe (Priority: P2)

**Goal**: Long-lived message-heavy session workflows serialize mutators, wait for runtime readiness, drain accepted handlers, and Continue-As-New with compact carry-forward state.
**Independent Test**: Drive concurrent controls, early runtime-bound updates, handler handoff, and forced shortened-history rollover.

### Tests for User Story 2

- [ ] T026 [P] [US2] Add concurrent mutator ordering and shared-state serialization tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T027 [P] [US2] Add accepted-before-handles readiness tests for runtime-bound controls in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T028 [P] [US2] Add handler-drain-before-complete and handler-drain-before-Continue-As-New tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T029 [P] [US2] Add shortened-history Continue-As-New carry-forward tests for locator, epoch, refs, and request tracking in `tests/unit/workflows/temporal/test_agent_session_replayer.py`
- [ ] T030 [P] [US2] Add local workflow lifecycle coverage for early updates and Continue-As-New handoff in `tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py`

### Implementation for User Story 2

- [ ] T031 [US2] Ensure all async mutators touching locator, thread, active turn, status, control metadata, refs, and degradation state share one workflow-safe lock in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T032 [US2] Gate accepted runtime-bound controls on runtime-handle readiness with deterministic wait conditions in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T033 [US2] Wait for `workflow.all_handlers_finished` before workflow completion and before Continue-As-New handoff in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T034 [US2] Keep Continue-As-New initiation in the workflow main run path and carry forward binding, epoch, locator, control metadata, refs, degradation, and request-tracking state in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T035 [US2] Preserve parent workflow session handoff behavior and child workflow invocation payload shape in `moonmind/workflows/temporal/workflows/run.py`

### Validation for User Story 2

- [ ] T036 [US2] Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/test_agent_session_replayer.py`
- [ ] T037 [US2] Run `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py -q`

**Checkpoint**: User Story 2 is independently functional and long-lived workflow hazards are covered.

---

## Phase 5: User Story 3 - Recover and Observe Sessions Safely (Priority: P3)

**Goal**: Operators can observe and recover sessions through bounded metadata, artifact refs, managed-session records, and recurring reconciliation without leaking sensitive or unbounded runtime content.
**Independent Test**: Transition sessions through launch, active turn, interruption, clear, degradation, cancellation, and termination, then verify bounded metadata, summaries, telemetry correlation, and reconcile outcomes.

### Tests for User Story 3

- [ ] T038 [P] [US3] Add bounded current-details and Search Attribute tests for managed session identity, epoch, status, and degradation state in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T039 [P] [US3] Add activity summary tests for launch, send, steer, interrupt, clear, cancel, terminate, publish, and reconcile in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [ ] T040 [P] [US3] Add controller publication tests proving summary/checkpoint/control/reset refs come from durable managed-session records in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- [ ] T041 [P] [US3] Add supervisor artifact publication and terminal summary tests in `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py`
- [ ] T042 [P] [US3] Add managed-session reconcile workflow tests for missing containers, stale degraded sessions, orphaned runtime state, and bounded outcomes in `tests/unit/workflows/temporal/workflows/test_managed_session_reconcile.py`
- [ ] T043 [P] [US3] Add client schedule tests for recurring managed-session reconcile creation, identifiers, and bounded metadata in `tests/unit/workflows/temporal/test_client_schedules.py`
- [ ] T044 [P] [US3] Add forbidden-content regression tests for workflow metadata, activity summaries, schedule metadata, and replay fixtures in `tests/unit/workflows/temporal/workflows/test_agent_session.py`

### Implementation for User Story 3

- [ ] T045 [US3] Update workflow static summary, static details, current details, Search Attributes, and query state to bounded managed-session fields only in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T046 [US3] Update Temporal client Search Attribute construction and managed-session schedule metadata to use only bounded identifiers in `moonmind/workflows/temporal/client.py`
- [ ] T047 [US3] Update managed-session activity summaries and telemetry correlation fields to avoid prompts, transcripts, scrollback, raw logs, credentials, secrets, and unbounded output in `moonmind/workflows/temporal/activity_runtime.py`
- [ ] T048 [US3] Ensure production summary and artifact publication reads from controller/supervisor durable records rather than container-local fallback helpers in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [ ] T049 [US3] Ensure supervisor summary, checkpoint, control, and reset artifacts are bounded and finalization-safe in `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`
- [ ] T050 [US3] Implement or harden recurring reconcile outcomes for stale degraded records, missing containers, orphaned runtime state, and supervision drift in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [ ] T051 [US3] Update `MoonMindManagedSessionReconcileWorkflow` to return bounded reconcile summaries and avoid sensitive payloads in `moonmind/workflows/temporal/workflows/managed_session_reconcile.py`
- [ ] T052 [US3] Register and route managed-session reconcile workflow and runtime activities onto the intended task queues in `moonmind/workflows/temporal/workers.py`

### Validation for User Story 3

- [ ] T053 [US3] Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/temporal/workflows/test_managed_session_reconcile.py tests/unit/workflows/temporal/test_client_schedules.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py`
- [ ] T054 [US3] Run the forbidden-content scan from `specs/165-agent-session-deployment-safety/quickstart.md` against `moonmind/workflows/temporal`, `tests/unit/workflows/temporal`, and `tests/integration/services/temporal/workflows`

**Checkpoint**: User Story 3 is independently functional and bounded recovery/observability behavior is covered.

---

## Phase 6: User Story 4 - Gate Workflow Changes Before Rollout (Priority: P4)

**Goal**: Incompatible managed-session workflow changes cannot roll out without Worker Versioning, scoped patching or explicit cutover, replay validation, and cutover guidance.
**Independent Test**: Run representative replay and deployment-safety validation for workflow-shape changes and verify rollout is blocked when versioning or replay coverage is absent.

### Tests for User Story 4

- [ ] T055 [P] [US4] Add Worker Versioning configuration tests for managed-session workflow workers in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [ ] T056 [P] [US4] Add replay gate tests for representative open and closed `AgentSessionWorkflow` histories in `tests/unit/workflows/temporal/test_agent_session_replayer.py`
- [ ] T057 [P] [US4] Add patch or versioned-cutover assertion tests for handler, payload, Continue-As-New, and visibility-shape changes in `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T058 [P] [US4] Add cutover playbook validation tests for steering, Continue-As-New, cancel/terminate semantics, and visibility metadata in `tests/unit/workflows/temporal/test_agent_session_replayer.py`

### Implementation for User Story 4

- [ ] T059 [US4] Enforce managed-session Worker Versioning configuration and safe default behavior in `moonmind/workflows/temporal/worker_runtime.py`
- [ ] T060 [US4] Add or harden explicit patch/version gates around replay-sensitive managed-session workflow-shape changes in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T061 [US4] Add replay gate helpers or fixtures for representative managed-session histories in `tests/unit/workflows/temporal/test_agent_session_replayer.py`
- [ ] T062 [US4] Add cutover playbook entries for enabling steering, enabling Continue-As-New, changing cancel/terminate semantics, and introducing visibility metadata in `docs/tmp/remaining-work/agent-session-deployment-safety-cutover.md`
- [ ] T063 [US4] Wire deployment-safety validation guidance into the feature quickstart without making docs a substitute for runtime changes in `specs/165-agent-session-deployment-safety/quickstart.md`

### Validation for User Story 4

- [ ] T064 [US4] Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_agent_session_replayer.py tests/unit/workflows/temporal/workflows/test_agent_session.py`
- [ ] T065 [US4] Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime`

**Checkpoint**: User Story 4 is independently functional and deployment gates are enforceable.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final verification, cleanup, and release readiness across all stories.

- [ ] T066 [P] Update cross-story managed-session lifecycle validation commands in `specs/165-agent-session-deployment-safety/quickstart.md`
- [ ] T067 [P] Remove obsolete or indefinite legacy managed-session bridge paths after replay/cutover conditions are satisfied in `moonmind/workflows/temporal/workflows/agent_session.py`
- [ ] T068 [P] Update any affected managed-session architecture notes to keep desired-state docs separate from migration backlog in `docs/ManagedAgents/CodexManagedSessionPlane.md`
- [ ] T069 Run full required unit validation with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T070 Run hermetic integration validation with `./tools/test_integration.sh` if implementation changed an `integration_ci` seam
- [ ] T071 Run `git diff --check` and `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational and is the MVP.
- **User Story 2 (Phase 4)**: Depends on Foundational; may run after or alongside User Story 1 if same-file workflow edits are coordinated.
- **User Story 3 (Phase 5)**: Depends on Foundational; can run after User Story 1 for clearer lifecycle semantics.
- **User Story 4 (Phase 6)**: Depends on Foundational and should run after any workflow-shape edits it gates.
- **Polish (Phase 7)**: Depends on the desired story set being complete.

### User Story Dependencies

- **US1 Control Sessions Without Leaks**: MVP. No dependency on US2-US4 after Foundational.
- **US2 Keep Long-Lived Sessions Safe**: Independent behavior after Foundational, but same-file edits in `agent_session.py` must be coordinated with US1.
- **US3 Recover and Observe Sessions Safely**: Independent behavior after Foundational, but final observability semantics should reflect US1 cancel/terminate outcomes.
- **US4 Gate Workflow Changes Before Rollout**: Can start after Foundational, but final replay/version gates must cover the workflow-shape changes introduced by US1-US3.

### Within Each User Story

- Tests are listed before implementation and should be written first.
- Runtime schema/activity/worker tasks in Phase 2 must land before story-specific code depends on them.
- Workflow tests precede workflow edits.
- Runtime/controller tests precede runtime/controller edits.
- Story validation commands run before moving to broad rollout or final polish.

## Parallel Opportunities

- T002 and T003 can run in parallel with T001.
- T004 and T005 can run in parallel with T006 and T007.
- T008 and T009 can run in parallel with schema and activity route work.
- US1 test tasks T010 through T015 can run in parallel because they target distinct behavior and files.
- US2 test tasks T026 through T030 can run in parallel, but implementation tasks T031 through T035 should be serialized around `agent_session.py`.
- US3 test tasks T038 through T044 can run in parallel across workflow, activity, controller, supervisor, reconcile, and client tests.
- US4 test tasks T055 through T058 can run in parallel; implementation tasks T059 through T063 should follow the workflow-shape decisions from US1-US3.

## Parallel Example: User Story 1

```text
Task: "T010 Add workflow-boundary accepted-path tests in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "T012 Add runtime-level control behavior tests in tests/unit/services/temporal/runtime/test_codex_session_runtime.py"
Task: "T013 Add controller-level idempotency and cleanup tests in tests/unit/services/temporal/runtime/test_managed_session_controller.py"
Task: "T015 Add activity-wrapper non-retryable and heartbeat tests in tests/unit/workflows/temporal/test_agent_runtime_activities.py"
```

## Parallel Example: User Story 3

```text
Task: "T038 Add bounded workflow metadata tests in tests/unit/workflows/temporal/workflows/test_agent_session.py"
Task: "T040 Add durable publication tests in tests/unit/services/temporal/runtime/test_managed_session_controller.py"
Task: "T042 Add reconcile workflow tests in tests/unit/workflows/temporal/workflows/test_managed_session_reconcile.py"
Task: "T043 Add reconcile schedule tests in tests/unit/workflows/temporal/test_client_schedules.py"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Write US1 tests T010 through T015.
3. Implement US1 runtime changes T016 through T024.
4. Run US1 validation T025.
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
4. `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime` passes.
5. `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passes, with any skipped integration-ci work explicitly justified.
