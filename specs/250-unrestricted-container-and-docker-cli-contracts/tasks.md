# Tasks: Unrestricted Container and Docker CLI Contracts

**Input**: Design documents from `specs/250-unrestricted-container-and-docker-cli-contracts/`  
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/unrestricted-docker-workload-contract.md`, `quickstart.md`

**Tests**: Unit tests and hermetic integration verification are REQUIRED. For MM-501, the repository already contains most unrestricted-mode production behavior and broad test evidence, so the story work is verification-first: preserve the canonical artifacts, re-run the focused unrestricted verification suites, close the remaining `implemented_unverified` gaps with tests first, and implement code changes only if verification exposes drift.

**Organization**: Tasks are grouped around the single MM-501 story: verify `container.run_container` and `container.run_docker` as distinct unrestricted contracts, prove mode-aware denial and profile-backed-path preservation, compare the documented unrestricted example flows to runtime behavior, and preserve MM-501 traceability.

**Source Traceability**: MM-501; FR-001 through FR-007; acceptance scenarios 1-5; SC-001 through SC-006; DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-017, DESIGN-REQ-022, DESIGN-REQ-025.

**Requirement Status Summary**: verification-first. `FR-001`, `FR-003`, `FR-004`, `FR-005`, `FR-007`, `DESIGN-REQ-003`, `DESIGN-REQ-010`, `DESIGN-REQ-017`, and `DESIGN-REQ-025` are already implemented and verified by current code plus unit/integration evidence. `FR-002`, `FR-006`, and `DESIGN-REQ-022` are `implemented_unverified` and require verification tests plus conditional fallback implementation only if verification fails.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/config/test_settings.py`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

**Purpose**: Confirm the MM-501 source brief, planning artifacts, and unrestricted-mode repo surfaces before verification work.

- [ ] T001 Confirm `docs/tmp/jira-orchestration-inputs/MM-501-moonspec-orchestration-input.md`, `specs/250-unrestricted-container-and-docker-cli-contracts/spec.md`, `plan.md`, `research.md`, `contracts/unrestricted-docker-workload-contract.md`, and `quickstart.md` remain the canonical MM-501 artifact set
- [ ] T002 Confirm the MM-501 runtime touchpoints in `moonmind/config/settings.py`, `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, `moonmind/workflow_docker_mode.py`, `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/temporal/worker_runtime.py`, and the existing unrestricted-mode test suites

---

## Phase 2: Foundational

**Purpose**: Lock the feature-local validation shape before story verification.

- [ ] T003 Confirm `specs/250-unrestricted-container-and-docker-cli-contracts/research.md` remains correct that MM-501 needs no `data-model.md`, migration, or new persistent storage because the story is a runtime contract verification story
- [ ] T004 Confirm the focused unit suites in `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, `tests/unit/config/test_settings.py`, plus the hermetic integration boundary in `tests/integration/temporal/test_integration_ci_tool_contract.py`, are the correct validation paths for MM-501

**Checkpoint**: Foundation ready - story verification work can begin.

---

## Phase 3: Story - Run Unrestricted Containers And Docker CLI Workloads

**Summary**: As a trusted deployment operator, I want separate unrestricted container and Docker CLI workflow tools so MoonMind can support operator-approved unrestricted execution without weakening the normal profile-backed workload contract.

**Independent Test**: In each workflow Docker mode, invoke `container.run_container`, `container.run_docker`, and `container.run_workload` with allowed and disallowed inputs, then verify unrestricted mode permits only the defined unrestricted contracts, `container.run_docker` requires a Docker CLI invocation, structured validation rejects forbidden host-path, privilege, networking, or auth inheritance inputs, non-unrestricted modes return deterministic denial outcomes, and MM-501 traceability remains preserved.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-017, DESIGN-REQ-022, DESIGN-REQ-025

**Unit Test Plan**:

- Re-run and, only if needed, strengthen `tests/unit/workloads/test_workload_contract.py` and `tests/unit/workloads/test_docker_workload_launcher.py` for structured unrestricted-boundary rejection, Docker CLI prefix enforcement, and example-flow request-shape alignment.
- Re-run the unrestricted mode registration and denial coverage in `tests/unit/workloads/test_workload_tool_bridge.py`.
- Re-run the Temporal runtime and worker wiring coverage in `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, and `tests/unit/config/test_settings.py`.

**Integration Test Plan**:

- Re-run `tests/integration/temporal/test_integration_ci_tool_contract.py` through `./tools/test_integration.sh` to prove dispatcher/runtime omission outside `unrestricted` mode and execution of `container.run_container` in `unrestricted` mode.
- Add targeted integration assertions in `tests/integration/temporal/test_integration_ci_tool_contract.py` only if the current boundary does not fully prove FR-002 or FR-006 after verification.

### Verification Tests

- [ ] T005 [P] Re-run and review unrestricted request validation coverage for FR-002, FR-003, SC-001, SC-002, DESIGN-REQ-017, and DESIGN-REQ-022 in `tests/unit/workloads/test_workload_contract.py` and `tests/unit/workloads/test_docker_workload_launcher.py`
- [ ] T006 [P] Re-run and review unrestricted tool registration and mode-aware denial coverage for FR-001, FR-004, FR-005, SC-003, SC-004, DESIGN-REQ-003, DESIGN-REQ-010, and DESIGN-REQ-025 in `tests/unit/workloads/test_workload_tool_bridge.py`
- [ ] T007 [P] Re-run and review runtime wiring and mode propagation coverage for FR-004, SC-003, DESIGN-REQ-003, and DESIGN-REQ-010 in `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, and `tests/unit/config/test_settings.py`
- [ ] T008 Re-run and review the hermetic dispatcher boundary for FR-001, FR-004, FR-005, SC-003, SC-004, SC-005, DESIGN-REQ-010, and DESIGN-REQ-025 in `tests/integration/temporal/test_integration_ci_tool_contract.py`

### Red-First Confirmation

- [ ] T009 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/config/test_settings.py` and confirm whether the MM-501-focused unit verification already passes or exposes a gap
- [ ] T010 Run `./tools/test_integration.sh` and confirm whether the current hermetic integration boundary in `tests/integration/temporal/test_integration_ci_tool_contract.py` already passes for MM-501 or exposes a gap

### Conditional Fallback Implementation

- [ ] T011 If T005 or T009 exposes a structured unrestricted-boundary gap for FR-002, implement the minimum schema or launcher fix in `moonmind/schemas/workload_models.py` and `moonmind/workloads/docker_launcher.py`
- [ ] T012 If T008 or T010 exposes a dispatcher/runtime alignment gap for FR-006 or DESIGN-REQ-022, implement the minimum tool registration, runtime enforcement, or integration assertion fix in `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `tests/integration/temporal/test_integration_ci_tool_contract.py`

### Story Validation

- [ ] T013 Re-run the focused unit command from T009 after any fallback changes and confirm MM-501 unit evidence is green in `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, and `tests/unit/config/test_settings.py`
- [ ] T014 Re-run `./tools/test_integration.sh` after any fallback changes and confirm `tests/integration/temporal/test_integration_ci_tool_contract.py` proves unrestricted-mode dispatcher/runtime behavior end to end
- [ ] T015 Compare `docs/ManagedAgents/DockerOutOfDocker.md` sections 11.4, 11.5, and 18.2-18.4 against `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, and `contracts/unrestricted-docker-workload-contract.md` to confirm FR-006 and DESIGN-REQ-022 remain aligned
- [ ] T016 Review `docs/tmp/jira-orchestration-inputs/MM-501-moonspec-orchestration-input.md`, `spec.md`, `plan.md`, `research.md`, `contracts/unrestricted-docker-workload-contract.md`, and `quickstart.md` to confirm FR-007 and SC-006 preserve MM-501 and the original Jira preset brief across downstream artifacts

**Checkpoint**: MM-501 is complete when the unrestricted container and Docker CLI contracts are proven at unit and integration boundaries, any exposed verification gap is remediated conservatively, the source-design examples still match runtime behavior, and the canonical artifact set preserves the Jira brief.

---

## Phase 4: Polish And Verification

**Purpose**: Final traceability and read-only verification for the completed story.

- [ ] T017 [P] Update `specs/250-unrestricted-container-and-docker-cli-contracts/plan.md`, `research.md`, `quickstart.md`, and `contracts/unrestricted-docker-workload-contract.md` if the verification evidence changed any MM-501 implementation assumptions or commands
- [ ] T018 [P] Review the missing helper-script note in `specs/250-unrestricted-container-and-docker-cli-contracts/research.md` against the current repository state and keep it accurate without adding hidden scope
- [ ] T019 Run `/moonspec-verify` for `specs/250-unrestricted-container-and-docker-cli-contracts/` and produce final verification evidence covering MM-501, FR-001 through FR-007, SC-001 through SC-006, and DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-017, DESIGN-REQ-022, DESIGN-REQ-025

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies
- Foundational (Phase 2): depends on Setup completion
- Story (Phase 3): depends on Foundational completion
- Polish (Phase 4): depends on story validation completing

### Within The Story

- T005-T008 define the verification surface before any fallback implementation is considered.
- T009-T010 confirm whether MM-501 is already green or whether the conditional fallback path is needed.
- T011-T012 only execute if the preceding verification exposes a real gap.
- T013-T016 provide the required post-fix or post-verification story validation before final verification.

### Parallel Opportunities

- T005-T007 can run in parallel because they validate different unit-test files.
- T017 and T018 can run in parallel after story validation is complete.

## Implementation Strategy

1. Preserve MM-501 as the canonical MoonSpec source input and artifact set.
2. Re-run the focused unrestricted-mode unit and integration verification suites before touching production code.
3. Apply the smallest possible fallback fix only if verification exposes a real runtime or test gap.
4. Re-validate unit and integration evidence after any fallback change.
5. Confirm source-design example-flow alignment and MM-501 traceability.
6. Finish with `/moonspec-verify`.
