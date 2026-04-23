# Tasks: Report Artifact Contract

**Input**: Design documents from `specs/244-define-report-artifact-contract/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-artifact-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration-style boundary verification are REQUIRED. For MM-492, run verification-first checks before any production changes, confirm failures if new verification coverage is added, and implement only if those checks expose drift from the spec.

**Organization**: Tasks are grouped around the single MM-492 story: define and verify the canonical report artifact contract while preserving explicit report semantics, compact bundles, bounded metadata, canonical report resolution, and separation from evidence and observability artifacts.

**Source Traceability**: MM-492; FR-001 through FR-011; acceptance scenarios 1-7; SC-001 through SC-006; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011.

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

**Purpose**: Confirm the planning artifacts and current verification targets for the single MM-492 story.

- [X] T001 Confirm `specs/244-define-report-artifact-contract/` contains `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-artifact-contract.md`, and `quickstart.md`
- [X] T002 Confirm focused verification targets exist in `tests/unit/workflows/temporal/test_artifacts.py`, `tests/unit/workflows/temporal/test_artifacts_activities.py`, `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, `tests/contract/test_temporal_artifact_api.py`, and `frontend/src/entrypoints/task-detail.test.tsx`

---

## Phase 2: Foundational

**Purpose**: Validate that MM-492 reuses existing report artifact infrastructure and requires no new schema, dependency, or environment foundation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Confirm no database migration or new persistent storage is required because MM-492 uses the existing artifact tables and store through `moonmind/workflows/temporal/artifacts.py`
- [X] T004 Confirm no new package or service dependency is required because MM-492 relies on existing report artifact helpers, API contract coverage, and Mission Control consumer paths in `moonmind/workflows/temporal/report_artifacts.py`, `tests/contract/test_temporal_artifact_api.py`, and `frontend/src/entrypoints/task-detail.tsx`

**Checkpoint**: Foundation ready - story verification and any fallback implementation work can now begin.

---

## Phase 3: Story - Define Report Deliverable Semantics

**Summary**: As a workflow producer, I want a canonical report artifact contract so report deliverables use explicit artifact semantics without introducing a second storage system.

**Independent Test**: Validate a report-producing workflow contract and a non-report generic output contract side by side, then confirm report deliverables use explicit `report.*` semantics, bundles contain only refs plus bounded metadata, standardized metadata rejects unsafe values, canonical report resolution is server/link-driven, and report/evidence/observability separation holds across backend and consumer boundaries.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011

**Unit Test Plan**:

- Verify report link semantics, generic fallback behavior, compact bundle validation, metadata safety, and bundle publication boundaries in the existing unit suites.
- Add or tighten unit assertions only where current evidence does not fully cover MM-492 terminology or contract drift.

**Integration Test Plan**:

- Verify canonical report resolution remains server/link-driven through `tests/contract/test_temporal_artifact_api.py`.
- Verify Mission Control continues to consume canonical `report.primary` artifacts and related report content without local heuristics in `frontend/src/entrypoints/task-detail.test.tsx`.
- Escalate to `./tools/test_integration.sh` only if fallback implementation changes artifact persistence, activity publication, or API serialization/linkage.

### Unit Verification Tests (write first)

- [X] T005 [P] Review existing unit assertions in `tests/unit/workflows/temporal/test_artifacts.py` for FR-001 through FR-007, SC-001 through SC-003, and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-007, DESIGN-REQ-008; no tightening was required because current coverage already matches MM-492
- [X] T006 [P] Review existing unit assertions in `tests/unit/workflows/temporal/test_report_workflow_rollout.py` for FR-008, FR-009, SC-004, SC-005, DESIGN-REQ-010, and DESIGN-REQ-011; no tightening was required because current coverage already matches MM-492
- [X] T007 [P] Review existing report bundle publication boundary coverage in `tests/unit/workflows/temporal/test_artifacts_activities.py` for FR-005, FR-006, and SC-002; no tightening was required because current coverage already matches MM-492

### Integration-Style Boundary Tests (write first)

- [X] T008 [P] Review existing contract assertions in `tests/contract/test_temporal_artifact_api.py` for canonical latest `report.primary` resolution and explicit link-type behavior covering FR-008, SC-004, and DESIGN-REQ-010; no tightening was required because current coverage already matches MM-492
- [X] T009 [P] Review existing Mission Control report-consumption assertions in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-008, FR-009, SC-004, SC-005, DESIGN-REQ-010, and DESIGN-REQ-011; no tightening was required because current coverage already matches MM-492

### Red-First Confirmation

- [X] T010 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifacts_activities.py tests/unit/workflows/temporal/test_report_workflow_rollout.py`; the focused MM-492 unit verification passed without exposing drift, so no new red-first failure was required before production changes
- [X] T011 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`; the focused MM-492 boundary verification passed without exposing drift, so no new red-first failure was required before production changes

### Fallback Implementation Tasks (only if T010 or T011 exposes drift)

- [X] T012 No unit-side implementation changes were required in `moonmind/workflows/temporal/report_artifacts.py` because T010 exposed no MM-492 drift
- [X] T013 No report bundle publication changes were required in `moonmind/workflows/temporal/artifacts.py` or `tests/unit/workflows/temporal/test_artifacts_activities.py` because T010 exposed no MM-492 drift
- [X] T014 No API or Mission Control changes were required in `tests/contract/test_temporal_artifact_api.py`, `frontend/src/entrypoints/task-detail.tsx`, or `frontend/src/entrypoints/task-detail.test.tsx` because T011 exposed no MM-492 drift
- [X] T015 No terminology or traceability remediation was required in `specs/244-define-report-artifact-contract/data-model.md`, `specs/244-define-report-artifact-contract/contracts/report-artifact-contract.md`, or runtime docstrings because the review found no MM-492 drift

### Story Validation

- [X] T016 Rerun the focused unit command from T010 until MM-492 verification passes
- [X] T017 Rerun the boundary verification command from T011 until MM-492 verification passes
- [X] T018 Hermetic integration escalation was not required for MM-492 because no artifact persistence, activity publication, or API serialization/linkage changes were needed
- [X] T019 Review `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-artifact-contract.md`, `quickstart.md`, code, and tests for MM-492 traceability covering FR-011 and SC-006

**Checkpoint**: The MM-492 story is verified against the current repo, any discovered drift is resolved, and the contract remains independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Complete final validation and preserve story-level evidence without adding hidden scope.

- [X] T020 [P] No `quickstart.md` or feature-artifact updates were required because the executed verification commands matched the MM-492 plan
- [X] T021 Final full-unit rerun was not required because no code or test files changed during MM-492 implementation review
- [X] T022 Run `/moonspec-verify` equivalent for `specs/244-define-report-artifact-contract/` and produce the final verification artifact covering MM-492, FR-001 through FR-011, SC-001 through SC-006, and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on focused story verification completing.

### Within The Story

- T005-T009 must be completed before red-first confirmation.
- T010-T011 must confirm red-first behavior for any newly added verification coverage before fallback implementation begins.
- T012-T015 are conditional and run only if T010 or T011 exposes MM-492 drift.
- T016-T017 validate story completion after any needed fixes.
- T018 is required only when MM-492 changes cross the hermetic integration boundary.
- T019 must complete before the final polish and verification phase.

### Parallel Opportunities

- T005-T007 can run in parallel only with coordination because they touch different report verification suites.
- T008-T009 can run in parallel because they touch different boundary verification files.
- T020 can run in parallel with final verification preparation after T019 completes.

## Implementation Strategy

1. Confirm the MM-492 planning artifacts and existing verification targets.
2. Tighten unit and boundary verification coverage first.
3. Confirm red-first behavior for any new verification checks.
4. Apply the smallest contract-preserving runtime or consumer fix only if verification exposes drift.
5. Rerun focused verification, then escalate to hermetic integration only when the changed surface warrants it.
6. Preserve MM-492 traceability and finish with `/moonspec-verify`.
