# Tasks: DooD Workload Observability

**Input**: Design documents from `/specs/155-dood-workload-observability/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/workload-observability-contract.md, quickstart.md

**Tests**: Validation tests are required by FR-016. Write story tests first and verify they fail before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other tasks in the same phase because it touches different files and has no dependency on incomplete tasks.
- **[Story]**: Maps to user stories from spec.md.
- Every task includes exact file paths.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing Phase 1-3 workload surfaces and prepare runtime scope.

- [ ] T001 Review Phase 1-3 workload contracts and launcher surfaces in moonmind/schemas/workload_models.py, moonmind/workloads/docker_launcher.py, and moonmind/workloads/tool_bridge.py
- [ ] T002 [P] Review workflow step artifact projection behavior in moonmind/workflows/temporal/workflows/run.py and tests/unit/workflows/temporal/workflows/test_run_step_ledger.py
- [ ] T003 [P] Review task detail artifact/API presentation surfaces in api_service/api/routers/task_runs.py and frontend/src/entrypoints/task-detail.tsx

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared model and result contract support required before any user story can publish or project workload evidence.

**CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T004 [P] Add failing WorkloadRequest declared output validation tests in tests/unit/workloads/test_workload_contract.py
- [ ] T005 [P] Add failing WorkloadResult artifact reference serialization tests in tests/unit/workloads/test_workload_contract.py
- [ ] T006 Implement declaredOutputs validation and workload artifact reference fields in moonmind/schemas/workload_models.py
- [ ] T007 Update Docker-backed tool output schema for stdout/stderr/diagnostics refs and workload metadata in moonmind/workloads/tool_bridge.py
- [ ] T008 Run focused foundational tests with pytest tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py -q --tb=short

**Checkpoint**: Shared workload request/result contracts support artifact refs, declared outputs, and bounded metadata.

---

## Phase 3: User Story 1 - Diagnose Workload Runs from Artifacts (Priority: P1) MVP

**Goal**: Successful, failed, timed-out, and canceled workload runs publish durable stdout, stderr, and diagnostics evidence.

**Independent Test**: Execute launcher unit tests for successful and failed workloads and verify stdout, stderr, diagnostics, and final metadata are readable from artifact refs without inspecting the container.

### Tests for User Story 1

- [ ] T009 [P] [US1] Add failing success and non-zero exit artifact publication tests in tests/unit/workloads/test_docker_workload_launcher.py
- [ ] T010 [P] [US1] Add failing timeout/cancel diagnostics publication tests in tests/unit/workloads/test_docker_workload_launcher.py
- [ ] T011 [P] [US1] Add failing artifact publication failure/degraded observability tests in tests/unit/workloads/test_docker_workload_launcher.py

### Implementation for User Story 1

- [ ] T012 [US1] Implement durable stdout/stderr/diagnostics publication in moonmind/workloads/docker_launcher.py
- [ ] T013 [US1] Implement bounded workload diagnostics metadata including profile, image, exit code, duration, timeout reason, cancel reason, labels, task run, step, attempt, and tool name in moonmind/workloads/docker_launcher.py
- [ ] T014 [US1] Implement operator-visible artifact publication failure handling in moonmind/workloads/docker_launcher.py
- [ ] T015 [US1] Run User Story 1 tests with pytest tests/unit/workloads/test_docker_workload_launcher.py -q --tb=short

**Checkpoint**: User Story 1 is independently functional and workload outcomes are diagnosable from durable artifacts.

---

## Phase 4: User Story 2 - Link Workload Outputs to Producing Steps (Priority: P1)

**Goal**: Workload artifact refs and declared outputs are linked to the producing task run, step, attempt, and tool invocation.

**Independent Test**: Execute a Docker-backed workload tool step and verify the normal tool result and step projection include workload refs, selected profile, image ref, status, and declared output handling.

### Tests for User Story 2

- [ ] T016 [P] [US2] Add failing declared output success/missing-output tests in tests/unit/workloads/test_docker_workload_launcher.py
- [ ] T017 [P] [US2] Add failing workload tool output mapping tests in tests/unit/workloads/test_workload_tool_bridge.py
- [ ] T018 [P] [US2] Add failing workflow step-ledger workload artifact projection tests in tests/unit/workflows/temporal/workflows/test_run_step_ledger.py

### Implementation for User Story 2

- [ ] T019 [US2] Implement declared output artifact collection and missing-output diagnostics in moonmind/workloads/docker_launcher.py
- [ ] T020 [US2] Map stdoutRef, stderrRef, diagnosticsRef, outputRefs, and workloadMetadata into normal tool results in moonmind/workloads/tool_bridge.py
- [ ] T021 [US2] Preserve workload artifact refs in producing step ledger/projection metadata in moonmind/workflows/temporal/workflows/run.py
- [ ] T022 [US2] Run User Story 2 tests with pytest tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/workflows/test_run_step_ledger.py -q --tb=short

**Checkpoint**: User Stories 1 and 2 are independently functional, and workload outputs are step-owned.

---

## Phase 5: User Story 3 - Preserve Managed Session Boundaries (Priority: P2)

**Goal**: Workloads launched from managed-session-assisted steps expose session association context without becoming session artifacts or managed sessions.

**Independent Test**: Execute workload tool conversion with session metadata and verify session context is present only as grouping metadata while session continuity artifact classes remain absent.

### Tests for User Story 3

- [ ] T023 [P] [US3] Add failing session association validation tests in tests/unit/workloads/test_workload_contract.py
- [ ] T024 [P] [US3] Add failing session association tool result tests in tests/unit/workloads/test_workload_tool_bridge.py
- [ ] T025 [P] [US3] Add failing managed-session boundary workflow tests in tests/unit/workflows/temporal/workflows/test_run_integration.py

### Implementation for User Story 3

- [ ] T026 [US3] Enforce sessionEpoch/sourceTurnId require sessionId and reject session continuity declared output classes in moonmind/schemas/workload_models.py
- [ ] T027 [US3] Add sessionContext as association-only workload metadata in moonmind/workloads/docker_launcher.py
- [ ] T028 [US3] Preserve sessionContext in tool result progress/outputs without creating session continuity refs in moonmind/workloads/tool_bridge.py
- [ ] T029 [US3] Run User Story 3 tests with pytest tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/workflows/test_run_integration.py -q --tb=short

**Checkpoint**: Session association is visible as grouping context only, and workload containers remain outside managed-session identity.

---

## Phase 6: User Story 4 - View Workload Evidence Through Existing Detail Surfaces (Priority: P3)

**Goal**: API and UI consumers can inspect workload logs, diagnostics, output refs, and live-tail fallback behavior through normal task/execution detail surfaces.

**Independent Test**: Query task/execution detail for a workload-producing step and verify workload metadata and artifact refs are present without presenting the workload as a managed session.

### Tests for User Story 4

- [ ] T030 [P] [US4] Add failing task-run API workload metadata projection tests in tests/unit/api/routers/test_task_runs.py
- [ ] T031 [P] [US4] Add failing task detail workload artifact rendering tests in frontend/src/entrypoints/task-detail.test.tsx
- [ ] T032 [P] [US4] Add failing observability event/live-tail fallback tests for workload artifacts in tests/unit/api/routers/test_task_runs.py

### Implementation for User Story 4

- [ ] T033 [US4] Expose workload metadata and artifact refs in task-run detail/projection responses in api_service/api/routers/task_runs.py
- [ ] T034 [US4] Render workload stdout, stderr, diagnostics, and declared output refs in the task detail experience in frontend/src/entrypoints/task-detail.tsx
- [ ] T035 [US4] Preserve artifact-backed fallback when live workload tail data is unavailable in api_service/api/routers/task_runs.py
- [ ] T036 [US4] Run User Story 4 tests with pytest tests/unit/api/routers/test_task_runs.py -q --tb=short and npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx

**Checkpoint**: Operators can inspect workload evidence through existing detail surfaces while artifacts remain durable truth.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, cleanup, and cross-story regression checks.

- [ ] T037 [P] Update workload observability contract notes if implementation changes contract details in specs/155-dood-workload-observability/contracts/workload-observability-contract.md
- [ ] T038 [P] Add or update concise operator-facing documentation references only if runtime behavior creates new visible fields in docs/ManagedAgents/DockerOutOfDocker.md
- [ ] T039 Run quickstart validation steps from specs/155-dood-workload-observability/quickstart.md
- [ ] T040 Run full unit verification with MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
- [ ] T041 Run runtime scope gate with .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup; blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational; MVP.
- **User Story 2 (Phase 4)**: Depends on Foundational and can be implemented after US1 tests define artifact refs; declared-output work may proceed in parallel with US1 implementation if helpers are coordinated.
- **User Story 3 (Phase 5)**: Depends on Foundational and can proceed in parallel with US1/US2 once shared metadata shape is stable.
- **User Story 4 (Phase 6)**: Depends on US2 output metadata/projection shape and benefits from US3 session-boundary metadata.
- **Polish (Phase 7)**: Depends on the desired story set being complete.

### User Story Dependencies

- **US1 Diagnose Workload Runs from Artifacts**: First MVP slice; no dependency on other user stories after Foundational.
- **US2 Link Workload Outputs to Producing Steps**: Depends on artifact refs from US1 but remains independently testable through tool and step projection fixtures.
- **US3 Preserve Managed Session Boundaries**: Depends on shared metadata fields; can be tested independently with session metadata fixtures.
- **US4 View Workload Evidence Through Existing Detail Surfaces**: Depends on output/projection contract from US2 and association context from US3.

### Parallel Opportunities

- T002 and T003 can run in parallel during setup.
- T004 and T005 can run in parallel before T006.
- T009, T010, and T011 can be written in parallel for US1.
- T016, T017, and T018 can be written in parallel for US2.
- T023, T024, and T025 can be written in parallel for US3.
- T030 and T031 can be written in parallel for API/UI in US4; T032 can run alongside T030 if fixture ownership is coordinated.
- T037 and T038 can run in parallel during polish.

---

## Parallel Example: User Story 1

```bash
Task: "T009 [US1] Add failing success and non-zero exit artifact publication tests in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T010 [US1] Add failing timeout/cancel diagnostics publication tests in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T011 [US1] Add failing artifact publication failure/degraded observability tests in tests/unit/workloads/test_docker_workload_launcher.py"
```

## Parallel Example: User Story 2

```bash
Task: "T016 [US2] Add failing declared output success/missing-output tests in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T017 [US2] Add failing workload tool output mapping tests in tests/unit/workloads/test_workload_tool_bridge.py"
Task: "T018 [US2] Add failing workflow step-ledger workload artifact projection tests in tests/unit/workflows/temporal/workflows/test_run_step_ledger.py"
```

## Parallel Example: User Story 4

```bash
Task: "T030 [US4] Add failing task-run API workload metadata projection tests in tests/unit/api/routers/test_task_runs.py"
Task: "T031 [US4] Add failing task detail workload artifact rendering tests in frontend/src/entrypoints/task-detail.test.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup and Phase 2 foundational schema/result contract work.
2. Complete Phase 3 User Story 1 tests and launcher implementation.
3. Stop and validate with pytest tests/unit/workloads/test_docker_workload_launcher.py -q --tb=short.
4. Confirm successful and failed workload runs can be diagnosed from durable artifacts.

### Incremental Delivery

1. Add US1 artifact publication and diagnostics.
2. Add US2 declared output and step-linkage projection.
3. Add US3 session association boundary enforcement.
4. Add US4 API/UI consumption.
5. Run quickstart and full unit verification.

### Runtime Validation Gate

Before completion, run:

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```
