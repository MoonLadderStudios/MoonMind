# Tasks: Shared Docker Workload Execution Plane

**Input**: Design documents from `specs/252-route-docker-workload-plane/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/shared-docker-workload-plane-contract.md`, `quickstart.md`

**Tests**: Unit tests and hermetic integration verification are REQUIRED. For MM-503, the repository already contains most routing and lifecycle behavior, so the story work is verification-first: preserve the canonical artifacts, add the missing cross-class proof for the shared execution plane, and only touch production code if verification reveals a real metadata, routing, timeout, or cleanup gap.

**Organization**: Tasks are grouped around the single MM-503 story: keep all Docker-backed MoonMind tools on one trusted workload plane, verify deterministic metadata and bounded lifecycle behavior across curated and unrestricted tool classes, and preserve cleanup ownership boundaries without widening scope.

**Source Traceability**: MM-503; FR-001 through FR-007; acceptance scenarios 1-6; SC-001 through SC-006; DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, DESIGN-REQ-024.

**Requirement Status Summary**: Verification-first with conditional fallback implementation. FR-007 is already implemented and verified by the feature-local artifact set. FR-001, FR-003, FR-004, FR-005, FR-006 and DESIGN-REQ-006/020/023/024 are implemented but still need explicit cross-class proof. FR-002 and DESIGN-REQ-019 remain partial because the current metadata evidence may not fully cover runtime mode or equivalent cross-class metadata. Add tests first, confirm the expected failure or missing proof, then execute fallback implementation tasks only if those tests expose a real runtime gap.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_activity_runtime.py`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

**Purpose**: Confirm the MM-503 artifact set, target runtime surfaces, and focused validation files before verification work begins.

- [ ] T001 Confirm `docs/tmp/jira-orchestration-inputs/MM-503-moonspec-orchestration-input.md`, `specs/252-route-docker-workload-plane/spec.md`, `specs/252-route-docker-workload-plane/plan.md`, `specs/252-route-docker-workload-plane/research.md`, `specs/252-route-docker-workload-plane/contracts/shared-docker-workload-plane-contract.md`, and `specs/252-route-docker-workload-plane/quickstart.md` remain the canonical MM-503 source and planning artifacts for FR-007 and SC-006
- [ ] T002 Confirm the MM-503 runtime touchpoints in `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workflows/temporal/test_activity_runtime.py`, and `tests/integration/temporal/test_profile_backed_workload_contract.py`

---

## Phase 2: Foundational

**Purpose**: Lock the verification scope and prerequisites before story execution.

- [ ] T003 Confirm `specs/252-route-docker-workload-plane/` needs no `data-model.md`, migration, or new persistent storage because MM-503 is a runtime boundary verification story
- [ ] T004 Confirm `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workflows/temporal/test_activity_runtime.py`, and `tests/integration/temporal/test_profile_backed_workload_contract.py` are the correct validation surfaces for FR-001 through FR-006 and DESIGN-REQ-006/019/020/023/024

**Checkpoint**: Foundation ready - story verification work can now begin.

---

## Phase 3: Story - Route DooD Workloads Through One Execution Plane

**Summary**: As a workflow runtime owner, I want all Docker-backed MoonMind tools to execute through one trusted workload execution plane so workload routing, labels, timeout handling, cancellation, and cleanup stay consistent across tool types.

**Independent Test**: Trigger representative Docker-backed workload executions across the supported workload classes, then verify they all route through the same trusted execution capability, emit the same required ownership labels and bounded terminal metadata, and follow the same timeout, cancellation, and cleanup rules regardless of current runtime placement.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, DESIGN-REQ-024

**Unit Test Plan**:

- Extend the workload contract, launcher, tool-bridge, and activity-runtime unit suites to confirm shared `docker_workload` capability routing, deterministic metadata, timeout/cancellation semantics, and cleanup boundaries for curated and unrestricted tool classes.

**Integration Test Plan**:

- Extend `tests/integration/temporal/test_profile_backed_workload_contract.py` with MM-503-specific dispatcher/runtime coverage for unrestricted container and Docker CLI result shaping, plus any missing metadata and cleanup-boundary assertions discovered during unit analysis.
- Reuse existing profile-backed and helper integration scenarios as supporting evidence for the shared workload plane.

### Unit Tests (write first) ⚠️

- [ ] T005 [P] Add failing unit tests for FR-001, FR-006, DESIGN-REQ-006, and DESIGN-REQ-020 in `tests/unit/workflows/temporal/test_activity_runtime.py` covering capability-based routing and shared `mm.tool.execute` binding for curated and unrestricted Docker-backed tools
- [ ] T006 [P] Add failing unit tests for FR-002, DESIGN-REQ-019, and SC-002 in `tests/unit/workloads/test_workload_contract.py` covering deterministic metadata and labels for unrestricted container and Docker CLI requests, including workload access class and any required runtime-mode metadata
- [ ] T007 [P] Add failing unit tests for FR-003, FR-004, FR-005, DESIGN-REQ-023, and DESIGN-REQ-024 in `tests/unit/workloads/test_docker_workload_launcher.py` covering bounded timeout/cancellation metadata and conservative cleanup ownership for unrestricted launch classes
- [ ] T008 Run targeted unit validation for `tests/unit/workflows/temporal/test_activity_runtime.py`, `tests/unit/workloads/test_workload_contract.py`, and `tests/unit/workloads/test_docker_workload_launcher.py`, confirming the MM-503 red-first gaps are isolated before any production changes

### Integration Tests (write first) ⚠️

- [ ] T009 Add a failing hermetic integration test for FR-001, FR-003, SC-001, SC-003, and DESIGN-REQ-006 in `tests/integration/temporal/test_profile_backed_workload_contract.py` covering the shared workload result contract across profile-backed and unrestricted execution paths
- [ ] T010 Add a failing hermetic integration test for FR-002, FR-004, FR-005, SC-002, SC-004, SC-005, DESIGN-REQ-019, DESIGN-REQ-023, and DESIGN-REQ-024 in `tests/integration/temporal/test_profile_backed_workload_contract.py` covering metadata completeness, bounded timeout/cancellation behavior, and cleanup ownership boundaries for unrestricted execution
- [ ] T011 Run `pytest tests/integration/temporal/test_profile_backed_workload_contract.py -q --tb=short -m 'integration_ci'` to confirm T009-T010 fail for the expected MM-503 shared-plane reason before any production changes

### Red-First Confirmation

- [ ] T012 Review the failures from `tests/unit/workflows/temporal/test_activity_runtime.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, and `tests/integration/temporal/test_profile_backed_workload_contract.py` to confirm the red-first evidence maps to FR-001 through FR-006 and DESIGN-REQ-006/019/020/023/024 rather than test-authoring mistakes

### Conditional Fallback Implementation (only if verification fails) ⚠️

- [ ] T013 Conditionally update `moonmind/workloads/tool_bridge.py` and `moonmind/workflows/temporal/activity_runtime.py` for FR-001, FR-006, DESIGN-REQ-006, and DESIGN-REQ-020 only if T008-T012 show any Docker-backed tool class bypasses the shared `docker_workload`/`mm.tool.execute` path or exposes fleet-coupled behavior
- [ ] T014 Conditionally update `moonmind/workloads/registry.py` and `moonmind/workloads/docker_launcher.py` for FR-002, FR-003, DESIGN-REQ-019, and SC-002 only if T008-T012 show missing or inconsistent workload metadata across curated and unrestricted launch classes
- [ ] T015 Conditionally update `moonmind/workloads/docker_launcher.py` for FR-004, FR-005, DESIGN-REQ-023, and DESIGN-REQ-024 only if T008-T012 show timeout/cancellation metadata or cleanup ownership boundaries are inconsistent or overly permissive

### Story Validation

- [ ] T016 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_activity_runtime.py` and confirm MM-503 unit evidence passes together
- [ ] T017 Attempt `./tools/test_integration.sh`, record any environment blocker precisely, then run `pytest tests/integration/temporal/test_profile_backed_workload_contract.py -q --tb=short -m 'integration_ci'` as a focused fallback to confirm the MM-503 dispatcher/runtime shared-plane evidence passes
- [ ] T018 Review `specs/252-route-docker-workload-plane/spec.md`, `specs/252-route-docker-workload-plane/plan.md`, `specs/252-route-docker-workload-plane/research.md`, `specs/252-route-docker-workload-plane/contracts/shared-docker-workload-plane-contract.md`, `specs/252-route-docker-workload-plane/quickstart.md`, and `docs/tmp/jira-orchestration-inputs/MM-503-moonspec-orchestration-input.md` to confirm FR-007 and SC-006 preserve MM-503 across downstream artifacts

**Checkpoint**: MM-503 is complete when the existing shared workload-plane behavior is proven across curated and unrestricted tool classes and the canonical artifact set preserves the Jira source brief.

---

## Phase 4: Polish And Verification

**Purpose**: Final traceability, quickstart validation, and read-only verification for the completed story.

- [ ] T019 [P] Align the feature-local artifacts in `specs/252-route-docker-workload-plane/` after MM-503 verification work so terminology, traceability, and commands stay coherent
- [ ] T020 Run the quickstart validation from `specs/252-route-docker-workload-plane/quickstart.md` and record any environment blockers or deviations needed for MM-503
- [ ] T021 Run `/moonspec-verify` for `specs/252-route-docker-workload-plane/` and produce a final evidence-backed verification report covering MM-503, FR-001 through FR-007, SC-001 through SC-006, and DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, DESIGN-REQ-024

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies
- Foundational (Phase 2): depends on Setup completion
- Story (Phase 3): depends on Foundational completion
- Polish (Phase 4): depends on story validation completing

### Within The Story

- T005-T007 create the unit-level MM-503 verification boundary before red-first confirmation.
- T009-T010 create the integration-level MM-503 verification boundary before red-first confirmation.
- T008 and T011 must complete before T012.
- T013-T015 are conditional and only run if T012 confirms a real product gap.
- T016-T018 run after verification tests and any conditional fallback implementation complete.

### Parallel Opportunities

- T005, T006, and T007 can run in parallel because they touch different files.
- T009 and T010 must stay sequential because they modify the same integration test file.
- T019 can run in parallel with verification prep after T018.

## Implementation Strategy

1. Preserve MM-503 as the canonical MoonSpec source input and keep the feature-local artifact set intact.
2. Add the missing unit and hermetic integration proof for the shared Docker workload execution plane across curated and unrestricted tool classes.
3. Confirm the new tests fail for the expected MM-503 reasons before touching production code.
4. Execute fallback implementation tasks only if the new verification exposes a real cross-class gap.
5. Rerun the focused unit and integration validations.
6. Finish with quickstart validation and `/moonspec-verify` against the original MM-503 brief.
