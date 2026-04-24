# Tasks: Publish Durable DooD Observability Outputs

**Input**: Design documents from `specs/253-publish-dood-observability/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/dood-observability-publication-contract.md`, `quickstart.md`

**Tests**: Unit tests and hermetic integration verification are REQUIRED. For MM-504, the repository already contains most of the workload artifact publication and report/metadata behavior, so the story work is verification-first: preserve the canonical artifacts, add focused proof for durable outputs, report publication, audit metadata, and redaction behavior, and only touch production code if verification exposes real drift.

**Organization**: Tasks are grouped around the single MM-504 story: verify durable workload artifacts, shared report publication, redacted audit metadata, operator-visible inspection, and artifact-class consistency for Docker-backed workloads without widening scope beyond the existing launcher and artifact boundaries.

**Source Traceability**: MM-504; FR-001 through FR-007; acceptance scenarios 1-6; SC-001 through SC-007; DESIGN-REQ-021, DESIGN-REQ-022.

**Requirement Status Summary**: Verification-first with conditional fallback implementation. `FR-007` is already implemented and verified by the feature-local artifact set. `FR-001`, `FR-003`, and `FR-006` are implemented but still need explicit story-level proof. `FR-002`, `FR-004`, `FR-005`, `DESIGN-REQ-021`, and `DESIGN-REQ-022` remain partial because the current evidence does not yet conclusively prove shared report publication, metadata completeness, or redaction behavior across the full MM-504 scope. Add tests first, confirm the expected failure or missing proof, then execute fallback implementation tasks only if the new verification exposes a real runtime gap.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/api/routers/test_task_runs.py`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

**Purpose**: Confirm the MM-504 source brief, planning artifacts, and runtime surfaces before verification work begins.

- [ ] T001 Confirm `spec.md` (Input), `specs/253-publish-dood-observability/spec.md`, `specs/253-publish-dood-observability/plan.md`, `specs/253-publish-dood-observability/research.md`, `specs/253-publish-dood-observability/data-model.md`, `specs/253-publish-dood-observability/contracts/dood-observability-publication-contract.md`, and `specs/253-publish-dood-observability/quickstart.md` remain the canonical MM-504 artifact set for FR-007 and SC-007
- [ ] T002 Confirm the MM-504 runtime touchpoints in `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/registry.py`, `moonmind/workflows/temporal/artifacts.py`, `moonmind/workflows/temporal/report_artifacts.py`, `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/temporal/workflows/run.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_activity_runtime.py`, `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, `tests/unit/api/routers/test_task_runs.py`, and `tests/integration/temporal/test_profile_backed_workload_contract.py`

---

## Phase 2: Foundational

**Purpose**: Lock the verification scope and prerequisites before story execution.

- [ ] T003 Confirm `specs/253-publish-dood-observability/` needs no migration or new persistent storage because MM-504 is a runtime publication and verification story bounded to existing artifact-backed outputs
- [ ] T004 Confirm `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_activity_runtime.py`, `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, `tests/unit/api/routers/test_task_runs.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py`, and `tests/integration/temporal/test_temporal_artifact_lifecycle.py` are the correct validation surfaces for FR-001 through FR-006 and DESIGN-REQ-021/022

**Checkpoint**: Foundation ready - story verification work can now begin.

---

## Phase 3: Story - Publish Durable DooD Observability Outputs

**Summary**: As an operator, I want durable artifacts, reports, and audit metadata for every Docker-backed workload so I can inspect execution outcomes without depending on transient daemon state or terminal history.

**Independent Test**: Execute representative Docker-backed workloads across the supported launch types, then verify each run produces durable summary, log, diagnostics, and declared output artifacts; requested reports follow the shared publication contract; audit metadata exposes workload mode and access class without leaking raw secret-looking values; and operators can review results without relying on daemon-local state or scrollback.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-021, DESIGN-REQ-022

**Unit Test Plan**:

- Extend launcher, tool-bridge, activity-runtime, report-rollout, and task-run inspection unit suites to confirm durable runtime outputs, declared primary report handling, artifact-class consistency, and redacted audit metadata for representative Docker-backed workload paths.

**Integration Test Plan**:

- Extend the existing hermetic Temporal workload integration suites to prove profile-backed and curated Docker-backed executions publish inspectable artifacts and bounded metadata end to end, then add targeted assertions only where unit verification shows cross-path drift.

### Unit Tests (write first) ⚠️

- [ ] T005 [P] Add failing unit tests for FR-001, FR-005, SC-001, SC-004, DESIGN-REQ-021, and DESIGN-REQ-022 in `tests/unit/workloads/test_docker_workload_launcher.py` covering durable stdout/stderr/diagnostics publication, partial publication failure metadata, docker-host normalization, and redaction of secret-like values in published outputs
- [ ] T006 [P] Add failing unit tests for FR-002, FR-004, SC-002, SC-003, DESIGN-REQ-021, and DESIGN-REQ-022 in `tests/unit/workloads/test_workload_tool_bridge.py` covering declared primary report publication semantics, bounded workload access metadata, and explicit unrestricted markers for representative Docker-backed tools
- [ ] T007 [P] Add failing unit tests for FR-003, FR-006, SC-005, SC-006, and DESIGN-REQ-021 in `tests/unit/api/routers/test_task_runs.py` and `tests/unit/workflows/temporal/test_report_workflow_rollout.py` covering operator-visible artifact inspection and artifact-class consistency across supported Docker-backed publication paths
- [ ] T008 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/api/routers/test_task_runs.py` to confirm T005-T007 fail for the expected MM-504 reason before any production changes

### Integration Tests (write first) ⚠️

- [ ] T009 Add a failing hermetic integration test for FR-001, FR-003, SC-001, SC-005, and DESIGN-REQ-021 in `tests/integration/temporal/test_profile_backed_workload_contract.py` covering durable workload artifact publication and operator-inspectable results for profile-backed Docker-backed execution
- [ ] T010 Add a failing hermetic integration test for FR-002, FR-004, FR-005, FR-006, SC-002, SC-003, SC-004, SC-006, DESIGN-REQ-021, and DESIGN-REQ-022 in `tests/integration/temporal/test_integration_ci_tool_contract.py` and `tests/integration/temporal/test_temporal_artifact_lifecycle.py` covering shared report publication semantics, bounded workload metadata, redaction behavior, and artifact-class consistency through the trusted workload plane
- [ ] T011 Run `./tools/test_integration.sh` and record the MM-504-specific failure surface from T009-T010 before any production changes

### Red-First Confirmation

- [ ] T012 Review the failures from `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, `tests/unit/api/routers/test_task_runs.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py`, and `tests/integration/temporal/test_temporal_artifact_lifecycle.py` to confirm the red-first evidence maps to FR-001 through FR-006 and DESIGN-REQ-021/022 rather than test-authoring mistakes

### Conditional Fallback Implementation (only if verification fails) ⚠️

- [ ] T013 Conditionally update `moonmind/workloads/docker_launcher.py` for FR-001, FR-004, FR-005, DESIGN-REQ-021, and DESIGN-REQ-022 only if T008-T012 show gaps in durable artifact publication, workload metadata, or redaction behavior
- [ ] T014 Conditionally update `moonmind/workloads/tool_bridge.py` and `moonmind/workloads/registry.py` for FR-002, FR-004, SC-002, SC-003, and DESIGN-REQ-021/022 only if T008-T012 show missing declared-report publication semantics or incomplete workload access / unrestricted metadata
- [ ] T015 Conditionally update `moonmind/workflows/temporal/artifacts.py`, `moonmind/workflows/temporal/report_artifacts.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/workflows/temporal/workflows/run.py` for FR-003, FR-006, SC-005, SC-006, and DESIGN-REQ-021 only if T008-T012 show artifact-class or operator-inspection drift at the Temporal boundary

### Story Validation

- [ ] T016 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/api/routers/test_task_runs.py` and confirm MM-504 unit evidence passes together
- [ ] T017 Run `./tools/test_integration.sh`, record any exact environment blocker, and confirm MM-504 artifact publication and inspection evidence is green or narrowed to a precise remaining gap
- [ ] T018 Review `specs/253-publish-dood-observability/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/dood-observability-publication-contract.md`, `quickstart.md`, and `spec.md` (Input) to confirm FR-007 and SC-007 preserve MM-504 and the original Jira preset brief across downstream artifacts

**Checkpoint**: MM-504 is complete when Docker-backed workloads publish durable observability evidence, shared report semantics, and redacted audit metadata through existing runtime boundaries, the focused unit and hermetic integration evidence is green, and the canonical artifact set preserves the Jira source brief.

---

## Phase 4: Polish And Verification

**Purpose**: Final traceability, quickstart validation, and read-only verification for the completed story.

- [ ] T019 [P] Align `specs/253-publish-dood-observability/plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/dood-observability-publication-contract.md` after MM-504 verification work so terminology, commands, and requirement-status notes stay coherent
- [ ] T020 Run the quickstart validation from `specs/253-publish-dood-observability/quickstart.md` and record any environment blockers or deviations needed for MM-504
- [ ] T021 Run `/moonspec-verify` for `specs/253-publish-dood-observability/` and produce a final evidence-backed verification report covering MM-504, FR-001 through FR-007, SC-001 through SC-007, and DESIGN-REQ-021 through DESIGN-REQ-022

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies
- Foundational (Phase 2): depends on Setup completion
- Story (Phase 3): depends on Foundational completion
- Polish (Phase 4): depends on story validation completing

### Within The Story

- T005-T007 create the unit-level MM-504 verification boundary before red-first confirmation.
- T009-T010 create the integration-level MM-504 verification boundary before red-first confirmation.
- T008 and T011 must complete before T012.
- T013-T015 are conditional and only run if T012 confirms a real product gap.
- T016-T018 run after verification tests and any conditional fallback implementation complete.

### Parallel Opportunities

- T005, T006, and T007 can run in parallel because they touch different files.
- T009 and T010 can run in parallel because they touch different integration files.
- T019 can run in parallel with verification-prep work after T018.

## Implementation Strategy

1. Preserve MM-504 as the canonical MoonSpec source input and feature-local artifact set.
2. Add the missing unit and hermetic integration proof for durable workload artifacts, shared report publication, audit metadata, redaction, and artifact-class consistency.
3. Confirm the new tests fail for the expected MM-504 reasons before touching production code.
4. Execute fallback implementation tasks only if the new verification exposes a real runtime gap.
5. Re-run the focused unit and integration validations.
6. Finish with quickstart validation and `/moonspec-verify` against the original MM-504 brief.
