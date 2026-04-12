# Tasks: DooD Unreal Pilot

**Input**: Design documents from `/specs/159-dood-unreal-pilot/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. The feature request explicitly calls for test-driven development and validation tests.

**Organization**: Tasks are grouped by user story so each increment is independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on another incomplete task.
- **[Story]**: Which user story the task supports.
- Every task includes an exact file path or validation command.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the current workload runtime and planning artifacts are ready for Phase 6 work.

- [ ] T001 Verify existing DooD workload runtime surfaces in `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, and `moonmind/workflows/temporal/worker_runtime.py`
- [ ] T002 [P] Verify Phase 6 planning artifacts in `specs/159-dood-unreal-pilot/spec.md`, `specs/159-dood-unreal-pilot/plan.md`, `specs/159-dood-unreal-pilot/research.md`, `specs/159-dood-unreal-pilot/data-model.md`, `specs/159-dood-unreal-pilot/contracts/unreal-run-tests-contract.schema.json`, and `specs/159-dood-unreal-pilot/quickstart.md`
- [ ] T003 [P] Verify existing workload tests in `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, and `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the shared Unreal pilot contract before story-specific implementation.

**CRITICAL**: No user story implementation can begin until this phase is complete.

- [ ] T004 [P] Add or verify the `unreal.run_tests` JSON input contract in `specs/159-dood-unreal-pilot/contracts/unreal-run-tests-contract.schema.json`
- [ ] T005 [P] Add or verify Unreal runner profile data-model coverage in `specs/159-dood-unreal-pilot/data-model.md`
- [ ] T006 [P] Add or verify Unreal pilot research decisions and alternatives in `specs/159-dood-unreal-pilot/research.md`

**Checkpoint**: Foundation ready. User story implementation can proceed.

---

## Phase 3: User Story 1 - Enable the Curated Unreal Runner (Priority: P1) MVP

**Goal**: Provide a deployment-owned `unreal-5_3-linux` runner profile that can be loaded by the Docker-capable worker without arbitrary Docker inputs.

**Independent Test**: Load the default workload profile registry and verify the Unreal profile includes the pinned image, approved workspace/cache mounts, safe network/device posture, resource bounds, timeout bounds, concurrency bound, and env allowlist.

### Tests for User Story 1

> Write these tests first and confirm they fail before implementation when behavior is missing.

- [ ] T007 [P] [US1] Add default Unreal profile registry validation in `tests/unit/workloads/test_workload_contract.py`
- [ ] T008 [P] [US1] Add agent-runtime default registry bootstrap validation in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [ ] T009 [P] [US1] Add Unreal profile safe Docker launch posture validation in `tests/unit/workloads/test_docker_workload_launcher.py`

### Implementation for User Story 1

- [ ] T010 [US1] Add the built-in `unreal-5_3-linux` runner profile in `config/workloads/default-runner-profiles.yaml`
- [ ] T011 [US1] Load `config/workloads/default-runner-profiles.yaml` from `moonmind/workflows/temporal/worker_runtime.py` when `MOONMIND_WORKLOAD_PROFILE_REGISTRY` is unset
- [ ] T012 [US1] Verify the profile remains compatible with existing registry policy in `moonmind/workloads/registry.py` without adding arbitrary image or mount bypasses

**Checkpoint**: User Story 1 exposes the curated Unreal profile safely and can be validated independently.

---

## Phase 4: User Story 2 - Run Unreal Tests Through a Stable Domain Contract (Priority: P1)

**Goal**: Let plans invoke Unreal tests through `unreal.run_tests` with project path, optional selectors, relative report outputs, and allowlisted environment values.

**Independent Test**: Invoke the tool handler with Unreal inputs and verify it builds a validated workload request using `unreal-5_3-linux`, curated command arguments, declared report outputs, and allowlisted env overrides while rejecting invalid report paths.

### Tests for User Story 2

> Write these tests first and confirm they fail before implementation when behavior is missing.

- [ ] T013 [P] [US2] Add `reportPaths` ToolDefinition schema validation in `tests/unit/workloads/test_workload_tool_bridge.py`
- [ ] T014 [P] [US2] Add curated Unreal command, env override, and declared output mapping validation in `tests/unit/workloads/test_workload_tool_bridge.py`
- [ ] T015 [P] [US2] Add invalid Unreal report path rejection validation in `tests/unit/workloads/test_workload_tool_bridge.py`

### Implementation for User Story 2

- [ ] T016 [US2] Extend `unreal.run_tests` ToolDefinition inputs with `reportPaths` in `moonmind/workloads/tool_bridge.py`
- [ ] T017 [US2] Map Unreal report paths to curated command flags and declared workload outputs in `moonmind/workloads/tool_bridge.py`
- [ ] T018 [US2] Inject only allowlisted Unreal environment keys from `unreal.run_tests` in `moonmind/workloads/tool_bridge.py`

**Checkpoint**: User Story 2 can execute through the existing workload tool path without exposing raw Docker controls.

---

## Phase 5: User Story 3 - Preserve Artifact and Cache Semantics (Priority: P2)

**Goal**: Reuse approved Unreal cache volumes while keeping runtime logs, diagnostics, and reports as durable artifacts.

**Independent Test**: Build launch args for the Unreal profile and run mocked workload publication to prove cache mounts are present, report outputs stay under `artifactsDir`, runtime artifacts publish on success/failure, and cancellation/timeout cleanup continues to use the hardened launcher path.

### Tests for User Story 3

> Write these tests first and confirm they fail before implementation when behavior is missing.

- [ ] T019 [P] [US3] Add Unreal cache mount and no-auth-volume launch validation in `tests/unit/workloads/test_docker_workload_launcher.py`
- [ ] T020 [P] [US3] Add declared Unreal report output publication validation in `tests/unit/workloads/test_docker_workload_launcher.py`
- [ ] T021 [P] [US3] Add or verify timeout/cancel cleanup coverage remains valid for Unreal-profile workloads in `tests/unit/workloads/test_docker_workload_launcher.py`

### Implementation for User Story 3

- [ ] T022 [US3] Ensure `config/workloads/default-runner-profiles.yaml` defines `unreal_ccache_volume` and `unreal_ubt_volume` as approved cache mounts only
- [ ] T023 [US3] Ensure `moonmind/workloads/docker_launcher.py` treats Unreal report files as declared outputs under `artifactsDir` and never publishes cache contents as durable outputs
- [ ] T024 [US3] Update operator rollout notes for Unreal cache and artifact expectations in `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`

**Checkpoint**: User Story 3 preserves cache reuse and durable artifact truth independently of real Unreal Engine availability.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, tracking, and scope checks.

- [ ] T025 [P] Validate the Unreal tool contract JSON with `python -m json.tool specs/159-dood-unreal-pilot/contracts/unreal-run-tests-contract.schema.json`
- [ ] T026 [P] Run focused workload validation with `pytest tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_build_agent_runtime_deps_uses_artifacts_env_without_double_nesting -q`
- [ ] T027 Run full unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T028 Run runtime task scope validation with `SPECIFY_FEATURE=159-dood-unreal-pilot .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [ ] T029 Review `git diff --check` and confirm no unrelated changes or secret-like values were introduced in `config/`, `moonmind/`, `tests/`, `docs/tmp/`, or `specs/159-dood-unreal-pilot/`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion; blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational and is the MVP.
- **User Story 2 (Phase 4)**: Depends on Foundational and can proceed after or alongside US1 once file ownership is coordinated.
- **User Story 3 (Phase 5)**: Depends on US1 profile shape and existing launcher artifact behavior.
- **Polish (Phase 6)**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1**: Required first for a loadable curated profile.
- **US2**: Requires the `unreal.run_tests` tool path and can use test profiles while US1 is in progress, but final validation depends on the curated profile ID.
- **US3**: Requires the profile cache mount contract from US1 and report output contract from US2.

### Within Each User Story

- Tests must be written first and fail before implementation when behavior is missing.
- Config/profile changes before worker bootstrap validation.
- ToolDefinition schema before request conversion.
- Request conversion before launcher/artifact validation.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T004, T005, and T006 can run in parallel.
- T007, T008, and T009 can run in parallel.
- T013, T014, and T015 can run in parallel.
- T019, T020, and T021 can run in parallel.
- T025 and T026 can run in parallel after implementation.

---

## Parallel Example: User Story 1

```bash
Task: "T007 [US1] Add default Unreal profile registry validation in tests/unit/workloads/test_workload_contract.py"
Task: "T008 [US1] Add agent-runtime default registry bootstrap validation in tests/unit/workflows/temporal/test_temporal_worker_runtime.py"
Task: "T009 [US1] Add Unreal profile safe Docker launch posture validation in tests/unit/workloads/test_docker_workload_launcher.py"
```

## Parallel Example: User Story 2

```bash
Task: "T013 [US2] Add reportPaths ToolDefinition schema validation in tests/unit/workloads/test_workload_tool_bridge.py"
Task: "T014 [US2] Add curated Unreal command, env override, and declared output mapping validation in tests/unit/workloads/test_workload_tool_bridge.py"
Task: "T015 [US2] Add invalid Unreal report path rejection validation in tests/unit/workloads/test_workload_tool_bridge.py"
```

## Parallel Example: User Story 3

```bash
Task: "T019 [US3] Add Unreal cache mount and no-auth-volume launch validation in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T020 [US3] Add declared Unreal report output publication validation in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T021 [US3] Add or verify timeout/cancel cleanup coverage remains valid for Unreal-profile workloads in tests/unit/workloads/test_docker_workload_launcher.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup and Phase 2 foundational checks.
2. Complete US1 tests and implementation for the curated Unreal profile.
3. Validate US1 independently with profile loading, worker bootstrap, and safe launch argument tests.

### Incremental Delivery

1. Deliver US1 so the platform has a safe default Unreal profile.
2. Deliver US2 so plans can invoke `unreal.run_tests` with reports and allowlisted env.
3. Deliver US3 so cache reuse and artifact semantics are verified.
4. Run polish validation and full unit verification before handoff.
