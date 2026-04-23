# Tasks: Calm Shimmer Motion and Reduced-Motion Fallback

**Input**: Design documents from `specs/246-animate-shimmer-motion/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/status-pill-shimmer-motion.md`, `quickstart.md`

## Validation Commands

- Unit tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts`
- Integration tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`
- Full unit suite: `./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Source Traceability Summary

- MM-490: Animate shimmer motion with reduced-motion fallback.
- FR-001 through FR-007: bounded left-to-right sweep, calm cadence, center-brightness emphasis, reduced-motion static fallback, reduced-motion active comprehension, executing-only activation, and MM-490 traceability.
- SCN-001 through SCN-006: bounded sweep travel, cadence/no-overlap timing, center emphasis, reduced-motion static fallback, reduced-motion active comprehension, and executing-only isolation.
- SC-001 through SC-007: measurable proof for bounded travel, 1.6 to 1.8 second cadence, center emphasis, reduced-motion static highlight, reduced-motion active comprehension, non-executing exclusion, and MM-490 traceability.
- DESIGN-REQ-007, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-014: executing-state trigger, motion profile, reduced-motion behavior, and executing-only state matrix.
- Requirement status summary from `plan.md`: 2 `missing`, 7 `partial`, 11 `implemented_unverified`, 4 `implemented_verified`.

## Phase 1: Setup

- [ ] T001 Verify MM-490 planning inputs and target frontend files in `specs/246-animate-shimmer-motion/spec.md`, `specs/246-animate-shimmer-motion/plan.md`, `specs/246-animate-shimmer-motion/research.md`, `frontend/src/styles/mission-control.css`, `frontend/src/utils/executionStatusPillClasses.ts`, `frontend/src/entrypoints/tasks-list.tsx`, and `frontend/src/entrypoints/task-detail.tsx`.
- [ ] T002 Confirm the focused unit and integration validation commands in `specs/246-animate-shimmer-motion/quickstart.md` remain the active test path for MM-490 and require no new package or service setup.

## Phase 2: Foundational

- [ ] T003 Reconcile the MM-490 motion contract and data model in `specs/246-animate-shimmer-motion/contracts/status-pill-shimmer-motion.md` and `specs/246-animate-shimmer-motion/data-model.md` with the current shared Mission Control shimmer seams before story verification begins.
- [ ] T004 Confirm MM-490 stays within the existing shared status-pill helper, Mission Control stylesheet, and list/detail render surfaces in `specs/246-animate-shimmer-motion/plan.md` and `specs/246-animate-shimmer-motion/research.md`, with no new component, route, or infrastructure.

## Phase 3: Story - Calm Executing Shimmer Motion

**Summary**: As a user watching an executing workflow, I want the status-pill shimmer to move with a calm sweep cadence when motion is allowed and become a static highlight when motion is reduced so executing still reads as active without feeling urgent or unstable.

**Independent Test**: Render supported Mission Control executing status pills under normal motion and reduced-motion conditions, then verify the shimmer stays bounded to the pill, follows a calm non-overlapping left-to-right cadence with center-focused emphasis, falls back to a static active highlight when reduced motion is requested, remains executing-only, and preserves MM-490 traceability.

**Traceability IDs**: FR-001 through FR-007; SCN-001 through SCN-006; SC-001 through SC-007; DESIGN-REQ-007, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-014.

### Unit Test Plan

- CSS contract tests verify bounded left-to-right sweep travel, 1.6 to 1.8 second total cadence including idle gap, no overlap between cycles, center-focused emphasis, and reduced-motion static fallback semantics.
- Helper tests verify executing-only activation remains stable and MM-490 traceability is exported alongside adjacent shimmer-story references.

### Integration Test Plan

- Task list entrypoint tests verify executing pills continue to use the shared shimmer contract, reduced-motion conditions still read as active without animation, and non-executing pills stay plain.
- Task detail entrypoint tests verify the same shared shimmer contract, reduced-motion active read, and executing-only behavior on detail surfaces.

### Tests First

- [ ] T005 [P] Add failing CSS contract tests for bounded left-to-right travel, total 1.6 to 1.8 second cadence including idle gap, no-overlap timing, center-focused emphasis, and reduced-motion static fallback semantics in `frontend/src/entrypoints/mission-control.test.tsx` covering FR-001, FR-002, FR-003, FR-004, FR-005, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-007, DESIGN-REQ-010, DESIGN-REQ-012.
- [ ] T006 [P] Add failing helper tests for MM-490 traceability preservation while keeping executing-only selector behavior intact in `frontend/src/utils/executionStatusPillClasses.test.ts` covering FR-006, FR-007, SCN-006, SC-006, SC-007, DESIGN-REQ-014.
- [ ] T007 [P] Add failing task-list integration tests for reduced-motion active comprehension, bounded executing-pill rendering, and non-executing exclusion in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-001, FR-004, FR-005, FR-006, SCN-001, SCN-004, SCN-005, SCN-006, SC-001, SC-004, SC-005, SC-006, DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-014.
- [ ] T008 [P] Add failing task-detail integration tests for reduced-motion active comprehension, bounded executing-pill rendering, and executing-only behavior in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-001, FR-004, FR-005, FR-006, SCN-001, SCN-004, SCN-005, SCN-006, SC-001, SC-004, SC-005, SC-006, DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-014.
- [ ] T009 Run the focused unit and integration validation commands from `specs/246-animate-shimmer-motion/quickstart.md` to confirm T005-T008 fail for the expected MM-490 gaps before production changes.

### Conditional Fallback For Implemented-Unverified Rows

- [ ] T010 If T005 or T009 shows the existing sweep path or reduced-motion fallback semantics are weaker than MM-490 requires, update `frontend/src/styles/mission-control.css` to preserve bounded travel, static fallback clarity, and executing-state comprehension for FR-001, FR-004, FR-005, SCN-001, SCN-004, SCN-005, SC-001, SC-004, SC-005, DESIGN-REQ-007, and DESIGN-REQ-012.
- [ ] T011 If T007-T009 show list/detail surfaces do not preserve the active read or executing-only guardrails under reduced motion, update `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/entrypoints/task-detail.tsx`, and `frontend/src/styles/mission-control.css` for FR-005, FR-006, SCN-005, SCN-006, SC-005, SC-006, and DESIGN-REQ-014.

### Implementation

- [ ] T012 Add the missing MM-490 traceability surface in `frontend/src/utils/executionStatusPillClasses.ts` and `frontend/src/utils/executionStatusPillClasses.test.ts` for FR-007 and SC-007 while preserving adjacent MM-488/MM-489 references.
- [ ] T013 Implement the missing MM-490 timing contract in `frontend/src/styles/mission-control.css` for FR-002, FR-003, SCN-002, SCN-003, SC-002, SC-003, and DESIGN-REQ-010 by refining shimmer tokens and keyframes to encode per-cycle idle gap, no-overlap timing, and center-focused emphasis.
- [ ] T014 Re-run the focused unit validation command from `specs/246-animate-shimmer-motion/quickstart.md`, fix any failing MM-490 CSS/helper assertions in `frontend/src/styles/mission-control.css` and `frontend/src/utils/executionStatusPillClasses.ts`, and confirm the verification-first tasks now pass.
- [ ] T015 Re-run the focused integration validation command from `specs/246-animate-shimmer-motion/quickstart.md`, fix any failing MM-490 render assertions in `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/entrypoints/task-detail.tsx`, and `frontend/src/styles/mission-control.css`, and confirm the story passes end to end on supported surfaces.

### Story Validation

- [ ] T016 Update MM-490 requirement-status evidence in `specs/246-animate-shimmer-motion/plan.md` after T014-T015 so `missing`, `partial`, and `implemented_unverified` rows reflect the final proof.
- [ ] T017 Verify the independent story criteria in `specs/246-animate-shimmer-motion/quickstart.md` and record any remaining MM-490-specific gaps before polish work.

## Final Phase: Polish and Verification

- [ ] T018 Expand edge-case coverage for rapid re-renders, non-overlap cadence guardrails, reduced-motion active comprehension, and executing-only isolation in `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/task-detail.test.tsx` as needed for SCN-002, SCN-005, SCN-006, SC-002, SC-005, SC-006, and DESIGN-REQ-010.
- [ ] T019 Run the quickstart validation steps from `specs/246-animate-shimmer-motion/quickstart.md`.
- [ ] T020 Run `./tools/test_unit.sh` for final unit-test verification.
- [ ] T021 Run `/moonspec-verify` by creating `specs/246-animate-shimmer-motion/verification.md` with MM-490 traceability, DESIGN-REQ coverage, test evidence, and final verdict.

## Dependencies and Execution Order

1. T001-T004 establish the story inputs, scope boundaries, and shared-seam baseline.
2. T005-T009 write verification and failing regression tests first, then confirm they fail before code changes.
3. T010-T011 are conditional fallback tasks for the `implemented_unverified` rows and execute only if the verification-first tasks expose real gaps.
4. T012-T013 complete the known missing and partial implementation work for MM-490 traceability and motion timing behavior.
5. T014-T017 rerun focused validation, fix any remaining regressions, and update planning evidence.
6. T018-T021 complete edge-case coverage, quickstart validation, final unit verification, and `/moonspec-verify` work.

## Parallel Examples

- T005 and T006 can run in parallel because they touch `frontend/src/entrypoints/mission-control.test.tsx` and `frontend/src/utils/executionStatusPillClasses.test.ts` respectively.
- T007 and T008 can run in parallel because they modify different integration test files.
- T012 and T013 can run in parallel after T009 because they modify different implementation files.

## Implementation Strategy

Start from the shared executing shimmer contract already present in the repo and treat MM-490 as a mixed verification-and-implementation refinement story. Prove bounded travel, cadence, center-brightness emphasis, reduced-motion semantics, and executing-only behavior with focused CSS/helper and render tests first; only after those tests expose real gaps should the shared Mission Control shimmer timing contract or entrypoint surfaces be adjusted. Complete the known missing work by adding MM-490 traceability and the missing cadence/center-emphasis behavior, then finish with quickstart validation, the full unit suite, and `/moonspec-verify`.
