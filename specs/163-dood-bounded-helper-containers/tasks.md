# Tasks: DooD Bounded Helper Containers

**Input**: Design documents from `/specs/163-dood-bounded-helper-containers/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/bounded-helper-workload-contract.md`, `quickstart.md`
**Tests**: Required. The feature is runtime work and explicitly requires production code changes plus validation tests. Use TDD: add failing tests before implementation tasks in each user story.

**Organization**: Tasks are grouped by user story so each story can be implemented, validated, and demonstrated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other tasks in the same phase when touching different files.
- **[Story]**: User-story label from `spec.md` for story-specific tasks.
- Every task includes an exact repository path or validation command.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing DooD workload surfaces and align the implementation slice before adding helper behavior.

- [X] T001 Review current workload contracts in `moonmind/schemas/workload_models.py` against `specs/163-dood-bounded-helper-containers/data-model.md`
- [X] T002 Review current runner-profile policy validation in `moonmind/workloads/registry.py` against `specs/163-dood-bounded-helper-containers/contracts/bounded-helper-workload-contract.md`
- [X] T003 Review current Docker workload launcher and janitor behavior in `moonmind/workloads/docker_launcher.py`
- [X] T004 [P] Review executable tool bridge behavior in `moonmind/workloads/tool_bridge.py` for future helper tool exposure boundaries
- [X] T005 [P] Review Temporal workload activity boundary in `moonmind/workflows/temporal/activity_runtime.py`
- [X] T006 [P] Confirm validation commands and evidence expectations in `specs/163-dood-bounded-helper-containers/quickstart.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared helper contract and policy tests before any story-specific implementation.

**CRITICAL**: No user story implementation should begin until these tests exist and fail for the expected missing helper behavior.

- [X] T007 [P] Add failing bounded helper profile/request contract tests in `tests/unit/workloads/test_workload_contract.py`
- [X] T008 [P] Add failing helper launcher and janitor boundary tests in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T009 [P] Add failing helper executable-tool bridge contract tests in `tests/unit/workloads/test_workload_tool_bridge.py`
- [X] T010 [P] Add failing Temporal workload activity boundary tests for helper invocation shape in `tests/unit/workflows/temporal/test_workload_run_activity.py`
- [X] T011 Add shared helper status/result expectations to workload result assertions in `tests/unit/workloads/test_workload_contract.py`

**Checkpoint**: Tests describe helper kind, TTL policy, readiness contract, helper identity, and teardown/cleanup expectations before production code changes.

---

## Phase 3: User Story 1 - Start a Bounded Helper (Priority: P1)

**Goal**: Validate and start a non-agent helper workload with explicit owner step, TTL, runner profile, artifacts location, and helper identity that does not become session identity.

**Independent Test**: Submit a helper request with an approved helper profile and verify bounded ownership metadata, deterministic helper name, TTL labels, artifact directory linkage, and session-context-as-grouping-only behavior.

### Tests for User Story 1

> Write these tests first and confirm they fail for the expected missing runtime behavior.

- [X] T012 [P] [US1] Add helper profile validation tests for required `kind`, `helperTtlSeconds`, `maxHelperTtlSeconds`, and `readinessProbe` in `tests/unit/workloads/test_workload_contract.py`
- [X] T013 [P] [US1] Add helper request validation tests for owner step, explicit `ttlSeconds`, profile maximum TTL, artifacts path, and optional session grouping in `tests/unit/workloads/test_workload_contract.py`
- [X] T014 [P] [US1] Add deterministic helper container name and `moonmind.kind=bounded_service` label tests in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T015 [P] [US1] Add helper detached Docker run argument tests for safe mounts, resources, env allowlist, no privileged launch, and TTL labels in `tests/unit/workloads/test_docker_workload_launcher.py`

### Implementation for User Story 1

- [X] T016 [US1] Add bounded helper workload kind, helper status values, readiness probe model, TTL fields, and deterministic helper name helper in `moonmind/schemas/workload_models.py`
- [X] T017 [US1] Implement helper profile and helper request policy validation in `moonmind/workloads/registry.py`
- [X] T018 [US1] Implement helper ownership metadata, helper container labels, and helper TTL expiration labels in `moonmind/workloads/docker_launcher.py`
- [X] T019 [US1] Implement detached helper run-argument construction in `moonmind/workloads/docker_launcher.py`
- [X] T020 [US1] Ensure helper metadata treats `sessionId`, `sessionEpoch`, and `sourceTurnId` as grouping context only in `moonmind/workloads/docker_launcher.py`

### Validation for User Story 1

- [X] T021 [US1] Verify User Story 1 with `./tools/test_unit.sh --python-only tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py`

**Checkpoint**: A bounded helper can be validated and started independently without becoming a managed session.

---

## Phase 4: User Story 2 - Prove Helper Readiness (Priority: P1)

**Goal**: Evaluate bounded readiness checks and return ready or unhealthy outcomes with bounded diagnostics.

**Independent Test**: Launch a helper with a readiness contract and verify readiness succeeds only after the probe passes; verify unhealthy result after retries or timeout.

### Tests for User Story 2

> Write these tests first and confirm they fail for the expected missing readiness behavior.

- [X] T022 [P] [US2] Add readiness success tests for bounded Docker exec probes in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T023 [P] [US2] Add readiness retry, timeout, exhausted retry, and unhealthy result tests in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T024 [P] [US2] Add diagnostics redaction tests proving readiness outputs omit env values, secrets, prompts, transcripts, and unbounded logs in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T025 [P] [US2] Add artifact publication tests for helper stdout, stderr, diagnostics, and partial publication failure in `tests/unit/workloads/test_docker_workload_launcher.py`

### Implementation for User Story 2

- [X] T026 [US2] Implement helper readiness probe execution and bounded retry handling in `moonmind/workloads/docker_launcher.py`
- [X] T027 [US2] Implement ready, unhealthy, timed-out, and canceled helper result metadata in `moonmind/workloads/docker_launcher.py`
- [X] T028 [US2] Publish bounded helper readiness artifacts and diagnostics in `moonmind/workloads/docker_launcher.py`
- [X] T029 [US2] Add readiness policy denial/error mapping for helper launch failures in `moonmind/workloads/registry.py` and `moonmind/workloads/docker_launcher.py`

### Validation for User Story 2

- [X] T030 [US2] Verify User Story 2 with `./tools/test_unit.sh --python-only tests/unit/workloads/test_docker_workload_launcher.py`

**Checkpoint**: Helper readiness is observable and bounded, and failed readiness is diagnosable from artifacts/metadata.

---

## Phase 5: User Story 3 - Use and Tear Down the Helper Window (Priority: P1)

**Goal**: Keep a ready helper available across multiple dependent sub-steps in one bounded window, then explicitly tear it down with stop/kill/remove diagnostics.

**Independent Test**: Start a helper, simulate two dependent sub-step references to the same helper ownership record, then tear down the helper and verify final diagnostics and cleanup actions.

### Tests for User Story 3

> Write these tests first and confirm they fail for the expected missing lifecycle behavior.

- [X] T031 [P] [US3] Add multi-sub-step helper survival test using the same helper ownership record in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T032 [P] [US3] Add explicit helper teardown stop/kill/remove tests in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T033 [P] [US3] Add cancellation and timeout teardown tests for helper start, readiness, and active helper window in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T034 [P] [US3] Add helper start/stop executable tool mapping tests in `tests/unit/workloads/test_workload_tool_bridge.py`
- [X] T035 [P] [US3] Add Temporal activity boundary tests for helper start/stop invocation and result shape in `tests/unit/workflows/temporal/test_workload_run_activity.py`

### Implementation for User Story 3

- [X] T036 [US3] Implement `DockerWorkloadLauncher.start_helper()` lifecycle orchestration in `moonmind/workloads/docker_launcher.py`
- [X] T037 [US3] Implement `DockerWorkloadLauncher.stop_helper()` teardown behavior and diagnostics in `moonmind/workloads/docker_launcher.py`
- [X] T038 [US3] Add helper start/stop tool definitions and schemas in `moonmind/workloads/tool_bridge.py`
- [X] T039 [US3] Register helper tool handlers and map helper inputs to validated workload requests in `moonmind/workloads/tool_bridge.py`
- [X] T040 [US3] Wire helper start/stop execution through the existing workload activity boundary in `moonmind/workflows/temporal/activity_runtime.py`
- [X] T041 [US3] Preserve one-shot workload behavior and existing `container.run_workload` / `unreal.run_tests` results while adding helper lifecycle handling in `moonmind/workloads/tool_bridge.py`

### Validation for User Story 3

- [X] T042 [US3] Verify User Story 3 with `./tools/test_unit.sh --python-only tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py`

**Checkpoint**: A helper can be launched, used across a bounded window, and explicitly torn down through the control-plane workload path.

---

## Phase 6: User Story 4 - Sweep Expired Helpers (Priority: P2)

**Goal**: Remove expired MoonMind-owned helper containers by ownership and TTL metadata without touching fresh helpers, one-shot workloads, session containers, or unrelated containers.

**Independent Test**: Create mixed helper/non-helper cleanup fixtures and verify only expired helper containers are removed.

### Tests for User Story 4

> Write these tests first and confirm they fail for the expected missing cleanup behavior.

- [X] T043 [P] [US4] Add expired helper sweep tests for `moonmind.kind=bounded_service` and `moonmind.expires_at` in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T044 [P] [US4] Add fresh helper, one-shot workload, session container, unrelated container, malformed TTL, and missing TTL skip tests in `tests/unit/workloads/test_docker_workload_launcher.py`
- [X] T045 [P] [US4] Add cleanup diagnostics tests for inspected, removed, skipped, and ownership basis metadata in `tests/unit/workloads/test_docker_workload_launcher.py`

### Implementation for User Story 4

- [X] T046 [US4] Implement helper-specific expired-container sweep in `moonmind/workloads/docker_launcher.py`
- [X] T047 [US4] Implement helper cleanup diagnostics and safe skip behavior for malformed or fresh TTL metadata in `moonmind/workloads/docker_launcher.py`
- [X] T048 [US4] Ensure helper cleanup does not alter one-shot workload cleanup semantics in `moonmind/workloads/docker_launcher.py`

### Validation for User Story 4

- [X] T049 [US4] Verify User Story 4 with `./tools/test_unit.sh --python-only tests/unit/workloads/test_docker_workload_launcher.py`

**Checkpoint**: Expired helper cleanup is reliable and narrowly scoped.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Confirm scope, docs, security posture, and full validation after all stories.

- [X] T050 [P] Update Phase 7 completion notes in `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`
- [X] T051 [P] Update helper quickstart evidence and any changed validation commands in `specs/163-dood-bounded-helper-containers/quickstart.md`
- [X] T052 [P] Scan helper diagnostics and tool outputs for secret-like values in `moonmind/workloads/docker_launcher.py` and `moonmind/workloads/tool_bridge.py`
- [X] T053 Run focused feature verification with `./tools/test_unit.sh --python-only tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py`
- [X] T054 Run full unit verification with `./tools/test_unit.sh`
- [X] T055 Run runtime scope validation with `SPECIFY_FEATURE=163-dood-bounded-helper-containers .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T056 Run runtime diff validation with `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies; starts immediately.
- **Phase 2 Foundational**: Depends on Phase 1 review; blocks story implementation.
- **Phase 3 User Story 1**: Depends on Phase 2; establishes helper contracts and launch identity.
- **Phase 4 User Story 2**: Depends on User Story 1 helper launch shape.
- **Phase 5 User Story 3**: Depends on User Stories 1 and 2 for helper start and readiness semantics.
- **Phase 6 User Story 4**: Depends on User Story 1 labels/TTL; can proceed after helper labels are implemented.
- **Phase 7 Polish**: Depends on all selected user stories.

### User Story Dependencies

- **User Story 1 (P1)**: MVP helper validation and detached launch identity. No dependency on other stories after foundation.
- **User Story 2 (P1)**: Requires helper launch and readiness profile contract from US1.
- **User Story 3 (P1)**: Requires helper launch/readiness behavior from US1 and US2.
- **User Story 4 (P2)**: Requires helper ownership labels and TTL metadata from US1; independent of tool exposure from US3.

### Within Each User Story

- Write automated tests first and confirm they fail for the expected reason.
- Implement schema/model changes before registry validation.
- Implement registry validation before launcher execution.
- Implement launcher behavior before tool/activity exposure.
- Run story-specific verification before moving to broader validation.
- Keep helper lifecycle behavior separate from managed-session code and true `MoonMind.AgentRun` contracts.

### Parallel Opportunities

- T004, T005, and T006 can run in parallel during setup.
- T007, T008, T009, and T010 can run in parallel because they touch separate test files.
- T012-T015 can run in parallel within US1.
- T022-T025 can run in parallel within US2.
- T031-T035 can run in parallel within US3.
- T043-T045 can run in parallel within US4.
- T050-T052 can run in parallel during polish.

---

## Parallel Example: User Story 1

```bash
Task: "T012 [P] [US1] Add helper profile validation tests in tests/unit/workloads/test_workload_contract.py"
Task: "T014 [P] [US1] Add deterministic helper labels tests in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T015 [P] [US1] Add detached Docker run argument tests in tests/unit/workloads/test_docker_workload_launcher.py"
```

## Parallel Example: User Story 3

```bash
Task: "T031 [P] [US3] Add multi-sub-step helper survival test in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T034 [P] [US3] Add helper start/stop tool mapping tests in tests/unit/workloads/test_workload_tool_bridge.py"
Task: "T035 [P] [US3] Add Temporal activity boundary tests in tests/unit/workflows/temporal/test_workload_run_activity.py"
```

---

## Implementation Strategy

### MVP First

1. Complete setup and foundational tests.
2. Complete User Story 1 to validate and start a bounded helper with safe identity/TTL metadata.
3. Verify with:

```bash
./tools/test_unit.sh --python-only \
  tests/unit/workloads/test_workload_contract.py \
  tests/unit/workloads/test_docker_workload_launcher.py
```

### Complete P1 Runtime Scope

1. Complete User Story 2 readiness behavior.
2. Complete User Story 3 helper window and teardown behavior.
3. Verify with focused workload/tool/activity tests.

### Complete Cleanup Safety

1. Complete User Story 4 expired-helper sweeper behavior.
2. Run focused cleanup tests and then full unit verification.

### Final Validation

1. Run focused feature verification.
2. Run `./tools/test_unit.sh`.
3. Run runtime scope validation for tasks and diff.
