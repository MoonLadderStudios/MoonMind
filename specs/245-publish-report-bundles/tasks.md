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

- [X] T001 Confirmed `specs/245-publish-report-bundles/` contains `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-bundle-publication-contract.md`, and `quickstart.md`
- [X] T002 Confirmed focused verification targets exist in `tests/unit/workflows/temporal/test_artifacts.py`, `tests/unit/workflows/temporal/test_artifacts_activities.py`, `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, `tests/contract/test_temporal_artifact_api.py`, and `frontend/src/entrypoints/task-detail.test.tsx`

---

## Phase 2: Foundational

**Purpose**: Validate that MM-493 reuses existing report artifact infrastructure and requires no new schema, dependency, or environment foundation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Confirmed no database migration or new persistent storage is required because MM-493 uses the existing artifact tables and store through `moonmind/workflows/temporal/artifacts.py`
- [X] T004 Confirmed no new package or service dependency is required because MM-493 relies on existing report bundle helpers, API contract coverage, and Mission Control consumer paths in `moonmind/workflows/temporal/report_artifacts.py`, `tests/contract/test_temporal_artifact_api.py`, and `frontend/src/entrypoints/task-detail.tsx`

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

- [X] T005 [P] Reviewed existing unit assertions in `tests/unit/workflows/temporal/test_artifacts.py` for FR-001 through FR-005, SC-001 through SC-005, and DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-020; current coverage already matched MM-493 publication, compact state, execution linkage, step metadata, and final marker behavior
- [X] T006 [P] Reviewed existing unit assertions in `tests/unit/workflows/temporal/test_report_workflow_rollout.py` for FR-006, FR-007, SC-006, DESIGN-REQ-019, and DESIGN-REQ-021; current coverage already matched MM-493 server-driven latest resolution and multi-workflow-family contract behavior
- [X] T007 [P] Reviewed existing activity-boundary coverage in `tests/unit/workflows/temporal/test_artifacts_activities.py` for FR-001, FR-002, and SC-001; the activity facade publication path already aligned with MM-493
- [X] T008 [P] Added `test_latest_report_primary_coexists_with_intermediate_report_without_mutation` to `tests/unit/workflows/temporal/test_artifacts.py` for FR-008, SC-005, and SC-007, closing the remaining coexistence and non-mutation evidence gap without requiring production-code changes

### Integration-Style Boundary Tests (write first)

- [X] T009 [P] Reviewed existing contract assertions in `tests/contract/test_temporal_artifact_api.py` for FR-003, FR-006, SC-003, SC-006, DESIGN-REQ-012, and DESIGN-REQ-021; latest `report.primary` lookup already remained execution-scoped and server-defined
- [X] T010 [P] Reviewed existing Mission Control report-consumption assertions in `frontend/src/entrypoints/task-detail.test.tsx` for FR-006, SC-006, and DESIGN-REQ-021; the UI already consumed canonical report artifacts without browser-side sorting heuristics
- [X] T011 [P] No boundary assertion changes were required in `tests/contract/test_temporal_artifact_api.py` or `frontend/src/entrypoints/task-detail.test.tsx` because existing coverage already matched MM-493 coexistence and final/latest report presentation behavior

### Red-First Confirmation

- [X] T012 Ran `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifacts_activities.py tests/unit/workflows/temporal/test_report_workflow_rollout.py`; the MM-493-focused unit verification passed, including the new coexistence test, so no runtime drift was exposed
- [X] T013 Ran `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`; the MM-493 boundary verification passed with 3 contract tests and 84 focused Mission Control tests

### Fallback Implementation Tasks (only if T012 or T013 exposes drift)

- [X] T014 No update was required in `moonmind/workflows/temporal/report_artifacts.py` because T012 exposed no compact bundle, rollout, or latest-resolution drift
- [X] T015 No update was required in `moonmind/workflows/temporal/artifacts.py` or `tests/unit/workflows/temporal/test_artifacts_activities.py` because T012 exposed no publication, linkage, final-marker, or immutability drift
- [X] T016 No update was required in `tests/contract/test_temporal_artifact_api.py`, `frontend/src/entrypoints/task-detail.tsx`, or `frontend/src/entrypoints/task-detail.test.tsx` because T013 exposed no API or Mission Control canonical report consumption drift
- [X] T017 Updated `specs/245-publish-report-bundles/plan.md`, `specs/245-publish-report-bundles/research.md`, and `specs/245-publish-report-bundles/verification.md` to preserve FR-009 and SC-007 after the new MM-493 coexistence verification closed the last unverified requirement

### Story Validation

- [X] T018 Reran the focused unit command from T012 until MM-493 verification passed; the updated `tests/unit/workflows/temporal/test_artifacts.py` suite and the wrapped focused unit command both passed
- [X] T019 Boundary verification from T013 remained passing after the MM-493 unit-test addition because no API or UI behavior changed after the contract/UI run
- [X] T020 Hermetic integration escalation was not required for MM-493 because no production-code or API-wiring change touched artifact persistence, activity publication, or API serialization/linkage
- [X] T021 Reviewed `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-bundle-publication-contract.md`, `quickstart.md`, code, and tests for MM-493 traceability covering FR-009 and SC-007

**Checkpoint**: The MM-493 story is verified against the current repo, no production-code drift was found, and report bundle publication remains independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Complete final validation and preserve story-level evidence without adding hidden scope.

- [X] T022 [P] No `quickstart.md` update was required because the executed verification commands matched the current MM-493 plan
- [X] T023 Ran `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; the full unit suite passed after the MM-493 unit-test addition
- [X] T024 Ran `/moonspec-verify` equivalent for `specs/245-publish-report-bundles/` and produced `specs/245-publish-report-bundles/verification.md` covering MM-493, FR-001 through FR-009, SC-001 through SC-007, and DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on focused story verification completing.

### Within The Story

- T005-T011 completed before red-first confirmation.
- T012-T013 confirmed verification behavior before any fallback implementation would have begun.
- T014-T017 were resolved conservatively after T012 and T013 showed no MM-493 runtime drift.
- T018-T019 validated story completion after the new coexistence verification was added.
- T020 was intentionally skipped as not required because MM-493 did not cross the hermetic integration boundary.
- T021 completed before the final polish and verification phase.

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
