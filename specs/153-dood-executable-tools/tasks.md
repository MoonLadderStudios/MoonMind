# Tasks: DooD Executable Tool Exposure

**Input**: Design documents from `/specs/153-dood-executable-tools/`  
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. The feature specification requires production runtime code changes plus validation tests, so each user story includes tests that should be written first and fail before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and validation of the generic workload tool, curated Unreal tool, and managed-session boundary.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other tasks in the same phase when assigned to different files
- **[Story]**: Which user story this task belongs to (`US1`, `US2`, `US3`)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm planned contracts and existing DooD runtime surfaces before adding executable tool exposure.

- [ ] T001 Review Phase 3 executable tool contracts in `specs/153-dood-executable-tools/contracts/dood-executable-tools-contract.md`.
- [ ] T002 Review existing workload request/result and runner profile contracts in `moonmind/schemas/workload_models.py`.
- [ ] T003 [P] Review existing runner profile validation in `moonmind/workloads/registry.py`.
- [ ] T004 [P] Review existing Docker launcher behavior in `moonmind/workloads/docker_launcher.py`.
- [ ] T005 [P] Review executable tool registry and dispatcher behavior in `moonmind/workflows/skills/tool_plan_contracts.py`, `moonmind/workflows/skills/tool_registry.py`, and `moonmind/workflows/skills/tool_dispatcher.py`.
- [ ] T006 [P] Review Temporal activity routing and worker topology in `moonmind/workflows/temporal/activity_catalog.py`, `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/temporal/worker_runtime.py`, and `moonmind/workflows/temporal/workers.py`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add failing boundary tests and scaffolding that all user stories depend on.

**CRITICAL**: No user story implementation should begin until this phase is complete.

- [ ] T007 [P] Add workload tool bridge test scaffolding in `tests/unit/workloads/test_workload_tool_bridge.py`.
- [ ] T008 [P] Add DooD tool definition registry assertions in `tests/unit/workflows/temporal/test_activity_runtime.py`.
- [ ] T009 [P] Add `docker_workload` skill capability routing assertions in `tests/unit/workflows/temporal/test_activity_catalog.py`.
- [ ] T010 [P] Add worker runtime handler-registration assertions in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`.
- [ ] T011 Create workload tool bridge module scaffolding in `moonmind/workloads/tool_bridge.py`.
- [ ] T012 Export workload tool bridge helpers from `moonmind/workloads/__init__.py`.

**Checkpoint**: Test scaffolding and module boundaries exist, and tests fail against the unimplemented Phase 3 behavior.

---

## Phase 3: User Story 1 - Run a Generic Workload Tool (Priority: P1) MVP

**Goal**: Expose `container.run_workload` as a controlled executable tool that converts validated tool inputs into a workload request and returns a normal tool result.

**Independent Test**: Execute a plan step whose tool is `container.run_workload` and verify the step is treated as an executable tool, validates its request against an approved runner profile, launches through the workload launcher, and returns a normal tool result.

### Tests for User Story 1

> Write these tests first and verify they fail before implementation.

- [ ] T013 [P] [US1] Add `container.run_workload` ToolDefinition generation tests in `tests/unit/workloads/test_workload_tool_bridge.py`.
- [ ] T014 [P] [US1] Add tests rejecting raw image, mount, device, and arbitrary Docker input fields in `tests/unit/workloads/test_workload_tool_bridge.py`.
- [ ] T015 [P] [US1] Add input-to-`WorkloadRequest` conversion and runner-profile validation tests in `tests/unit/workloads/test_workload_tool_bridge.py`.
- [ ] T016 [P] [US1] Add launcher invocation and `WorkloadResult` to normal `ToolResult` mapping tests in `tests/unit/workloads/test_workload_tool_bridge.py`.
- [ ] T017 [P] [US1] Add default registry payload tests for `container.run_workload` in `tests/unit/workflows/temporal/test_activity_runtime.py`.

### Implementation for User Story 1

- [ ] T018 [US1] Implement `container.run_workload` ToolDefinition payload generation in `moonmind/workloads/tool_bridge.py`.
- [ ] T019 [US1] Implement generic workload tool input shaping and execution-context defaults in `moonmind/workloads/tool_bridge.py`.
- [ ] T020 [US1] Implement runner-profile validation and launcher invocation for generic workload tool execution in `moonmind/workloads/tool_bridge.py`.
- [ ] T021 [US1] Implement `WorkloadResult` to `ToolResult` status/output mapping in `moonmind/workloads/tool_bridge.py`.
- [ ] T022 [US1] Wire DooD tool definition generation into default registry payload construction in `moonmind/workflows/temporal/activity_runtime.py`.

**Checkpoint**: `container.run_workload` can be executed through the executable tool dispatcher independently of managed-session operations.

---

## Phase 4: User Story 2 - Run Curated Unreal Tests (Priority: P1)

**Goal**: Expose `unreal.run_tests` as a curated domain tool that maps Unreal-specific inputs to the approved Unreal workload profile without exposing raw Docker controls.

**Independent Test**: Invoke `unreal.run_tests` with a repository workspace, artifacts location, project path, and test selector, then verify MoonMind maps the request to the curated Unreal runner profile and returns a normal tool result.

### Tests for User Story 2

> Write these tests first and verify they fail before implementation.

- [ ] T023 [P] [US2] Add `unreal.run_tests` ToolDefinition generation tests in `tests/unit/workloads/test_workload_tool_bridge.py`.
- [ ] T024 [P] [US2] Add Unreal domain input validation and required `projectPath` tests in `tests/unit/workloads/test_workload_tool_bridge.py`.
- [ ] T025 [P] [US2] Add curated Unreal command construction tests in `tests/unit/workloads/test_workload_tool_bridge.py`.
- [ ] T026 [P] [US2] Add default registry payload tests for `unreal.run_tests` in `tests/unit/workflows/temporal/test_activity_runtime.py`.

### Implementation for User Story 2

- [ ] T027 [US2] Implement `unreal.run_tests` ToolDefinition payload generation in `moonmind/workloads/tool_bridge.py`.
- [ ] T028 [US2] Implement Unreal tool input shaping, curated default profile selection, and command construction in `moonmind/workloads/tool_bridge.py`.
- [ ] T029 [US2] Integrate `unreal.run_tests` into workload tool handler registration in `moonmind/workloads/tool_bridge.py`.
- [ ] T030 [US2] Ensure default registry payload construction emits `unreal.run_tests` as a `docker_workload` executable tool in `moonmind/workflows/temporal/activity_runtime.py`.

**Checkpoint**: `unreal.run_tests` can be executed independently using the curated workload profile contract.

---

## Phase 5: User Story 3 - Preserve Managed Session Boundaries (Priority: P2)

**Goal**: Ensure Docker-backed workload tools route through the control-plane executable tool path and never through managed-session launch or `MoonMind.AgentRun` for generic workload containers.

**Independent Test**: Execute a managed-session-assisted plan step that requests a workload tool and verify the step routes through the control-plane tool path, not through managed-session launch or session-control operations.

### Tests for User Story 3

> Write these tests first and verify they fail before implementation.

- [ ] T031 [P] [US3] Add `docker_workload` skill capability routing tests in `tests/unit/workflows/temporal/test_activity_catalog.py`.
- [ ] T032 [P] [US3] Add worker topology assertions that `docker_workload` remains on the `agent_runtime` fleet in `tests/unit/workflows/temporal/test_temporal_workers.py`.
- [ ] T033 [P] [US3] Add agent-runtime worker handler-registration tests in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`.
- [ ] T034 [P] [US3] Add `MoonMind.Run` workflow-boundary routing test for a DooD skill step in `tests/unit/workflows/temporal/workflows/test_run_integration.py`.

### Implementation for User Story 3

- [ ] T035 [US3] Route `docker_workload` skill capability to the `agent_runtime` task queue in `moonmind/workflows/temporal/activity_catalog.py`.
- [ ] T036 [US3] Register DooD workload tool handlers only on the agent-runtime worker path in `moonmind/workflows/temporal/worker_runtime.py`.
- [ ] T037 [US3] Preserve `MoonMind.Run` skill-step routing through `mm.tool.execute` or `mm.skill.execute` without invoking `MoonMind.AgentRun` for workload tools in `moonmind/workflows/temporal/workflows/run.py`.
- [ ] T038 [US3] Confirm managed-session controller code remains unchanged for generic workload execution in `moonmind/workflows/temporal/runtime/managed_session_controller.py`.

**Checkpoint**: Docker-backed workload tools are invocable from plans or managed-session-assisted steps without granting the session container Docker authority or creating managed agent runs for workload containers.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify the runtime scope, documentation tracker alignment, and full test suite.

- [ ] T039 [P] Update Phase 3 completion tracking in `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`.
- [ ] T040 [P] Run focused Phase 3 verification from `specs/153-dood-executable-tools/quickstart.md` using `./tools/test_unit.sh --python-only tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_catalog.py tests/unit/workflows/temporal/test_activity_runtime.py::test_default_skill_registry_payload_uses_dood_tool_definitions tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_build_runtime_activities_reconciles_managed_sessions_only_on_agent_runtime_fleet tests/unit/workflows/temporal/workflows/test_run_integration.py::test_run_execution_stage_routes_dood_skill_tool_to_agent_runtime_activity`.
- [ ] T041 Run existing workload launcher regression coverage using `./tools/test_unit.sh --python-only tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_catalog.py tests/unit/workflows/temporal/test_temporal_workers.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_workload_run_activity.py`.
- [ ] T042 Run full unit verification with `./tools/test_unit.sh`.
- [ ] T043 Run runtime tasks scope validation with `SPECIFY_FEATURE=153-dood-executable-tools .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- [ ] T044 Run runtime diff scope validation with `SPECIFY_FEATURE=153-dood-executable-tools .specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies; starts immediately.
- **Phase 2 Foundational**: Depends on Phase 1; blocks all user stories.
- **Phase 3 User Story 1**: Depends on Phase 2 and delivers the MVP generic workload tool.
- **Phase 4 User Story 2**: Depends on Phase 2 and can proceed alongside User Story 1 after shared bridge scaffolding exists.
- **Phase 5 User Story 3**: Depends on Phase 2 and can proceed alongside User Stories 1 and 2 because it mostly touches routing and workflow boundaries.
- **Phase 6 Polish**: Depends on all selected user stories.

### User Story Dependencies

- **User Story 1 (P1)**: MVP; no dependency on other stories after foundational scaffolding.
- **User Story 2 (P1)**: Uses the shared bridge surface from User Story 1 but remains independently testable through curated Unreal inputs.
- **User Story 3 (P2)**: Depends on tool definitions and bridge registration being available, but validates the separate routing/session-boundary concern.

### Within Each User Story

- Write tests first and confirm failure.
- Implement production runtime behavior after failing tests exist.
- Run story-specific tests before moving to broader validation.
- Keep workload execution on the tool path; do not introduce managed-session or `MoonMind.AgentRun` aliases for workload containers.

### Parallel Opportunities

- T003, T004, T005, and T006 can run in parallel during setup because they read different files.
- T007, T008, T009, and T010 can run in parallel because they add tests in separate files.
- T013, T015, T016, and T017 can run in parallel if coordinated within test files.
- T023, T024, T025, and T026 can run in parallel with User Story 1 tests after shared scaffolding exists.
- T031, T032, T033, and T034 can run in parallel because they cover distinct routing boundaries.

---

## Parallel Example: User Story 1

```bash
Task: "T013 [P] [US1] Add container.run_workload ToolDefinition generation tests in tests/unit/workloads/test_workload_tool_bridge.py"
Task: "T017 [P] [US1] Add default registry payload tests for container.run_workload in tests/unit/workflows/temporal/test_activity_runtime.py"
```

## Parallel Example: User Story 2

```bash
Task: "T024 [P] [US2] Add Unreal domain input validation and required projectPath tests in tests/unit/workloads/test_workload_tool_bridge.py"
Task: "T026 [P] [US2] Add default registry payload tests for unreal.run_tests in tests/unit/workflows/temporal/test_activity_runtime.py"
```

## Parallel Example: User Story 3

```bash
Task: "T031 [P] [US3] Add docker_workload skill capability routing tests in tests/unit/workflows/temporal/test_activity_catalog.py"
Task: "T034 [P] [US3] Add MoonMind.Run workflow-boundary routing test for a DooD skill step in tests/unit/workflows/temporal/workflows/test_run_integration.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup review.
2. Complete Phase 2 foundational tests and bridge scaffolding.
3. Complete Phase 3 User Story 1 for `container.run_workload`.
4. Validate User Story 1 independently with:

```bash
./tools/test_unit.sh --python-only \
  tests/unit/workloads/test_workload_tool_bridge.py \
  tests/unit/workflows/temporal/test_activity_runtime.py::test_default_skill_registry_payload_uses_dood_tool_definitions
```

### Complete Curated Tool Scope

1. Complete User Story 2 tests and implementation for `unreal.run_tests`.
2. Validate both executable tool contracts with:

```bash
./tools/test_unit.sh --python-only tests/unit/workloads/test_workload_tool_bridge.py
```

### Complete Runtime Boundary

1. Complete User Story 3 routing and managed-session boundary tests.
2. Run focused quickstart verification.
3. Run full unit verification.
4. Run runtime scope validation for tasks and diff.
