# Tasks: Docker-Out-of-Docker Workload Launcher

**Input**: Design documents from `/specs/151-dood-workload-launcher/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/workload-launcher-contract.md, quickstart.md

**Tests**: Required. The feature request and FR-012 require production runtime code changes plus validation tests, so each user story includes tests that should be written first and fail before implementation.

**Organization**: Tasks are grouped by user story to keep each increment independently testable. User Story 1 is the MVP launcher path; User Story 2 completes the P1 cleanup safety path; User Story 3 adds the P2 fleet-routing boundary.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other tasks in the same phase when assigned to different files
- **[Story]**: User story label from `spec.md` for story-specific tasks
- Each task includes an exact repository path or command path

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the Phase 1 workload contract and Phase 2 execution boundary before adding launcher behavior.

- [X] T001 Review existing workload request, result, and runner-profile contracts in `moonmind/schemas/workload_models.py`
- [X] T002 Review existing runner profile registry behavior in `moonmind/workloads/registry.py`
- [X] T003 [P] Review Phase 2 acceptance and verification commands in `specs/151-dood-workload-launcher/quickstart.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the failing tests that define the shared launcher, activity, and worker-routing boundary before implementation.

**CRITICAL**: No user story implementation should begin until these boundary tests exist.

- [X] T004 [P] Add failing launcher construction and result-shape tests in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T005 [P] Add failing `workload.run` activity boundary tests in `tests/unit/workflows/temporal/test_workload_run_activity.py`
- [X] T006 [P] Add failing activity catalog tests for `workload.run` in `tests/unit/workflows/temporal/test_activity_catalog.py`
- [X] T007 [P] Add failing worker topology tests for `docker_workload` capability in `tests/unit/workflows/temporal/test_temporal_workers.py`
- [X] T008 [P] Add failing worker runtime dependency-injection tests in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`

**Checkpoint**: Test coverage captures the desired runtime boundary and should fail against the unimplemented launcher.

---

## Phase 3: User Story 1 - Run a Validated Workload Container (Priority: P1) MVP

**Goal**: Launch one bounded Docker workload container from an already validated request, run it against the task workspace, capture bounded execution metadata, and remove the ephemeral container on completion.

**Independent Test**: Submit a valid workload request using an approved runner profile and verify the launcher builds deterministic Docker arguments, runs in the task repo directory, returns selected profile/image, timing, exit status, bounded stdout/stderr diagnostics, and removes the container.

### Tests for User Story 1

> Write these tests first and verify they fail before implementation.

- [X] T009 [P] [US1] Add Docker run argument tests for deterministic names, labels, workspace mount, approved artifacts directory handling, cache mounts, workdir, env, timeout, and resource controls in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T010 [P] [US1] Add stream capture, bounded diagnostics, and normal-completion container removal tests for success and non-zero exits in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T011 [P] [US1] Add `workload.run` request-envelope validation and launcher-call tests in `tests/unit/workflows/temporal/test_workload_run_activity.py`

### Implementation for User Story 1

- [X] T012 [US1] Implement deterministic workload identity, ownership labels, and Docker argument construction in `moonmind/workloads/docker_launcher.py`
- [X] T013 [US1] Implement bounded Docker process execution, stdout/stderr capture, exit-code capture, timing, diagnostics metadata, and cleanup-policy removal after normal completion in `moonmind/workloads/docker_launcher.py`
- [X] T014 [US1] Implement profile-approved workspace mount, approved artifacts directory handling, cache mount resolution, and task repo workdir handling in `moonmind/workloads/docker_launcher.py`
- [X] T015 [US1] Export `DockerWorkloadLauncher` and launcher result helpers from `moonmind/workloads/__init__.py`
- [X] T016 [US1] Bind the `workload.run` activity to validated request parsing and launcher execution in `moonmind/workflows/temporal/activity_runtime.py`

**Checkpoint**: User Story 1 can launch and complete one bounded workload independently of managed-session operations.

---

## Phase 4: User Story 2 - Clean Up Timed-Out or Canceled Containers (Priority: P1)

**Goal**: Stop, terminate, remove, and report timed-out or canceled workload containers without leaving routine orphan containers.

**Independent Test**: Run a workload that exceeds its timeout or is canceled during execution and verify bounded cleanup attempts, removed ephemeral containers, timed-out or canceled metadata, and label-based orphan lookup.

### Tests for User Story 2

> Write these tests first and verify they fail before implementation.

- [X] T017 [P] [US2] Add timeout cleanup tests for stop, kill, remove, timeout status, and timeout diagnostics in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T018 [P] [US2] Add cancellation cleanup tests for bounded stop, kill, remove, and cancellation propagation in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T019 [P] [US2] Add orphan lookup tests for MoonMind ownership label filters in `tests/unit/workloads/test_docker_workload_launcher.py`

### Implementation for User Story 2

- [X] T020 [US2] Implement Docker cleanup utility methods for `docker stop`, `docker kill`, `docker rm`, and already-missing containers in `moonmind/workloads/docker_launcher.py`
- [X] T021 [US2] Implement timeout handling with bounded stop, terminate, remove, and timed-out `WorkloadResult` metadata in `moonmind/workloads/docker_launcher.py`
- [X] T022 [US2] Implement cancellation handling with bounded cleanup and cancellation propagation in `moonmind/workloads/docker_launcher.py`
- [X] T023 [US2] Implement operator-usable orphan lookup by MoonMind workload labels in `moonmind/workloads/docker_launcher.py`

**Checkpoint**: User Story 2 cleanup behavior is independently testable and no routine timeout/cancel path leaves a running ephemeral container.

---

## Phase 5: User Story 3 - Route Workloads to the Docker-Capable Fleet (Priority: P2)

**Goal**: Expose Docker workload execution as a distinct `docker_workload` capability on the existing Docker-capable `agent_runtime` worker fleet without overloading managed-session verbs.

**Independent Test**: Inspect activity catalog and worker topology metadata and verify `workload.run` routes through the Docker-capable `agent_runtime` fleet while non-Docker fleets do not advertise `docker_workload`.

### Tests for User Story 3

> Write these tests first and verify they fail before implementation.

- [X] T024 [P] [US3] Add activity catalog assertions for `workload.run` activity name, task queue, and `docker_workload` capability in `tests/unit/workflows/temporal/test_activity_catalog.py`
- [X] T025 [P] [US3] Add worker topology assertions that only the `agent_runtime` fleet advertises `docker_workload` in `tests/unit/workflows/temporal/test_temporal_workers.py`
- [X] T026 [P] [US3] Add worker runtime assertions that the workload registry and launcher are initialized separately from managed-session controllers in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`

### Implementation for User Story 3

- [X] T027 [US3] Register `workload.run` as a dedicated activity on the `agent_runtime` queue with `docker_workload` capability metadata in `moonmind/workflows/temporal/activity_catalog.py`
- [X] T028 [US3] Add `docker_workload` capability to the Docker-capable `agent_runtime` fleet and keep it absent from non-Docker worker definitions in `moonmind/workflows/temporal/workers.py`
- [X] T029 [US3] Initialize workload profile registry and `DockerWorkloadLauncher` dependencies in the agent-runtime worker path in `moonmind/workflows/temporal/worker_runtime.py`
- [X] T030 [US3] Verify managed-session activity names remain unchanged and distinct from workload launching in `moonmind/workflows/temporal/activity_runtime.py`

**Checkpoint**: User Story 3 proves Docker workloads are a separate runtime capability and not a managed-session lifecycle operation.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify runtime scope, update implementation tracking, and run the requested validation suite.

- [X] T031 [P] Update Phase 2 implementation tracking notes in `docs/ManagedAgents/DockerOutOfDocker.md`
- [X] T032 [P] Run focused Phase 2 verification with `./tools/test_unit.sh --python-only tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_activity_catalog.py tests/unit/workflows/temporal/test_temporal_workers.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_workload_run_activity.py`
- [X] T033 Run full unit verification with `./tools/test_unit.sh`
- [X] T034 Run runtime scope validation with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T035 Run runtime diff validation with `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies; starts immediately.
- **Phase 2 Foundational**: Depends on Phase 1; defines failing boundary tests and blocks implementation.
- **Phase 3 User Story 1**: Depends on Phase 2; delivers the MVP one-shot workload launcher.
- **Phase 4 User Story 2**: Depends on Phase 2 and can be developed alongside US1 after shared launcher test scaffolding exists, but final cleanup behavior depends on the launcher surface from US1.
- **Phase 5 User Story 3**: Depends on Phase 2 and can be developed alongside US1/US2 because it touches Temporal routing files.
- **Phase 6 Polish**: Depends on all implemented stories selected for delivery.

### User Story Dependencies

- **User Story 1 (P1)**: MVP. No dependency on other user stories after foundational tests.
- **User Story 2 (P1)**: Depends on the launcher surface from US1 but remains independently testable through timeout, cancellation, and orphan lookup tests.
- **User Story 3 (P2)**: No runtime dependency on US2; depends on the `workload.run` activity boundary and can be completed after or alongside US1.

### Within Each User Story

- Write tests first and confirm failure.
- Implement production runtime code after the failing tests exist.
- Run the story-specific tests before moving to a broader validation command.
- Keep workload launch and managed-session lifecycle behavior in separate modules and activity names.

### Parallel Opportunities

- T003 can run after T001/T002 are assigned because it only reads spec artifacts.
- T004, T005, T006, T007, and T008 can run in parallel because they touch separate test files.
- T009, T010, and T011 can run in parallel within US1.
- T017, T018, and T019 can run in parallel within US2.
- T024, T025, and T026 can run in parallel within US3.
- US3 implementation tasks T027-T029 can proceed in parallel with US2 after foundational tests are in place because they touch Temporal routing and worker initialization rather than launcher cleanup internals.

---

## Parallel Example: User Story 1

```bash
# These test-writing tasks can be assigned together:
Task: "T009 [P] [US1] Add Docker run argument tests in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T011 [P] [US1] Add workload.run request-envelope validation tests in tests/unit/workflows/temporal/test_workload_run_activity.py"
```

## Parallel Example: User Story 2

```bash
# These cleanup-focused tests can be assigned together:
Task: "T017 [P] [US2] Add timeout cleanup tests in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T019 [P] [US2] Add orphan lookup tests in tests/unit/workloads/test_docker_workload_launcher.py"
```

## Parallel Example: User Story 3

```bash
# These routing tests can be assigned together:
Task: "T024 [P] [US3] Add activity catalog assertions in tests/unit/workflows/temporal/test_activity_catalog.py"
Task: "T025 [P] [US3] Add worker topology assertions in tests/unit/workflows/temporal/test_temporal_workers.py"
Task: "T026 [P] [US3] Add worker runtime assertions in tests/unit/workflows/temporal/test_temporal_worker_runtime.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup review.
2. Complete Phase 2 foundational failing tests.
3. Complete Phase 3 User Story 1 launcher implementation.
4. Stop and validate US1 with:

```bash
./tools/test_unit.sh --python-only \
  tests/unit/workloads/test_docker_workload_launcher.py \
  tests/unit/workflows/temporal/test_workload_run_activity.py
```

### Complete P1 Safety Scope

1. Complete User Story 2 cleanup tests and implementation.
2. Validate launcher behavior with:

```bash
./tools/test_unit.sh --python-only tests/unit/workloads/test_docker_workload_launcher.py
```

### Complete Phase 2 Boundary

1. Complete User Story 3 routing tests and implementation.
2. Run the focused quickstart suite from `specs/151-dood-workload-launcher/quickstart.md`.
3. Run full unit verification with `./tools/test_unit.sh`.
4. Run runtime scope validation with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
