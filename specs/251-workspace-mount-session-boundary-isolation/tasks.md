# Tasks: Workspace, Mount, and Session-Boundary Isolation

**Input**: Design documents from `specs/251-workspace-mount-session-boundary-isolation/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/workload-isolation-contract.md`, `quickstart.md`

**Tests**: Unit tests and hermetic integration verification are REQUIRED. For MM-502, the repository already contains most runtime behavior and unit coverage, so the story work is verification-first: preserve the canonical artifacts, add the missing dispatcher-boundary proof for session-assisted workload isolation, and only touch production code if verification reveals a real gap.

**Organization**: Tasks are grouped around the single MM-502 story: keep Docker-backed workloads inside MoonMind-owned task paths, preserve session/workload identity separation, require explicit credential-sharing policy for auth material, and prove policy alignment at the dispatcher/runtime boundary.

**Source Traceability**: MM-502; FR-001 through FR-007; acceptance scenarios 1-5; SC-001 through SC-006; DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022.

**Requirement Status Summary**: Verification-first with conditional fallback implementation. FR-001, FR-004, FR-005, FR-007 and DESIGN-REQ-005/014/016 are already implemented and verified by existing unit evidence plus artifact traceability. FR-002, FR-003, FR-006 and DESIGN-REQ-002/004/013/015/022 are implemented but still need explicit session-assisted boundary proof; add tests first, confirm the expected failure or missing proof, then execute the fallback implementation tasks only if those tests expose a real runtime gap.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workloads/test_docker_workload_launcher.py`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

**Purpose**: Confirm the MM-502 artifact set, target runtime surfaces, and focused validation files before verification work begins.

- [X] T001 Confirm `docs/tmp/jira-orchestration-inputs/MM-502-moonspec-orchestration-input.md`, `specs/251-workspace-mount-session-boundary-isolation/spec.md`, `specs/251-workspace-mount-session-boundary-isolation/plan.md`, `specs/251-workspace-mount-session-boundary-isolation/research.md`, `specs/251-workspace-mount-session-boundary-isolation/contracts/workload-isolation-contract.md`, and `specs/251-workspace-mount-session-boundary-isolation/quickstart.md` remain the canonical MM-502 source and planning artifacts for FR-007 and SC-006
- [X] T002 Confirm the MM-502 runtime touchpoints in `moonmind/schemas/workload_models.py`, `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, and `tests/integration/temporal/test_profile_backed_workload_contract.py`

---

## Phase 2: Foundational

**Purpose**: Lock the verification scope and prerequisites before story execution.

- [X] T003 Confirm `specs/251-workspace-mount-session-boundary-isolation/` needs no `data-model.md`, migration, or new persistent storage because MM-502 is a runtime boundary verification story
- [X] T004 Confirm `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, and `tests/integration/temporal/test_profile_backed_workload_contract.py` are the correct validation surfaces for FR-001 through FR-006 and DESIGN-REQ-002/004/005/013/014/015/016/022

**Checkpoint**: Foundation ready - story verification work can now begin.

---

## Phase 3: Story - Enforce Workload Isolation Boundaries

**Summary**: As a platform maintainer, I want MoonMind workload launches to stay inside MoonMind-owned task paths and remain isolated from managed-session identity and provider authentication state so Docker-backed workloads cannot silently widen session authority.

**Independent Test**: Submit Docker-backed workload requests from direct tool calls and session-assisted steps, then verify valid requests remain confined to MoonMind-owned task paths, invalid paths are rejected before launch, session-associated launches do not change workload identity or grant raw Docker authority to the session, and auth volumes are absent unless an explicit credential-sharing policy allows them.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022

**Unit Test Plan**:

- Reuse and extend the workload contract, tool-bridge, activity-runtime, and launcher unit suites to confirm workspace confinement, association-only metadata, and explicit credential-mount behavior remain intact.

**Integration Test Plan**:

- Extend `tests/integration/temporal/test_profile_backed_workload_contract.py` with MM-502-specific dispatcher/runtime coverage for session-associated workload isolation and policy alignment.
- Reuse `tests/integration/temporal/test_integration_ci_tool_contract.py` only as supporting evidence for the existing Docker workload dispatcher path.

### Unit Tests (write first) ⚠️

- [X] T005 [P] Confirm and preserve unit coverage for FR-002, FR-003, DESIGN-REQ-004, and DESIGN-REQ-015 in `tests/unit/workloads/test_workload_tool_bridge.py` covering session-associated workload metadata and the absence of session continuity artifact outputs
- [X] T006 [P] Add failing unit tests for FR-006, DESIGN-REQ-013, and DESIGN-REQ-022 in `tests/unit/workflows/temporal/test_workload_run_activity.py` covering deterministic denial and workload-policy alignment for session-assisted requests
- [X] T007 Run targeted unit validation for `tests/unit/workloads/test_workload_tool_bridge.py` and `tests/unit/workflows/temporal/test_workload_run_activity.py`, confirming the MM-502 red-first gap was isolated to the activity/runtime boundary before any production changes

### Integration Tests (write first) ⚠️

- [X] T008 Add a failing hermetic integration test for FR-002, FR-003, SC-002, SC-003, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-013, and DESIGN-REQ-015 in `tests/integration/temporal/test_profile_backed_workload_contract.py` covering a session-associated workload request that must remain a workload-plane execution with bounded `sessionContext`
- [X] T009 Add a failing hermetic integration test for FR-006, SC-004, and DESIGN-REQ-022 in `tests/integration/temporal/test_profile_backed_workload_contract.py` covering dispatcher/runtime policy alignment for session-assisted workload launches and mode denial
- [X] T010 Run `pytest tests/integration/temporal/test_profile_backed_workload_contract.py -q --tb=short -m 'integration_ci'` to confirm T008-T009 fail for the expected MM-502 boundary reason before any production changes

### Red-First Confirmation

- [X] T011 Review the failures from `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, and `tests/integration/temporal/test_profile_backed_workload_contract.py` to confirm the red-first evidence maps to FR-002, FR-003, FR-006, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-013, DESIGN-REQ-015, and DESIGN-REQ-022 rather than test-authoring mistakes

### Conditional Fallback Implementation (only if verification fails) ⚠️

- [X] T012 Conditionally update `moonmind/workloads/tool_bridge.py` and `moonmind/workflows/temporal/activity_runtime.py` for FR-002, FR-006, DESIGN-REQ-002, DESIGN-REQ-013, and DESIGN-REQ-022 only if T007-T011 show session-assisted requests can bypass workload-path isolation or mode enforcement; verification localized the gap to missing test-harness evidence, so no production change was required
- [X] T013 Conditionally update `moonmind/schemas/workload_models.py` and `moonmind/workloads/docker_launcher.py` for FR-003, DESIGN-REQ-004, and DESIGN-REQ-015 only if T007-T011 show session association metadata can alter workload identity or leak session continuity semantics; verification confirmed the existing runtime behavior, so no production change was required

### Story Validation

- [X] T014 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workloads/test_docker_workload_launcher.py` and confirm FR-001, FR-004, FR-005, FR-006, DESIGN-REQ-005, DESIGN-REQ-014, DESIGN-REQ-016, and any MM-502 fallback fixes pass together
- [X] T015 Attempt `./tools/test_integration.sh`, record any environment blocker precisely, then run `pytest tests/integration/temporal/test_profile_backed_workload_contract.py tests/integration/temporal/test_integration_ci_tool_contract.py -q --tb=short -m 'integration_ci'` as a focused fallback to confirm the MM-502 dispatcher/runtime isolation boundary and supporting Docker workload path evidence pass
- [X] T016 Review `specs/251-workspace-mount-session-boundary-isolation/spec.md`, `specs/251-workspace-mount-session-boundary-isolation/plan.md`, `specs/251-workspace-mount-session-boundary-isolation/research.md`, `specs/251-workspace-mount-session-boundary-isolation/contracts/workload-isolation-contract.md`, `specs/251-workspace-mount-session-boundary-isolation/quickstart.md`, and `docs/tmp/jira-orchestration-inputs/MM-502-moonspec-orchestration-input.md` to confirm FR-007 and SC-006 preserve MM-502 across downstream artifacts

**Checkpoint**: MM-502 is complete when the existing workload-isolation behavior is proven at unit and integration boundaries and the canonical artifact set preserves the Jira source brief.

---

## Phase 4: Polish And Verification

**Purpose**: Final traceability, quickstart validation, and read-only verification for the completed story.

- [X] T017 [P] Align the feature-local artifacts in `specs/251-workspace-mount-session-boundary-isolation/` after MM-502 verification work so terminology, traceability, and commands stay coherent
- [X] T018 Run the quickstart validation from `specs/251-workspace-mount-session-boundary-isolation/quickstart.md` and record any environment blockers or deviations needed for MM-502
- [X] T019 Run `/moonspec-verify` for `specs/251-workspace-mount-session-boundary-isolation/` and produce a final evidence-backed verification report covering MM-502, FR-001 through FR-007, SC-001 through SC-006, and DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies
- Foundational (Phase 2): depends on Setup completion
- Story (Phase 3): depends on Foundational completion
- Polish (Phase 4): depends on story validation completing

### Within The Story

- T005-T006 create the unit-level MM-502 verification boundary before red-first confirmation.
- T008-T009 create the integration-level MM-502 verification boundary before red-first confirmation.
- T007 and T010 must complete before T011.
- T012-T013 are conditional and only run if T011 confirms a real product gap.
- T014-T016 run after verification tests and any conditional fallback implementation complete.

### Parallel Opportunities

- T005 and T006 can run in parallel because they touch different unit test files.
- T008 and T009 must stay sequential because they modify the same integration test file.
- T017 can run in parallel with verification prep after T016.

## Implementation Strategy

1. Preserve MM-502 as the canonical MoonSpec source input and keep the feature-local artifact set intact.
2. Add the missing unit and hermetic integration proof for session-assisted workload isolation.
3. Confirm the new tests fail for the expected MM-502 reasons before touching production code.
4. Execute fallback implementation tasks only if the new verification exposes a real boundary gap.
5. Rerun the focused unit and integration validations.
6. Finish with quickstart validation and `/moonspec-verify` against the original MM-502 brief.
