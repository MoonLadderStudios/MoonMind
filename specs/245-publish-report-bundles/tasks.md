# Tasks: Publish Report Bundles

**Input**: Design documents from `specs/245-publish-report-bundles/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-bundle-publication-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration-style boundary verification are REQUIRED. For MM-493, run verification-first checks before any production changes, confirm failures if new verification coverage is added, and implement only if those checks expose drift from the spec.

**Organization**: Tasks are grouped around the single MM-493 story: publish immutable report bundles from workflows through activity/service boundaries while preserving compact workflow state, canonical final report behavior, latest-report resolution, and downstream traceability.

**Source Traceability**: MM-493; FR-001 through FR-009; acceptance scenarios 1-7; SC-001 through SC-007; DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifacts_activities.py tests/unit/workflows/temporal/test_report_workflow_rollout.py`
- Integration-style boundary verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Hermetic integration escalation: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the planning artifacts and current verification targets for the single MM-493 story.

- [ ] T001 Confirm `specs/245-publish-report-bundles/` contains `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-bundle-publication-contract.md`, and `quickstart.md`
- [ ] T002 Confirm focused verification targets exist in `tests/unit/workflows/temporal/test_artifacts.py`, `tests/unit/workflows/temporal/test_artifacts_activities.py`, `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, `tests/contract/test_temporal_artifact_api.py`, and `frontend/src/entrypoints/task-detail.test.tsx`

---

## Phase 2: Foundational

**Purpose**: Validate that MM-493 reuses existing report artifact infrastructure and requires no new schema, dependency, or environment foundation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T003 Confirm no database migration or new persistent storage is required because MM-493 uses the existing artifact tables and store through `moonmind/workflows/temporal/artifacts.py`
- [ ] T004 Confirm no new package or service dependency is required because MM-493 relies on existing report bundle helpers, API contract coverage, and Mission Control consumer paths in `moonmind/workflows/temporal/report_artifacts.py`, `tests/contract/test_temporal_artifact_api.py`, and `frontend/src/entrypoints/task-detail.tsx`

**Checkpoint**: Foundation ready - story verification and any fallback implementation work can now begin.

---

## Phase 3: Story - Publish Immutable Report Bundles

**Summary**: As an operator, I want report-producing workflows to publish immutable report bundles through activities so completed executions expose durable final and step-level reports without workflow-history bloat.

**Independent Test**: Trigger a report-producing workflow path that publishes final and step-scoped report artifacts, then verify the workflow-visible result contains only compact refs and bounded metadata, exactly one canonical final report is identifiable, intermediate artifacts remain immutable, and latest report resolution comes from server behavior instead of browser-side sorting.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021

**Unit Test Plan**:

- Verify activity/service-owned report publication, compact bundle validation, final marker enforcement, step metadata propagation, and workflow-family rollout behavior in the existing unit suites.
- Add or tighten unit assertions only where current evidence does not fully cover MM-493 coexistence, immutability, or traceability.

**Integration Test Plan**:

- Verify latest canonical `report.primary` resolution remains server/link-driven through `tests/contract/test_temporal_artifact_api.py`.
- Verify Mission Control continues to consume canonical `report.primary` artifacts without browser-side heuristics in `frontend/src/entrypoints/task-detail.test.tsx`.
- Escalate to `./tools/test_integration.sh` only if fallback implementation changes artifact persistence, activity publication, or API serialization/linkage.

### Unit Verification Tests (write first)

- [ ] T005 [P] Review existing unit assertions in `tests/unit/workflows/temporal/test_artifacts.py` for FR-001 through FR-005, SC-001 through SC-005, and DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-020 to confirm MM-493 publication, compact state, execution linkage, step metadata, and final marker coverage
- [ ] T006 [P] Review existing unit assertions in `tests/unit/workflows/temporal/test_report_workflow_rollout.py` for FR-006, FR-007, SC-006, DESIGN-REQ-019, and DESIGN-REQ-021 to confirm server-driven latest resolution and multi-workflow-family contract behavior
- [ ] T007 [P] Review existing activity-boundary coverage in `tests/unit/workflows/temporal/test_artifacts_activities.py` for FR-001, FR-002, and SC-001 to confirm activity facade publication remains aligned with MM-493
- [ ] T008 [P] Add or tighten unit assertions for FR-008, SC-005, and SC-007 in `tests/unit/workflows/temporal/test_artifacts.py` if current coverage does not explicitly prove intermediate and final report coexistence without artifact mutation or MM-493 traceability preservation

### Integration-Style Boundary Tests (write first)

- [ ] T009 [P] Review existing contract assertions in `tests/contract/test_temporal_artifact_api.py` for FR-003, FR-006, SC-003, SC-006, DESIGN-REQ-012, and DESIGN-REQ-021 to confirm latest `report.primary` lookup remains execution-scoped and server-defined
- [ ] T010 [P] Review existing Mission Control report-consumption assertions in `frontend/src/entrypoints/task-detail.test.tsx` for FR-006, SC-006, and DESIGN-REQ-021 to confirm the UI consumes canonical report artifacts without browser-side sorting heuristics
- [ ] T011 [P] Add or tighten boundary assertions in `tests/contract/test_temporal_artifact_api.py` or `frontend/src/entrypoints/task-detail.test.tsx` if current evidence does not explicitly cover MM-493 coexistence or final/latest report presentation behavior

### Red-First Confirmation

- [ ] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifacts_activities.py tests/unit/workflows/temporal/test_report_workflow_rollout.py` to confirm the MM-493-focused unit verification passes or exposes the intended drift after any new assertions from T008
- [ ] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py --ui-args frontend/src/entrypoints/task-detail.test.tsx` to confirm the MM-493 boundary verification passes or exposes the intended drift after any new assertions from T011

### Fallback Implementation Tasks (only if T012 or T013 exposes drift)

- [ ] T014 Update `moonmind/workflows/temporal/report_artifacts.py` to preserve FR-002, FR-006, FR-007, DESIGN-REQ-005, DESIGN-REQ-019, and DESIGN-REQ-021 if T012 exposes compact bundle, rollout, or latest-resolution drift
- [ ] T015 Update `moonmind/workflows/temporal/artifacts.py` and `tests/unit/workflows/temporal/test_artifacts_activities.py` to preserve FR-001, FR-003, FR-004, FR-005, FR-008, DESIGN-REQ-006, DESIGN-REQ-012, DESIGN-REQ-013, and DESIGN-REQ-020 if T012 exposes publication, linkage, final-marker, or immutability drift
- [ ] T016 Update `tests/contract/test_temporal_artifact_api.py`, `frontend/src/entrypoints/task-detail.tsx`, and `frontend/src/entrypoints/task-detail.test.tsx` to preserve FR-006, FR-008, SC-005, SC-006, and DESIGN-REQ-021 if T013 exposes API or Mission Control canonical report consumption drift
- [ ] T017 Update `specs/245-publish-report-bundles/data-model.md`, `specs/245-publish-report-bundles/contracts/report-bundle-publication-contract.md`, or related runtime docstrings to preserve FR-009 and SC-007 if verification exposes traceability or contract-language drift

### Story Validation

- [ ] T018 Rerun the focused unit command from T012 until MM-493 verification passes
- [ ] T019 Rerun the boundary verification command from T013 until MM-493 verification passes
- [ ] T020 Run `./tools/test_integration.sh` only if T014-T016 changed artifact persistence, activity publication, or API serialization/linkage; otherwise record that hermetic integration escalation was not required for MM-493
- [ ] T021 Review `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-bundle-publication-contract.md`, `quickstart.md`, code, and tests for MM-493 traceability covering FR-009 and SC-007

**Checkpoint**: The MM-493 story is verified against the current repo, any discovered drift is resolved, and report bundle publication remains independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Complete final validation and preserve story-level evidence without adding hidden scope.

- [ ] T022 [P] Update `specs/245-publish-report-bundles/quickstart.md` only if the executed verification commands or contingencies differ from the current MM-493 plan
- [ ] T023 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` if T014-T016 changed code or tests; otherwise record why the full unit rerun was not required
- [ ] T024 Run `/moonspec-verify` for `specs/245-publish-report-bundles/` and produce the final verification artifact covering MM-493, FR-001 through FR-009, SC-001 through SC-007, and DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on focused story verification completing.

### Within The Story

- T005-T011 must complete before red-first confirmation.
- T012-T013 must confirm verification behavior before fallback implementation begins.
- T014-T017 are conditional and run only if T012 or T013 exposes MM-493 drift.
- T018-T019 validate story completion after any needed fixes.
- T020 is required only when MM-493 changes cross the hermetic integration boundary.
- T021 must complete before the final polish and verification phase.

### Parallel Opportunities

- T005-T008 can run in parallel only with coordination because they touch different report verification suites.
- T009-T011 can run in parallel because they touch different boundary verification files.
- T022 can run in parallel with verification preparation after T021 completes.

## Implementation Strategy

1. Confirm the MM-493 planning artifacts and existing verification targets.
2. Tighten unit and boundary verification coverage first.
3. Confirm verification behavior with focused unit and boundary commands.
4. Apply the smallest contract-preserving runtime or consumer fix only if verification exposes drift.
5. Rerun focused verification, then escalate to hermetic integration only when the changed surface warrants it.
6. Preserve MM-493 traceability and finish with `/moonspec-verify`.
