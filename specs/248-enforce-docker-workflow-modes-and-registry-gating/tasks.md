# Tasks: Enforce Docker Workflow Modes and Registry Gating

**Input**: Design documents from `specs/248-enforce-docker-workflow-modes-and-registry-gating/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/workflow-docker-mode-contract.md`, `quickstart.md`

**Tests**: Unit tests and hermetic integration verification are REQUIRED. For MM-499, write or update failing unit and integration coverage before production code changes, confirm the new coverage fails for the current boolean gate, then implement the canonical tri-mode workflow Docker contract.

**Organization**: Tasks are grouped around the single MM-499 story: replace the legacy boolean workflow Docker gate with the deployment-owned `disabled` / `profiles` / `unrestricted` mode contract, keep curated/profile-backed tools as the normal path, align registry exposure with runtime denial behavior, and preserve MM-499 traceability.

**Source Traceability**: MM-499; FR-001 through FR-008; acceptance scenarios 1-6; SC-001 through SC-007; DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011.

**Requirement Status Summary**: missing/partial only. Code-and-test work is required for FR-001 through FR-007 and DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011. Traceability-preservation work is required for FR-008 and SC-007. No rows are `implemented_verified` or `implemented_unverified`, so there are no verification-only or conditional-fallback-only tasks in this story.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/config/test_settings.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_workload_run_activity.py`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final full unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the single-story MM-499 artifact set and the exact repo surfaces that must move from a boolean Docker gate to a tri-mode policy contract.

- [ ] T001 Confirm `specs/248-enforce-docker-workflow-modes-and-registry-gating/` contains `spec.md`, `plan.md`, `research.md`, `contracts/workflow-docker-mode-contract.md`, and `quickstart.md` for MM-499
- [ ] T002 Confirm the current MM-499 touchpoints and traceability targets in `moonmind/config/settings.py`, `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/worker_runtime.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `docs/tmp/jira-orchestration-inputs/MM-499-moonspec-orchestration-input.md`

---

## Phase 2: Foundational

**Purpose**: Lock the shared prerequisites for the story before changing runtime behavior.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T003 Confirm no `data-model.md`, database migration, or new persistent storage is required for MM-499 because the story changes runtime configuration and tool policy only, using `specs/248-enforce-docker-workflow-modes-and-registry-gating/research.md` as the source of truth
- [ ] T004 Confirm the unit and hermetic integration harnesses named in `specs/248-enforce-docker-workflow-modes-and-registry-gating/quickstart.md` are the correct boundaries for FR-001 through FR-007 before adding new coverage

**Checkpoint**: Foundation ready - red-first story work can now begin.

---

## Phase 3: Story - Govern Workflow Docker Access

**Summary**: As a deployment operator, I want workflow Docker access to follow one explicit deployment mode so MoonMind exposes and enforces only the workload tools allowed for that environment.

**Independent Test**: Start the system in each supported workflow Docker mode plus one unsupported value, then verify the effective mode defaults correctly, invalid mode values fail deterministically at startup, registry discovery reflects the selected mode, runtime invocation denies mode-forbidden tools, and MM-499 traceability is preserved.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011

**Unit Test Plan**:

- Verify settings normalization and fail-fast invalid values in `tests/unit/config/test_settings.py`.
- Verify mode-aware tool definition exposure, handler denial, and unrestricted-tool gating in `tests/unit/workloads/test_workload_tool_bridge.py`.
- Verify Temporal activity/runtime denial and worker registration alignment in `tests/unit/workflows/temporal/test_activity_runtime.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, and `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`.

**Integration Test Plan**:

- Add or update one `integration_ci` dispatcher boundary in `tests/integration/temporal/test_integration_ci_tool_contract.py` so the selected workflow Docker mode governs tool exposure and execution together.
- Escalate through `./tools/test_integration.sh` after the new integration coverage is in place and again after implementation changes land.

### Unit Tests (write first)

- [ ] T005 [P] Add failing unit tests for FR-002, FR-003, SC-001, SC-002, DESIGN-REQ-003, DESIGN-REQ-007, and DESIGN-REQ-008 in `tests/unit/config/test_settings.py` covering default `profiles`, accepted `disabled` / `profiles` / `unrestricted` values, and invalid `MOONMIND_WORKFLOW_DOCKER_MODE` rejection
- [ ] T006 [P] Add failing unit tests for FR-001, FR-004, FR-005, FR-006, FR-007, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-011 in `tests/unit/workloads/test_workload_tool_bridge.py` covering mode-aware registration matrices, disabled-mode omission, profiles-mode curated exposure, unrestricted-mode unrestricted tool exposure, and deterministic denial of forbidden direct invocation
- [ ] T007 [P] Add failing unit tests for FR-004, FR-006, FR-007, SC-003, SC-005, and SC-006 in `tests/unit/workflows/temporal/test_activity_runtime.py` and `tests/unit/workflows/temporal/test_workload_run_activity.py` covering mode-aware activity denial and allowed execution paths for Docker-backed tools
- [ ] T008 [P] Add failing unit tests for FR-001, FR-005, FR-006, FR-007, SC-004, SC-005, and SC-006 in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` covering worker/runtime wiring of the normalized workflow Docker mode into registration and execution surfaces

### Integration Tests (write first)

- [ ] T009 [P] Add a failing `integration_ci` boundary test for acceptance scenarios 3-6, SC-003 through SC-006, and DESIGN-REQ-009 through DESIGN-REQ-011 in `tests/integration/temporal/test_integration_ci_tool_contract.py` proving registry exposure and dispatcher execution stay aligned for `disabled`, `profiles`, and `unrestricted` modes

### Red-First Confirmation

- [ ] T010 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/config/test_settings.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_workload_run_activity.py` and confirm the new MM-499-focused unit coverage fails against the current boolean workflow Docker gate
- [ ] T011 Run `./tools/test_integration.sh` and confirm the new MM-499 hermetic integration boundary fails until mode-aware registration and execution are aligned

### Implementation

- [ ] T012 Implement the canonical `MOONMIND_WORKFLOW_DOCKER_MODE` configuration surface and remove the superseded boolean workflow Docker setting in `moonmind/config/settings.py` for FR-001, FR-002, FR-003, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007, and DESIGN-REQ-008
- [ ] T013 Implement normalized workflow Docker mode policy helpers and mode-aware tool registration in `moonmind/workloads/tool_bridge.py` for FR-001, FR-004, FR-005, FR-006, FR-007, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-011
- [ ] T014 Implement unrestricted request and validation support for `container.run_container` and `container.run_docker` in `moonmind/schemas/workload_models.py`, `moonmind/workloads/registry.py`, and `moonmind/workloads/docker_launcher.py` for FR-006, SC-005, and DESIGN-REQ-011
- [ ] T015 Implement mode-aware worker/runtime wiring in `moonmind/workflows/temporal/worker_runtime.py` and `moonmind/workflows/temporal/activity_runtime.py` so discovery and execution share the same policy decision for FR-004, FR-005, FR-006, FR-007, SC-003, SC-004, SC-005, and SC-006

### Story Validation

- [ ] T016 Rerun the focused unit command from T010 until FR-001 through FR-007, SC-001 through SC-006, and DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011 pass in `tests/unit/config/test_settings.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_activity_runtime.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, and `tests/unit/workflows/temporal/test_workload_run_activity.py`
- [ ] T017 Rerun `./tools/test_integration.sh` until the updated `tests/integration/temporal/test_integration_ci_tool_contract.py` boundary confirms disabled/profiles/unrestricted registry and execution alignment for acceptance scenarios 3-6 and SC-003 through SC-006
- [ ] T018 Review `spec.md`, `plan.md`, `research.md`, `contracts/workflow-docker-mode-contract.md`, `quickstart.md`, changed code, and tests to confirm FR-008 and SC-007 preserve MM-499 and the original Jira preset brief across downstream artifacts and implementation evidence

**Checkpoint**: The MM-499 story is complete when the canonical mode setting replaces the boolean gate, discovery and execution align across all three modes, and the focused unit plus hermetic integration coverage passes.

---

## Phase 4: Polish And Verification

**Purpose**: Complete final validation and preserve story-level evidence without adding hidden scope.

- [ ] T019 [P] Update `specs/248-enforce-docker-workflow-modes-and-registry-gating/plan.md`, `research.md`, and `quickstart.md` if implementation details or test commands changed while preserving MM-499 traceability
- [ ] T020 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` to confirm the full required unit suite still passes after the MM-499 runtime and test changes
- [ ] T021 Run `/moonspec-verify` for `specs/248-enforce-docker-workflow-modes-and-registry-gating/` and produce `verification.md` covering MM-499, FR-001 through FR-008, SC-001 through SC-007, and DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on story validation completing.

### Within The Story

- T005-T009 must be written before any production implementation task.
- T010-T011 must confirm the red state before T012-T015 begin.
- T012 establishes the canonical configuration surface before mode-aware tool/runtime wiring is finalized.
- T013 and T014 define the mode-aware tool contract and unrestricted runtime surface before T015 integrates the policy into worker/activity execution.
- T016-T018 validate story completion before Polish begins.

### Parallel Opportunities

- T005-T008 can run in parallel because they modify different unit test files.
- T009 can run in parallel with T005-T008 because it modifies a different integration file.
- T019 can run in parallel with verification preparation once T018 confirms traceability.

## Implementation Strategy

1. Confirm the MM-499 planning artifacts and repo touchpoints.
2. Add failing unit and hermetic integration coverage for the tri-mode workflow Docker contract.
3. Replace the legacy boolean workflow Docker setting with the canonical mode surface.
4. Implement mode-aware tool registration, unrestricted tool support, and unified worker/runtime policy wiring.
5. Rerun focused unit and hermetic integration verification until the story passes.
6. Preserve MM-499 traceability in all downstream artifacts and finish with `/moonspec-verify`.
