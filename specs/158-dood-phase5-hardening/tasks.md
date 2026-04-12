# Tasks: DooD Phase 5 Hardening

**Input**: Design documents from `/specs/158-dood-phase5-hardening/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. The feature request explicitly requires test-driven development and validation tests for runtime behavior.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing workload runtime surfaces and feature artifacts are ready for TDD implementation.

- [ ] T001 Verify current workload runtime surfaces in `moonmind/schemas/workload_models.py`, `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, and `moonmind/workflows/temporal/worker_runtime.py`
- [ ] T002 [P] Verify existing workload validation tests and fixtures in `tests/unit/workloads/test_workload_contract.py` and `tests/unit/workloads/test_docker_workload_launcher.py`
- [ ] T003 [P] Verify existing tool and Temporal routing tests in `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/unit/workflows/temporal/test_activity_catalog.py`, `tests/unit/workflows/temporal/test_temporal_workers.py`, and `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared hardening primitives that every story depends on.

**CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T004 [P] Implement or verify with failing-first tests stable workload policy denial categories and non-secret details in `moonmind/workloads/registry.py`
- [ ] T005 [P] Implement or verify with failing-first tests workload profile security fields and validation hooks in `moonmind/schemas/workload_models.py`
- [ ] T006 [P] Implement or verify with failing-first tests Docker launcher operational label helpers and cleanup helper boundaries in `moonmind/workloads/docker_launcher.py`
- [ ] T007 Implement or verify with failing-first tests tool failure propagation preserves policy reason/details in `moonmind/workloads/tool_bridge.py`
- [ ] T008 Implement or verify with failing-first tests agent-runtime worker bootstrap accepts operator-owned workload policy/capacity settings in `moonmind/workflows/temporal/worker_runtime.py`

**Checkpoint**: Foundation ready. User story implementation can begin in priority order or in parallel where marked.

---

## Phase 3: User Story 1 - Deny Unsafe Workload Launches (Priority: P1) MVP

**Goal**: Reject unsafe or unauthorized Docker-backed workload requests before any workload container starts.

**Independent Test**: Submit workload requests that violate profile, image, mount, environment, network, resource, device, or secret-injection policy and verify each request is denied with no workload container launched.

### Tests for User Story 1

> Write these tests first and confirm they fail before implementation when behavior is missing.

- [ ] T009 [P] [US1] Add image provenance and registry allowlist denial tests in `tests/unit/workloads/test_workload_contract.py`
- [ ] T010 [P] [US1] Add auth/credential/secret volume rejection tests in `tests/unit/workloads/test_workload_contract.py`
- [ ] T011 [P] [US1] Add stable denial metadata tests for unknown profile, disallowed env key, disallowed mount, and resource request too large in `tests/unit/workloads/test_workload_contract.py`
- [ ] T012 [P] [US1] Add explicit no-privileged, no host-network, and no implicit device launch argument tests in `tests/unit/workloads/test_docker_workload_launcher.py`
- [ ] T013 [P] [US1] Add tool failure reason/detail propagation tests in `tests/unit/workloads/test_workload_tool_bridge.py`

### Implementation for User Story 1

- [ ] T014 [US1] Implement image registry/provenance allowlist enforcement in `moonmind/workloads/registry.py`
- [ ] T015 [US1] Implement auth/credential/secret volume rejection and profile security validation in `moonmind/schemas/workload_models.py`
- [ ] T016 [US1] Implement structured policy denial reason/details for registry validation failures in `moonmind/workloads/registry.py`
- [ ] T017 [US1] Implement explicit safe Docker launch posture in `moonmind/workloads/docker_launcher.py`
- [ ] T018 [US1] Implement policy denial propagation into executable-tool failures in `moonmind/workloads/tool_bridge.py`
- [ ] T019 [US1] Run `pytest tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py -q`

**Checkpoint**: User Story 1 rejects unsafe launches before Docker execution and exposes stable non-secret denial diagnostics.

---

## Phase 4: User Story 2 - Bound Heavy Workload Capacity (Priority: P2)

**Goal**: Enforce per-profile and per-fleet workload capacity so heavy Docker-backed jobs cannot starve normal managed-runtime work.

**Independent Test**: Run workload requests until configured profile or fleet limits are reached and verify additional work is denied or held according to policy while unrelated managed-runtime capacity remains protected.

### Tests for User Story 2

> Write these tests first and confirm they fail before implementation when behavior is missing.

- [ ] T020 [P] [US2] Add runner profile `maxConcurrency` schema validation tests in `tests/unit/workloads/test_workload_contract.py`
- [ ] T021 [P] [US2] Add profile concurrency limit denial tests in `tests/unit/workloads/test_docker_workload_launcher.py`
- [ ] T022 [P] [US2] Add fleet concurrency limit bootstrap tests in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
- [ ] T023 [P] [US2] Add capacity denial reason/detail propagation tests in `tests/unit/workloads/test_workload_tool_bridge.py`

### Implementation for User Story 2

- [ ] T024 [US2] Add per-profile workload concurrency field and validation to `moonmind/schemas/workload_models.py`
- [ ] T025 [US2] Implement Docker workload concurrency limiter and lease release behavior in `moonmind/workloads/docker_launcher.py`
- [ ] T026 [US2] Wire fleet-level workload concurrency configuration into agent-runtime bootstrap in `moonmind/workflows/temporal/worker_runtime.py`
- [ ] T027 [US2] Export concurrency limiter from `moonmind/workloads/__init__.py`
- [ ] T028 [US2] Run `pytest tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_build_agent_runtime_deps_uses_artifacts_env_without_double_nesting -q`

**Checkpoint**: User Story 2 prevents additional workload launches when profile or fleet pressure reaches configured limits.

---

## Phase 5: User Story 3 - Clean Up Orphaned Workloads (Priority: P3)

**Goal**: Remove expired MoonMind-owned workload containers by ownership labels and TTL without touching unrelated or non-expired containers.

**Independent Test**: Create workload containers with ownership metadata and expired TTL values, run cleanup, and verify expired workload containers are removed while non-expired or unrelated containers remain untouched.

### Tests for User Story 3

> Write these tests first and confirm they fail before implementation when behavior is missing.

- [ ] T029 [P] [US3] Add workload TTL label construction tests in `tests/unit/workloads/test_docker_workload_launcher.py`
- [ ] T030 [P] [US3] Add expired orphan sweep tests for workload-labeled containers in `tests/unit/workloads/test_docker_workload_launcher.py`
- [ ] T031 [P] [US3] Add skip behavior tests for non-expired, malformed, and unrelated containers in `tests/unit/workloads/test_docker_workload_launcher.py`

### Implementation for User Story 3

- [ ] T032 [US3] Implement workload expiration label generation in `moonmind/workloads/docker_launcher.py`
- [ ] T033 [US3] Implement expired workload orphan sweep behavior in `moonmind/workloads/docker_launcher.py`
- [ ] T034 [US3] Ensure cleanup uses MoonMind workload ownership labels and never broad container selection in `moonmind/workloads/docker_launcher.py`
- [ ] T035 [US3] Run `pytest tests/unit/workloads/test_docker_workload_launcher.py -q`

**Checkpoint**: User Story 3 reliably removes expired MoonMind workload orphans and leaves unrelated containers alone.

---

## Phase 6: User Story 4 - Audit Launch Decisions (Priority: P4)

**Goal**: Make approvals, denials, cleanup actions, and pressure signals diagnosable from bounded metadata and artifacts without leaking secrets.

**Independent Test**: Execute successful, denied, timed-out, and cleaned-up workload scenarios and verify each outcome includes bounded, operator-consumable decision metadata without leaking secrets.

### Tests for User Story 4

> Write these tests first and confirm they fail before implementation when behavior is missing.

- [ ] T036 [P] [US4] Add workload result metadata tests for selected profile, image, labels, timing, and artifact publication in `tests/unit/workloads/test_docker_workload_launcher.py`
- [ ] T037 [P] [US4] Add workflow activity boundary tests for hardened workload results in `tests/unit/workflows/temporal/test_workload_run_activity.py`
- [ ] T038 [P] [US4] Add activity catalog and worker topology tests for missing `docker_workload` capability diagnostics in `tests/unit/workflows/temporal/test_activity_catalog.py` and `tests/unit/workflows/temporal/test_temporal_workers.py`
- [ ] T039 [P] [US4] Add negative diagnostics tests proving workload metadata and tool failure details omit or redact secret-like env values, auth volume paths, and raw environment dumps in `tests/unit/workloads/test_docker_workload_launcher.py` and `tests/unit/workloads/test_workload_tool_bridge.py`

### Implementation for User Story 4

- [ ] T040 [US4] Add bounded launch approval and denial metadata to `moonmind/workloads/docker_launcher.py` and `moonmind/workloads/registry.py`
- [ ] T041 [US4] Ensure `workload.run` returns hardened result metadata at the Temporal activity boundary in `moonmind/workflows/temporal/activity_runtime.py`
- [ ] T042 [US4] Ensure Docker workload capability routing failures remain operator-diagnosable in `moonmind/workflows/temporal/activity_catalog.py` and `moonmind/workflows/temporal/workers.py`
- [ ] T043 [US4] Run `pytest tests/unit/workloads tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workflows/temporal/test_activity_catalog.py tests/unit/workflows/temporal/test_temporal_workers.py -q`

**Checkpoint**: User Story 4 produces bounded, non-secret diagnostics for launch decisions, denials, cleanup, and capacity pressure.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final integration checks, tracker consistency, and canonical verification.

- [ ] T044 [P] Update Phase 5 completion notes in `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md` if runtime work changes tracker state
- [ ] T045 [P] Validate the planning contract schema with `python -m json.tool specs/158-dood-phase5-hardening/contracts/workload-hardening-contract.schema.json`
- [ ] T046 Run focused workload and Temporal verification with `pytest tests/unit/workloads tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_build_agent_runtime_deps_uses_artifacts_env_without_double_nesting -q`
- [ ] T047 Run canonical final verification with `./tools/test_unit.sh`
- [ ] T048 Review `git diff` for unrelated changes and ensure no secrets or raw environment dumps appear in `moonmind/`, `tests/`, `docs/tmp/`, or `specs/158-dood-phase5-hardening/`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion; blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational; MVP and security gate.
- **User Story 2 (Phase 4)**: Depends on Foundational; can run after or alongside US1 if shared schema changes are coordinated.
- **User Story 3 (Phase 5)**: Depends on Foundational; can run alongside US1/US2 after launcher helper boundaries are stable.
- **User Story 4 (Phase 6)**: Depends on US1, US2, and US3 metadata paths.
- **Polish (Phase 7)**: Depends on selected user stories being complete.

### User Story Dependencies

- **User Story 1 (P1)**: MVP; no dependency on other stories after Foundation.
- **User Story 2 (P2)**: Requires shared profile schema from Foundation; otherwise independent.
- **User Story 3 (P3)**: Requires launcher ownership/label helpers from Foundation; otherwise independent.
- **User Story 4 (P4)**: Integrates diagnostics from US1, US2, and US3.

### Within Each User Story

- Tests must be written first and fail before implementation when behavior is missing.
- Schema/model updates before registry/launcher service behavior.
- Registry validation before tool bridge failure mapping.
- Launcher helpers before cleanup/concurrency behavior.
- Activity/topology tests after lower-level workload unit tests pass.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T004, T005, and T006 can run in parallel.
- US1 tests T009-T013 can run in parallel.
- US2 tests T020-T023 can run in parallel.
- US3 tests T029-T031 can run in parallel.
- US4 tests T036-T039 can run in parallel.
- Different user stories can be implemented in parallel after Phase 2 if files are coordinated to avoid conflicts.

---

## Parallel Example: User Story 1

```bash
Task: "T009 [US1] Add image provenance and registry allowlist denial tests in tests/unit/workloads/test_workload_contract.py"
Task: "T010 [US1] Add auth/credential/secret volume rejection tests in tests/unit/workloads/test_workload_contract.py"
Task: "T012 [US1] Add explicit no-privileged, no host-network, and no implicit device launch argument tests in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T013 [US1] Add tool failure reason/detail propagation tests in tests/unit/workloads/test_workload_tool_bridge.py"
```

## Parallel Example: User Story 2

```bash
Task: "T021 [US2] Add profile concurrency limit denial tests in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T022 [US2] Add fleet concurrency limit bootstrap tests in tests/unit/workflows/temporal/test_temporal_worker_runtime.py"
Task: "T023 [US2] Add capacity denial reason/detail propagation tests in tests/unit/workloads/test_workload_tool_bridge.py"
```

## Parallel Example: User Story 3

```bash
Task: "T029 [US3] Add workload TTL label construction tests in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T030 [US3] Add expired orphan sweep tests for workload-labeled containers in tests/unit/workloads/test_docker_workload_launcher.py"
Task: "T031 [US3] Add skip behavior tests for non-expired, malformed, and unrelated containers in tests/unit/workloads/test_docker_workload_launcher.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup.
2. Complete Phase 2 foundational hardening primitives.
3. Complete Phase 3 User Story 1 tests and implementation.
4. Stop and validate with `pytest tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py -q`.
5. Continue with capacity controls, cleanup, and diagnostics only after unsafe launch denial is reliable.

### Incremental Delivery

1. Deliver US1 as the security MVP.
2. Deliver US2 to bound heavy workload pressure.
3. Deliver US3 to make orphan cleanup reliable.
4. Deliver US4 to unify operator-facing diagnostics across approval, denial, capacity, and cleanup paths.
5. Run final focused and canonical verification before marking the feature complete.
