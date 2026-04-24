# Tasks: Shimmer Quality Regression Guardrails

**Input**: Design documents from `specs/247-guard-shimmer-quality/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/status-pill-shimmer-quality.md`, `quickstart.md`

## Validation Commands

- Unit tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts`
- Integration tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`
- Full unit suite: `./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Source Traceability Summary

- MM-491: Guard shimmer quality across states, themes, and layouts.
- FR-001 through FR-007: readability and bounds, state-matrix isolation, layout stability, theme intent, reduced-motion active fallback, non-goal preservation, and MM-491 traceability.
- SCN-001 through SCN-005: executing readability and bounds, non-executing isolation, layout stability, theme intent, and reduced-motion active fallback.
- SC-001 through SC-007: measurable proof for readability, clipping, non-executing exclusion, layout stability, theme intent, reduced-motion fallback, and MM-491 traceability.
- DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-011, DESIGN-REQ-014, DESIGN-REQ-016: host text/layout guardrails, isolation rules, reduced-motion behavior, state matrix, and non-goal preservation.
- Requirement status summary from `plan.md`: 1 `missing`, 3 `partial`, 18 `implemented_unverified`, 0 `implemented_verified`.

## Phase 1: Setup

- [ ] T001 Verify MM-491 planning inputs and target frontend files in `specs/247-guard-shimmer-quality/spec.md`, `specs/247-guard-shimmer-quality/plan.md`, `specs/247-guard-shimmer-quality/research.md`, `specs/247-guard-shimmer-quality/quickstart.md`, `frontend/src/styles/mission-control.css`, `frontend/src/utils/executionStatusPillClasses.ts`, `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/task-detail.test.tsx`.
- [ ] T002 Confirm the focused unit and integration validation commands in `specs/247-guard-shimmer-quality/quickstart.md` remain the active test path for MM-491 and require no new package or service setup.

## Phase 2: Foundational

- [ ] T003 Reconcile the MM-491 quality-guardrail contract and data model in `specs/247-guard-shimmer-quality/contracts/status-pill-shimmer-quality.md` and `specs/247-guard-shimmer-quality/data-model.md` with the current shared Mission Control shimmer seams before story verification begins.
- [ ] T004 Confirm MM-491 stays within the existing shared status-pill helper, Mission Control stylesheet, and list/detail render surfaces in `specs/247-guard-shimmer-quality/plan.md` and `specs/247-guard-shimmer-quality/research.md`, with no new component, route, or infrastructure.

## Phase 3: Story - Guard Shimmer Quality Regressions

**Summary**: As a MoonMind maintainer, I want automated shimmer regression coverage so executing pills stay readable, bounded, stable, and correctly scoped across supported states and themes.

**Independent Test**: Render supported Mission Control status-pill surfaces in executing, every listed non-executing state, light and dark themes, and reduced-motion conditions, then verify executing pills remain readable, bounded, and layout-stable, non-executing pills remain plain, reduced motion preserves an active fallback without animation, and MM-491 traceability appears in runtime-adjacent evidence.

**Traceability IDs**: FR-001 through FR-007; SCN-001 through SCN-005; SC-001 through SC-007; DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-011, DESIGN-REQ-014, DESIGN-REQ-016.

### Unit Test Plan

- Shared CSS contract tests verify readability-supporting bounds, scrollbar isolation, theme-aware active treatment, reduced-motion fallback semantics, and non-goal preservation for the executing shimmer.
- Helper tests verify the full non-executing state matrix remains plain and MM-491 traceability is exported alongside adjacent shimmer-story references.

### Integration Test Plan

- Task list entrypoint tests verify executing pills keep the shared shimmer contract while the listed non-executing states remain plain on list and card surfaces.
- Task detail entrypoint tests verify the same shared shimmer contract, reduced-motion active fallback, and layout stability on detail surfaces.

### Tests First

- [ ] T005 [P] Add failing CSS contract tests for executing readability guardrails, rounded-bound clipping, scrollbar isolation, theme-aware active treatment, reduced-motion fallback semantics, and non-goal preservation in `frontend/src/entrypoints/mission-control.test.tsx` covering FR-001, FR-004, FR-005, FR-006, SCN-001, SCN-004, SCN-005, SC-001, SC-002, SC-005, SC-006, DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-011, and DESIGN-REQ-016.
- [ ] T006 [P] Add failing helper tests for the full non-executing state matrix and MM-491 traceability preservation in `frontend/src/utils/executionStatusPillClasses.test.ts` covering FR-002, FR-007, SCN-002, SC-003, SC-007, DESIGN-REQ-014, and MM-491 traceability requirements.
- [ ] T007 [P] Add failing task-list integration tests for executing readability, non-executing isolation, theme intent, reduced-motion active fallback, and layout stability in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-001, FR-002, FR-003, FR-004, FR-005, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-011, and DESIGN-REQ-014.
- [ ] T008 [P] Add failing task-detail integration tests for executing readability, non-executing isolation, theme intent, reduced-motion active fallback, and layout stability in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-001, FR-002, FR-003, FR-004, FR-005, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-011, and DESIGN-REQ-014.
- [ ] T009 Run the focused unit and integration validation commands from `specs/247-guard-shimmer-quality/quickstart.md` to confirm T005-T008 fail for the expected MM-491 gaps before production changes.

### Conditional Fallback For Implemented-Unverified Rows

- [ ] T010 If T005 or T009 shows the shared CSS contract does not preserve executing readability, bounds, scrollbar isolation, theme intent, reduced-motion fallback clarity, or non-goal constraints, update `frontend/src/styles/mission-control.css` for FR-001, FR-004, FR-005, FR-006, SCN-001, SCN-004, SCN-005, SC-001, SC-002, SC-005, SC-006, DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-011, and DESIGN-REQ-016.
- [ ] T011 If T007-T009 show supported list/detail surfaces do not preserve non-executing isolation, layout stability, or reduced-motion active comprehension, update `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/entrypoints/task-detail.tsx`, and `frontend/src/styles/mission-control.css` for FR-002, FR-003, FR-005, SCN-002, SCN-003, SCN-005, SC-003, SC-004, SC-006, DESIGN-REQ-009, DESIGN-REQ-011, and DESIGN-REQ-014.
- [ ] T012 If T005-T009 show the current shimmer verification surface cannot express the MM-491 non-goal contract cleanly, update `specs/247-guard-shimmer-quality/contracts/status-pill-shimmer-quality.md`, `frontend/src/entrypoints/mission-control.test.tsx`, and `specs/247-guard-shimmer-quality/plan.md` to keep the regression target explicit for FR-006 and DESIGN-REQ-016.

### Implementation

- [ ] T013 Add the missing MM-491 traceability surface in `frontend/src/utils/executionStatusPillClasses.ts` and `frontend/src/utils/executionStatusPillClasses.test.ts` for FR-007 and SC-007 while preserving adjacent MM-488/MM-489/MM-490 references.
- [ ] T014 Re-run the focused unit validation command from `specs/247-guard-shimmer-quality/quickstart.md`, fix any failing MM-491 CSS/helper assertions in `frontend/src/styles/mission-control.css` and `frontend/src/utils/executionStatusPillClasses.ts`, and confirm the verification-first tasks now pass.
- [ ] T015 Re-run the focused integration validation command from `specs/247-guard-shimmer-quality/quickstart.md`, fix any failing MM-491 render assertions in `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/entrypoints/task-detail.tsx`, and `frontend/src/styles/mission-control.css`, and confirm the story passes end to end on supported surfaces.

### Story Validation

- [ ] T016 Update MM-491 requirement-status evidence in `specs/247-guard-shimmer-quality/plan.md` after T014-T015 so `missing`, `partial`, and `implemented_unverified` rows reflect the final proof.
- [ ] T017 Verify the independent story criteria in `specs/247-guard-shimmer-quality/quickstart.md` and record any remaining MM-491-specific gaps before polish work.

## Final Phase: Polish and Verification

- [ ] T018 Expand edge-case coverage for full state-matrix isolation, sampled readability points, and layout-stability guardrails in `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/task-detail.test.tsx` as needed for SCN-001, SCN-002, SCN-003, SC-001, SC-003, SC-004, DESIGN-REQ-004, DESIGN-REQ-009, and DESIGN-REQ-014.
- [ ] T019 Run the quickstart validation steps from `specs/247-guard-shimmer-quality/quickstart.md`.
- [ ] T020 Run `./tools/test_unit.sh` for final unit-test verification.
- [ ] T021 Run `/moonspec-verify` by creating `specs/247-guard-shimmer-quality/verification.md` with MM-491 traceability, DESIGN-REQ coverage, test evidence, and final verdict.

## Dependencies and Execution Order

1. T001-T004 establish the story inputs, scope boundaries, and shared-seam baseline.
2. T005-T009 write verification and failing regression tests first, then confirm they fail before code changes.
3. T010-T012 are conditional fallback tasks for the `implemented_unverified` rows and execute only if the verification-first tasks expose real gaps.
4. T013 completes the known missing implementation work for MM-491 traceability.
5. T014-T017 rerun focused validation, fix any remaining regressions, and update planning evidence.
6. T018-T021 complete edge-case coverage, quickstart validation, final unit verification, and `/moonspec-verify` work.

## Parallel Examples

- T005 and T006 can run in parallel because they touch `frontend/src/entrypoints/mission-control.test.tsx` and `frontend/src/utils/executionStatusPillClasses.test.ts` respectively.
- T007 and T008 can run in parallel because they modify different integration test files.
- T010 and T013 can run in parallel after T009 because they modify different files and one is conditional.

## Implementation Strategy

Start from the shared executing shimmer contract already present in the repo and treat MM-491 as a verification-first regression story. Prove readability, bounds, scrollbar isolation, state-matrix coverage, theme intent, reduced-motion semantics, and non-goal preservation with focused CSS/helper and render tests first; only after those tests expose real gaps should the shared Mission Control shimmer styling or entrypoint surfaces be adjusted. Complete the known missing work by adding MM-491 traceability, then finish with quickstart validation, the full unit suite, and `/moonspec-verify`.
